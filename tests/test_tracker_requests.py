"""The calls to trackers (Jira, Azure DevOps, GitHub, Confluence): retried when they hiccup, final when they refuse, and their error messages read well."""
import httpx
import pytest

from app import integrations


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text
        self.content = b"x" if payload is not None or text else b""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture()
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(integrations.time, "sleep", lambda s: seen.append(("sleep", s)))
    return seen


def _script(monkeypatch, calls, answers):
    answers = list(answers)

    def fake(method, url, **kw):
        calls.append((method, url))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(integrations.httpx, "request", fake)


def test_a_rate_limit_then_success_is_retried_with_a_back_off(monkeypatch, calls):
    _script(monkeypatch, calls, [Resp(429, {}), Resp(503, {}), Resp(200, {"ok": 1})])
    assert integrations._request("GET", "https://tracker.example/x", {}) == {"ok": 1}
    assert [c for c in calls if c[0] == "sleep"] == [("sleep", 1.5), ("sleep", 3.0)], "1.5 s, then 3 s"


def test_three_server_errors_give_up_with_the_reason(monkeypatch, calls):
    _script(monkeypatch, calls, [Resp(500, {}), Resp(500, {}), Resp(502, {})])
    with pytest.raises(integrations.TrackerError, match="HTTP 502"):
        integrations._request("GET", "https://tracker.example/x", {})
    assert len([c for c in calls if c[0] == "GET"]) == 3


def test_network_trouble_is_retried_and_then_named(monkeypatch, calls):
    _script(monkeypatch, calls, [httpx.ConnectError("down")] * 3)
    with pytest.raises(integrations.TrackerError, match="ConnectError contacting tracker"):
        integrations._request("GET", "https://tracker.example/x", {})


def test_a_refusal_is_final_and_explained(monkeypatch, calls):
    _script(monkeypatch, calls, [Resp(400, {"errorMessages": ["Epic Name is required"], "errors": {"customfield_1": "bad value"}})])
    with pytest.raises(integrations.TrackerError) as err:
        integrations._request("POST", "https://tracker.example/x", {}, {"a": 1})
    assert "Epic Name is required" in str(err.value) and "customfield_1: bad value" in str(err.value)
    assert len([c for c in calls if c[0] == "POST"]) == 1, "no second attempt at something that will not change"


def test_an_empty_success_is_an_empty_answer(monkeypatch, calls):
    _script(monkeypatch, calls, [Resp(204)])
    assert integrations._request("DELETE", "https://tracker.example/x", {}) == {}


def test_error_texts_for_every_shape_a_tracker_answers_with():
    assert integrations._error_text(Resp(400, {"message": "Bad credentials"})) == "Bad credentials"
    assert integrations._error_text(Resp(400, ["a", "list"])) == "['a', 'list']"
    assert integrations._error_text(Resp(502, None, "<html>Bad gateway</html>")) == "<html>Bad gateway</html>"
    assert integrations._error_text(Resp(400, {"errorMessages": ["one", "two"]})) == "one; two"
