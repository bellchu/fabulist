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
        status, body = self.replies.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def chat(content, pin=10, pout=5):
    return 200, {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


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
    assert usage == {"in": 100, "out": 40}
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
    assert usage == {"in": 20, "out": 10}
    assert quality.is_ready(quality.story_issues(out[0], {"REQ-001"}))


def test_generate_stories_fails_cleanly_on_persistent_garbage(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", Fake([chat("nope"), chat("still nope")]))
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
