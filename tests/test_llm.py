"""Exercise the real-provider code paths against a fake HTTP layer."""
import json

import httpx
import pytest

from app import llm, pipeline, quality

OPTS = {"chunk_chars": 5000, "max_chunks_per_run": 50, "rag_top_k": 0, "redact_pii": True,
        "temperature": 0.2, "max_output_tokens": 1000, "timeout_seconds": 5}
OPENAI = {"kind": "openai", "name": "fake", "base_url": "https://llm.test/v1", "model": "m", "api_key": "k"}
ANTHROPIC = {"kind": "anthropic", "name": "fake", "base_url": "https://llm.test", "model": "m", "api_key": "k"}


class Fake:
    """Queue of canned responses; records each request payload."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        reply = self.replies.pop(0)
        status, body = reply[:2]
        return httpx.Response(status, json=body, headers=reply[2] if len(reply) > 2 else None, request=httpx.Request("POST", url))


def chat(content, pin=10, pout=5):
    return 200, {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    llm._cooldowns.clear()  # a rate limit noted by one test must not slow the next


def test_openai_complete_parses_text_and_usage(monkeypatch):
    fake = Fake([chat("hello", 12, 3)])
    monkeypatch.setattr(llm.httpx, "post", fake)
    text, usage = llm.complete(OPENAI, "sys", "user", OPTS)
    assert text == "hello" and usage == {"in": 12, "out": 3}
    call = fake.calls[0]
    assert call["url"] == "https://llm.test/v1/chat/completions" and call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["messages"][0] == {"role": "system", "content": "sys"} and call["json"]["max_tokens"] == 1000


def test_anthropic_complete(monkeypatch):
    fake = Fake([(200, {"content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 7, "output_tokens": 2}})])
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.complete(ANTHROPIC, "s", "u", OPTS) == ("hi", {"in": 7, "out": 2})
    assert fake.calls[0]["url"] == "https://llm.test/v1/messages" and fake.calls[0]["headers"]["x-api-key"] == "k"


def test_retries_transient_errors_but_not_client_errors(monkeypatch):
    fake = Fake([(503, {}), (429, {}), chat("ok")])
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok" and len(fake.calls) == 3
    fake = Fake([(401, {"error": {"message": "invalid api key"}})])
    monkeypatch.setattr(llm.httpx, "post", fake)
    with pytest.raises(llm.LLMError, match="401: invalid api key"):
        llm.complete(OPENAI, "s", "u", OPTS)
    assert len(fake.calls) == 1
    monkeypatch.setattr(llm.config, "LLM_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(llm.httpx, "post", Fake([(500, {})] * 3))
    with pytest.raises(llm.LLMError, match="HTTP 500"):
        llm.complete(OPENAI, "s", "u", OPTS)


def test_test_connection_reports_failure(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", Fake([(404, {"error": {"message": "model not found"}})]))
    r = llm.test_connection(OPENAI, OPTS)
    assert r["ok"] is False and "model not found" in r["error"]


def test_extract_requirements_via_llm_redacts_and_counts_tokens(monkeypatch):
    reply = "```json\n" + json.dumps([
        {"statement": "The system shall email order confirmations to the buyer.", "type": "functional", "actors": ["buyer"], "source_id": "BR-9"},
        {"statement": "x"},  # too short: dropped
        {"statement": "The system shall email order confirmations to the buyer.", "type": "functional"},  # duplicate
    ]) + "\n```"
    fake = Fake([chat(reply, 100, 40)])
    monkeypatch.setattr(llm.httpx, "post", fake)
    usage = {"in": 0, "out": 0}
    doc = {"id": "d", "filename": "brd.md", "text": "# Orders\nContact jane.doe@example.com. Orders shall be confirmed by email."}
    reqs = pipeline.extract_requirements([doc], "sap", OPENAI, OPTS, lambda m: None, usage)
    assert len(reqs) == 1 and reqs[0]["source_id"] == "BR-9" and reqs[0]["epic"] == "Orders"
    assert usage == {"in": 100, "out": 40, "requests": 1}  # tokens as reported by the provider, and the one request that made them
    prompt = fake.calls[0]["json"]["messages"][1]["content"]
    assert "jane.doe@example.com" not in prompt and "[EMAIL]" in prompt and "SAP S/4HANA" in prompt


def test_generate_stories_retries_invalid_json_and_filters_links(monkeypatch):
    stories = [{"title": "Email confirmations", "persona": "buyer", "narrative": "As a buyer, I want confirmations, so that I know.",
                "acceptance_criteria": ["Given an order, when it is placed, then an email is sent."], "requirement_ids": ["REQ-001", "REQ-999"],
                "story_points": 3, "priority": "High"}]
    fake = Fake([chat("Sure! Here are the stories."), chat(json.dumps(stories))])
    monkeypatch.setattr(llm.httpx, "post", fake)
    reqs = [{"key": "REQ-001", "type": "functional", "statement": "The system shall email confirmations.", "epic": "Orders", "section": "Notify"}]
    usage = {"in": 0, "out": 0}
    out = pipeline.generate_stories(reqs, "p", "salesforce", OPENAI, OPTS, lambda m: None, usage)
    assert len(fake.calls) == 2 and "not valid JSON" in fake.calls[1]["json"]["messages"][1]["content"]
    assert out[0]["requirement_ids"] == ["REQ-001"] and out[0]["epic"] == "Orders" and out[0]["feature"] == "Notify"
    assert usage == {"in": 20, "out": 10, "requests": 2}  # the invalid-JSON retry is a second provider request
    assert quality.is_ready(quality.story_issues(out[0], {"REQ-001"}))


def test_generate_stories_fails_cleanly_on_persistent_garbage(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", Fake([chat("nope"), chat("still nope"), chat("never")]))  # three tries in all
    reqs = [{"key": "REQ-001", "type": "functional", "statement": "The system shall x things.", "epic": "E", "section": "S"}]
    with pytest.raises(ValueError, match="did not return valid JSON"):
        pipeline.generate_stories(reqs, "p", "generic", OPENAI, OPTS, lambda m: None, {"in": 0, "out": 0})


def test_refine_story_via_llm_constrains_links(monkeypatch):
    improved = [{"title": "Cancel open orders", "persona": "buyer", "narrative": "As a buyer, I want to cancel open orders, so that I avoid unwanted deliveries.",
                 "acceptance_criteria": ["Given an open order, when I cancel it, then its status is Cancelled within 5 seconds."],
                 "requirement_ids": ["REQ-001", "REQ-777"]}]
    fake = Fake([chat(json.dumps(improved))])
    monkeypatch.setattr(llm.httpx, "post", fake)
    story = {"title": "Cancel", "persona": "user", "narrative": "x", "acceptance_criteria": [], "requirement_ids": ["REQ-001"], "epic": "E", "feature": "F"}
    reqs = [{"key": "REQ-001", "type": "functional", "statement": "The system shall let buyers cancel open orders.", "actors": ["buyer"]}]
    out = pipeline.refine_story(story, reqs, [{"message": "No acceptance criteria"}], "generic", OPENAI, OPTS, {"in": 0, "out": 0})
    assert out["requirement_ids"] == ["REQ-001"] and out["epic"] == "E" and out["persona"] == "buyer"
    prompt = fake.calls[0]["json"]["messages"][1]["content"]
    assert "No acceptance criteria" in prompt and "REQ-001 [functional]" in prompt


def test_list_models_openai_and_anthropic(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append((url, headers))
        req = httpx.Request("GET", url)
        if "anthropic" in url:
            return httpx.Response(200, json={"data": [{"id": "claude-b", "display_name": "Claude B"}, {"id": "claude-a"}]}, request=req)
        if "bad" in url:
            return httpx.Response(401, json={"error": {"message": "invalid key"}}, request=req)
        return httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "text-embedding-y"}, {"nope": 1}]}, request=req)
    monkeypatch.setattr(llm.httpx, "get", fake_get)
    assert llm.list_models("openai", "https://api.example.com/v1/", "k") == [{"id": "gpt-x", "label": "gpt-x"}, {"id": "text-embedding-y", "label": "text-embedding-y"}]
    assert seen[-1] == ("https://api.example.com/v1/models", {"Authorization": "Bearer k"})
    assert [m["id"] for m in llm.list_models("anthropic", "https://api.anthropic.com", "k")] == ["claude-a", "claude-b"]
    assert seen[-1][1]["x-api-key"] == "k"
    with pytest.raises(llm.LLMError, match="invalid key"):
        llm.list_models("openai", "https://bad.example/v1", "k")
    assert llm.list_models("mock", "", "") == []


def test_answer_question_via_llm_uses_numbered_passages(monkeypatch):
    fake = Fake([chat("Customers have 30 days [1].")])
    monkeypatch.setattr(llm.httpx, "post", fake)
    passages = [{"filename": "r.md", "text": "Returns within 30 days. Contact ops@example.com."}]
    out = pipeline.answer_question("How long for returns?", passages, OPENAI, OPTS, {"in": 0, "out": 0})
    prompt = fake.calls[0]["json"]["messages"][1]["content"]
    assert out == "Customers have 30 days [1]." and "[1] (r.md)" in prompt and "ops@example.com" not in prompt


def test_estimate_via_llm_filters_invalid(monkeypatch):
    reply = json.dumps([{"key": "US-001", "points": 5, "rationale": "integration"}, {"key": "US-001", "points": 4},
                        {"key": "US-999", "points": 3}, {"key": "US-002", "points": "x"}])
    monkeypatch.setattr(llm.httpx, "post", Fake([chat(reply)]))
    stories = [{"key": "US-001", "title": "A", "acceptance_criteria": []}, {"key": "US-002", "title": "B", "acceptance_criteria": []}]
    assert pipeline.estimate_stories(stories, OPENAI, OPTS, {"in": 0, "out": 0}) == [{"key": "US-001", "points": 5, "rationale": "integration"}]


def test_out_of_quota_is_not_retried_and_keeps_the_providers_explanation(monkeypatch):
    fake = Fake([(429, {"error": {"message": "You exceeded your current quota, please check your plan and billing details.", "type": "insufficient_quota"}})] * 3)
    monkeypatch.setattr(llm.httpx, "post", fake)
    with pytest.raises(llm.LLMError) as err:
        llm.complete(OPENAI, "s", "u", OPTS)
    assert len(fake.calls) == 1, "running out of quota is not transient"
    assert "exceeded your current quota" in str(err.value) and "out of quota" in str(err.value)


def test_a_failure_after_retries_still_says_what_the_provider_said(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.config, "LLM_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(llm.httpx, "post", Fake([(503, {"error": {"message": "The server is overloaded"}})] * 3))
    with pytest.raises(llm.LLMError, match="HTTP 503: The server is overloaded"):
        llm.complete(OPENAI, "s", "u", OPTS)


@pytest.mark.parametrize("status,hint", [(401, "API key"), (403, "access"), (404, "model name")])
def test_authentication_and_model_errors_say_what_to_check(monkeypatch, status, hint):
    monkeypatch.setattr(llm.httpx, "post", Fake([(status, {"error": {"message": "nope"}})]))
    with pytest.raises(llm.LLMError, match=hint):
        llm.complete(OPENAI, "s", "u", OPTS)


def test_an_unreachable_provider_names_the_host_and_what_to_check(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def refuse(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(llm.httpx, "post", refuse)
    with pytest.raises(llm.LLMError) as err:
        llm.complete(OPENAI, "s", "u", OPTS)
    assert "llm.test" in str(err.value) and "endpoint URL" in str(err.value) and "ConnectError" in str(err.value)


def test_cancelling_stops_the_retry_loop_after_the_attempt_in_flight(monkeypatch):
    class Cancelled(Exception):
        pass
    state = {"checks": 0}

    def check():
        state["checks"] += 1
        if state["checks"] > 1:  # the first check (before attempt 1) passes; the user cancels while that attempt hangs
            raise Cancelled()
    calls = []

    def hang(url, headers=None, json=None, timeout=None):
        calls.append(1)
        raise httpx.ReadTimeout("no answer")
    monkeypatch.setattr(llm.httpx, "post", hang)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    llm.set_cancel_check(check)
    try:
        with pytest.raises(Cancelled):
            llm.complete(OPENAI, "s", "u", OPTS)
    finally:
        llm.set_cancel_check(None)
    assert len(calls) == 1, "no second or third attempt after cancelling (each can take a full timeout)"


def test_without_a_cancel_check_nothing_changes(monkeypatch):
    llm.set_cancel_check(None)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    fake = Fake([(503, {}), chat("ok")])
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok" and len(fake.calls) == 2


def test_interactive_ai_calls_are_limited_and_refused_fast_when_the_limit_is_reached(monkeypatch):
    import threading
    from app import config
    monkeypatch.setattr(config, "MAX_INTERACTIVE_AI_CALLS", 1)
    llm._reset_interactive_slots()
    entered, release = threading.Event(), threading.Event()

    def slow(url, headers=None, json=None, timeout=None):
        entered.set()
        release.wait(10)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", slow)
    first = threading.Thread(target=lambda: llm.complete(OPENAI, "s", "u", OPTS))
    first.start()
    assert entered.wait(5)
    try:
        with pytest.raises(llm.AIBusy):
            llm.complete(OPENAI, "s", "u", OPTS)  # refused at once, it does not wait for the slot (that would hold a request thread)
        # a background run is bounded by its own pool and must never be refused
        result = []
        run = threading.Thread(target=lambda: (llm.set_cancel_check(lambda: None), result.append(llm.complete(OPENAI, "s", "u", OPTS)[0])))
        run.start()
        release.set()
        run.join(10)
        assert result == ["ok"]
    finally:
        release.set()
        first.join(10)
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok", "the slot is released when a call finishes"


# ---- patient retries ------------------------------------------------------------------------------------------------------
# A large run makes hundreds of requests, so even a modest failure rate (rate limits, overloaded servers) means some request fails
# several times in a row. Three quick attempts failed whole runs; the provider's own Retry-After is the right wait.

def test_a_rate_limit_is_waited_out_for_as_long_as_the_provider_asks(monkeypatch):
    waits = []
    monkeypatch.setattr(llm, "_sleep", lambda s: waits.append(s))
    fake = Fake([(429, {"error": {"message": "Rate limit reached"}}, {"Retry-After": "7"}), chat("ok")])
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok"
    assert 7 <= waits[0] <= 8.5, "waited what Retry-After said (plus a little jitter): %s" % waits


def test_many_quick_failures_in_a_row_are_survived(monkeypatch):
    waits = []
    monkeypatch.setattr(llm, "_sleep", lambda s: waits.append(s))
    fake = Fake([(500, {})] * 5 + [chat("finally")])
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "finally" and len(fake.calls) == 6
    assert waits == sorted(waits) and waits[-1] > waits[0] * 4, "the waits grow: %s" % waits


def test_the_waits_are_bounded_and_the_last_failure_is_reported(monkeypatch):
    waits = []
    monkeypatch.setattr(llm, "_sleep", lambda s: waits.append(s))
    monkeypatch.setattr(llm.httpx, "post", Fake([(503, {"error": {"message": "overloaded"}}, {"Retry-After": "3600"})] * 6))
    with pytest.raises(llm.LLMError, match="HTTP 503: overloaded"):
        llm.complete(OPENAI, "s", "u", OPTS)
    assert max(waits) <= llm.MAX_WAIT_SECONDS + 1.5, "an absurd Retry-After is capped (%s)" % waits


def test_a_rate_limit_makes_every_call_to_that_provider_wait_not_just_the_one_that_hit_it(monkeypatch):
    events = []
    monkeypatch.setattr(llm, "_sleep", lambda s: events.append(("sleep", round(s))))
    post = Fake([chat("ok")])

    def logged(*a, **k):
        events.append(("post",))
        return post(*a, **k)
    monkeypatch.setattr(llm.httpx, "post", logged)
    llm._note_rate_limit("llm.test", 5)  # another section in the same run, or another run, was just told to slow down
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok"
    assert events[0][0] == "sleep" and 4 <= events[0][1] <= 5 and events[1] == ("post",), "this call waited out the cooldown before sending anything: %s" % events
    events.clear()
    llm._cooldowns.clear()  # the cooldown is over
    post.replies.append(chat("ok"))
    assert llm.complete(OPENAI, "s", "u", OPTS)[0] == "ok"
    assert events == [("post",)], "and once the cooldown is over, calls go straight out"


def test_a_reply_that_is_not_json_is_asked_again_twice_before_the_run_gives_up(monkeypatch):
    junk = chat("I am sorry, I cannot help with that.")
    ok = chat('[{"title": "A story", "narrative": "As a buyer, I want x, so that y.", "acceptance_criteria": ["Given a, when b, then c."], "requirement_ids": ["REQ-001"]}]')
    usage = {"in": 0, "out": 0}
    fake = Fake([junk, junk, ok])
    monkeypatch.setattr(llm.httpx, "post", fake)
    out = pipeline._ask(OPENAI, OPTS, "sys", "prompt", usage)
    assert len(out) == 1 and len(fake.calls) == 3 and usage["requests"] == 3, "the third reply was usable: %s calls" % len(fake.calls)
    assert "not valid JSON" in fake.calls[1]["json"]["messages"][-1]["content"], "and the model was told what was wrong"
    monkeypatch.setattr(llm.httpx, "post", Fake([junk, junk, junk]))
    with pytest.raises(ValueError, match="did not return valid JSON"):
        pipeline._ask(OPENAI, OPTS, "sys", "prompt", {"in": 0, "out": 0})
