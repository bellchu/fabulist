"""A run asks the AI about several sections at once (they are independent), merges the answers back in section order, and the server as a
whole never has more than MAX_RUN_AI_CALLS background AI calls in flight."""
import json
import threading
import time

import pytest

from app import config, llm, pipeline

PROFILE = {"kind": "openai", "model": "m", "name": "Fake", "base_url": "", "api_key": "k", "id": "x"}


def _opts(**more):
    return dict({"chunk_chars": 1000, "max_chunks_per_run": 100, "redact_pii": False, "ai_concurrency": 3, "temperature": 0.2, "timeout_seconds": 30, "max_output_tokens": 800}, **more)


def _docs(n):
    return [{"id": "d1", "filename": "brd.md", "text": "\n\n".join("# Area %02d\nThe system shall do thing number %d for the buyer." % (i, i) for i in range(n))}]


class Provider:
    """Stands in for the provider call: records how many calls overlap, and answers from the section named in the prompt."""
    def __init__(self, delay=0.05, fail_on=None):
        self.delay, self.fail_on, self.lock = delay, fail_on, threading.Lock()
        self.now = self.peak = self.calls = 0

    def __call__(self, profile, system, prompt, opts):
        area = int(prompt.split("# Area ")[1][:2]) if "# Area " in prompt else int(prompt.split("Area ")[1][:2])
        with self.lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)
        try:
            time.sleep(self.delay)
            if self.fail_on == area:
                raise llm.LLMError("the provider refused")
            reply = [{"statement": "The system shall handle area %02d thoroughly and record it." % area, "type": "functional"}]
            return json.dumps(reply), {"in": 10, "out": 5}
        finally:
            with self.lock:
                self.now -= 1


def _run(docs, provider, monkeypatch, opts=None, progress=None):
    monkeypatch.setattr(llm, "_complete", provider)
    usage, messages = {"in": 0, "out": 0}, []
    llm.set_cancel_check(lambda: None)  # a background run (the interactive-call cap does not apply)
    try:
        items = pipeline.extract_requirements(docs, "generic", PROFILE, opts or _opts(), progress or messages.append, usage)
    finally:
        llm.set_cancel_check(None)
    return items, usage, messages


def test_sections_are_asked_in_parallel_up_to_the_setting_and_kept_in_section_order(monkeypatch):
    provider = Provider()
    items, usage, messages = _run(_docs(12), provider, monkeypatch)
    assert provider.peak == 3, "three sections in flight at once"
    assert [i["statement"][:26] for i in items] == ["The system shall handle ar"] * 12
    assert [int(i["statement"].split("area ")[1][:2]) for i in items] == list(range(12)), "merged in section order, whatever order the answers arrived in"
    assert usage["requests"] == 12 and usage["in"] == 120 and usage["out"] == 60, "usage is counted exactly once per request: %s" % usage
    assert [m.split(":")[0] for m in messages] == ["Reading section %d of 12" % i for i in range(1, 13)], messages


def test_a_concurrency_of_one_is_the_old_sequential_behaviour(monkeypatch):
    provider = Provider(delay=0.01)
    _run(_docs(6), provider, monkeypatch, _opts(ai_concurrency=1))
    assert provider.peak == 1


def test_parallel_is_faster_than_sequential(monkeypatch):
    t = time.time()
    _run(_docs(12), Provider(delay=0.1), monkeypatch, _opts(ai_concurrency=1))
    sequential = time.time() - t
    t = time.time()
    _run(_docs(12), Provider(delay=0.1), monkeypatch, _opts(ai_concurrency=4))
    assert time.time() - t < sequential * 0.5, "4 at a time should take about a quarter of the time"


def test_a_failing_section_fails_the_run_naming_the_section_and_stops_asking(monkeypatch):
    provider = Provider(delay=0.02, fail_on=2)
    with pytest.raises(llm.LLMError, match=r"Section 3 of 30.*Nothing was saved"):
        _run(_docs(30), provider, monkeypatch, _opts(ai_concurrency=2))
    time.sleep(0.3)
    assert provider.calls < 30, "work not yet started is dropped after a failure (%d of 30 asked)" % provider.calls


def test_cancelling_stops_the_remaining_sections(monkeypatch):
    provider = Provider(delay=0.02)
    seen = []

    def progress(msg):
        seen.append(msg)
        if len(seen) == 4:
            raise KeyboardInterrupt("cancelled")  # what RunCancelled stands for here: any exception from the progress hook
    with pytest.raises(KeyboardInterrupt):
        _run(_docs(40), provider, monkeypatch, _opts(ai_concurrency=2), progress)
    time.sleep(0.3)
    assert provider.calls < 40, "the rest were never asked (%d of 40)" % provider.calls


def test_the_server_wide_cap_on_background_ai_calls_holds_across_runs(monkeypatch):
    monkeypatch.setattr(config, "MAX_RUN_AI_CALLS", 2)
    llm._reset_run_slots()
    provider = Provider(delay=0.05)
    monkeypatch.setattr(llm, "_complete", provider)
    outcome = []

    def one_run():
        llm.set_cancel_check(lambda: None)
        try:
            pipeline.extract_requirements(_docs(8), "generic", PROFILE, _opts(ai_concurrency=4), lambda m: None, {"in": 0, "out": 0})
            outcome.append("ok")
        finally:
            llm.set_cancel_check(None)
    threads = [threading.Thread(target=one_run) for _ in range(3)]  # three runs x four sections in flight each would be twelve
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert outcome == ["ok"] * 3
    assert provider.peak == 2, "never more than MAX_RUN_AI_CALLS calls in flight, whatever the number of runs (%d)" % provider.peak
