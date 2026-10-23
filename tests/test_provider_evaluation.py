"""Before relying on an AI provider, an administrator can see how it does on a BRD whose answer is known: the built-in sample has twelve numbered requirements."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _provider(client, name="Offline heuristic"):
    return next(p for p in client.get("/api/admin/providers").json()["providers"] if p["name"] == name)


def test_the_offline_engine_is_scored_on_the_sample(client):
    r = client.post("/api/admin/providers/%s/evaluate" % _provider(client)["id"])
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["expected"] == 12 and e["found"] == 12 and e["missing"] == []
    assert e["kept_tbd"] is True, "an open item (BR-303 'rules TBD') must stay open, never be resolved or dropped"
    assert e["items"] >= 12 and e["seconds"] >= 0 and e["requests"] >= 0
    assert "12 of 12" in e["verdict"]


class _Half(BaseHTTPRequestHandler):
    """A model that only finds the first BR of each section and 'resolves' the TBD."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        import re
        first = re.findall(r"(BR-\d+): ([^\n]+)", prompt)[:1]
        items = [{"statement": s.replace("rules TBD", "rules are defined"), "type": "functional", "actors": [], "source_id": i, "rationale": ""} for i, s in first]
        out = json.dumps({"choices": [{"message": {"content": json.dumps(items)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_a_weaker_model_scores_lower_and_the_gaps_are_named(client):
    server = ThreadingHTTPServer(("127.0.0.1", 9187), _Half)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        pid = client.post("/api/admin/providers", json={"name": "Half model", "kind": "openai", "base_url": "http://127.0.0.1:9187/v1", "model": "m", "api_key": "k"}).json()["id"]
        e = client.post("/api/admin/providers/%s/evaluate" % pid).json()
    finally:
        server.shutdown()
    assert 0 < e["found"] < 12 and e["found"] + len(e["missing"]) == 12 and all(m.startswith("BR-") for m in e["missing"])
    assert e["tokens_in"] > 0 and e["tokens_out"] > 0 and e["requests"] >= 1
    assert "of 12" in e["verdict"]


def test_it_is_recorded_and_for_administrators_only(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "ana", "role": "analyst", "password": "ana-password-12"}, auth=root)
    pid = client.get("/api/admin/providers", auth=root).json()["providers"][0]["id"]
    assert client.post("/api/admin/providers/%s/evaluate" % pid, auth=("ana", "ana-password-12")).status_code == 403
    assert client.post("/api/admin/providers/%s/evaluate" % pid, auth=root).status_code == 200
    rows = [x for x in client.get("/api/admin/audit", auth=root).json() if x["action"] == "provider evaluated"]
    assert len(rows) == 1 and "of 12" in rows[0]["summary"]


def test_an_unknown_provider_is_404ish_and_an_unreachable_one_says_so(client):
    assert client.post("/api/admin/providers/nope/evaluate").status_code in (400, 404)
    pid = client.post("/api/admin/providers", json={"name": "Dead", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": "k"}).json()["id"]
    r = client.post("/api/admin/providers/%s/evaluate" % pid)
    assert r.status_code == 200 and r.json()["error"] and r.json()["found"] == 0
