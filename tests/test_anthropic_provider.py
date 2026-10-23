"""The Anthropic provider kind (Messages API): the request it sends, the answer it understands, the mistakes it explains. Simulated with a local server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import llm

SEEN = []
MODE = {"shape": "ok"}


class Fake(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        out = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "key": self.headers.get("x-api-key"), "version": self.headers.get("anthropic-version"), "body": body})
        if MODE["shape"] == "broken":
            return self._send(200, {"unexpected": True})
        if MODE["shape"] == "denied":
            return self._send(401, {"error": {"message": "invalid x-api-key"}})
        reqs = [{"statement": "The system shall export invoices to CSV on request.", "type": "functional", "actors": [], "source_id": "BR-1", "rationale": ""}]
        self._send(200, {"content": [{"type": "text", "text": json.dumps(reqs)[:20]}, {"type": "text", "text": json.dumps(reqs)[20:]}], "usage": {"input_tokens": 120, "output_tokens": 30}})

    def do_GET(self):
        SEEN.append({"path": self.path, "key": self.headers.get("x-api-key")})
        self._send(200, {"data": [{"id": "claude-b", "display_name": "Claude B"}, {"id": "claude-a"}]})

    def log_message(self, *a):
        pass


@pytest.fixture()
def anthropic():
    SEEN.clear()
    MODE["shape"] = "ok"
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", 9190), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"kind": "anthropic", "name": "Claude", "base_url": "http://127.0.0.1:9190", "model": "claude-x", "api_key": "sk-ant-test"}
    server.shutdown()
    server.server_close()


OPTS = {"temperature": 0.2, "max_output_tokens": 500, "timeout_seconds": 20}


def test_the_request_has_the_anthropic_shape_and_the_answer_is_joined_and_counted(anthropic):
    text, usage = llm.complete(anthropic, "Be a business analyst.", "Extract requirements.", OPTS)
    sent = SEEN[0]
    assert sent["path"] == "/v1/messages" and sent["key"] == "sk-ant-test" and sent["version"] == "2023-06-01"
    assert sent["body"]["model"] == "claude-x" and sent["body"]["system"] == "Be a business analyst." and sent["body"]["max_tokens"] == 500
    assert sent["body"]["messages"] == [{"role": "user", "content": "Extract requirements."}]
    assert json.loads(text)[0]["source_id"] == "BR-1", "the text blocks are joined"
    assert usage == {"in": 120, "out": 30}


def test_extraction_works_through_it(anthropic):
    from app import pipeline, settings
    items = pipeline.extract_requirements([{"id": "d", "filename": "b.md", "text": "# A\nBR-1: The system shall export invoices to CSV on request.\n"}], "generic",
                                          anthropic, dict(settings.DEFAULTS), lambda m: None, {"in": 0, "out": 0})
    assert items and items[0]["source_id"] == "BR-1"


def test_a_missing_key_a_refusal_and_a_broken_answer_are_explained(anthropic):
    with pytest.raises(llm.LLMError, match="No API key"):
        llm.complete(dict(anthropic, api_key=""), "s", "u", OPTS)
    MODE["shape"] = "denied"
    with pytest.raises(llm.LLMError, match="401|invalid x-api-key"):
        llm.complete(anthropic, "s", "u", OPTS)
    MODE["shape"] = "broken"
    with pytest.raises(llm.LLMError, match="Unexpected provider response shape"):
        llm.complete(anthropic, "s", "u", OPTS)


def test_the_models_are_listed_and_the_connection_test_reports(anthropic):
    models = llm.list_models("anthropic", anthropic["base_url"], anthropic["api_key"])
    assert [m["id"] for m in models] == ["claude-a", "claude-b"] and models[1]["label"] == "Claude B"
    assert SEEN[-1]["path"].startswith("/v1/models") and SEEN[-1]["key"] == "sk-ant-test"
    ok = llm.test_connection(anthropic, OPTS)
    assert ok["ok"] is True and ok["reply"]
    MODE["shape"] = "denied"
    bad = llm.test_connection(anthropic, OPTS)
    assert bad["ok"] is False and bad["error"]
