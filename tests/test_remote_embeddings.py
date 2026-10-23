"""A remote embeddings provider (any OpenAI-compatible /embeddings endpoint): batched, retried when it hiccups, and its mistakes are explained."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import config, db, embeddings, settings


class Fake(BaseHTTPRequestHandler):
    requests = []
    fail_first = 0
    dim = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.requests.append(body)
        if Fake.fail_first > 0:
            Fake.fail_first -= 1
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        dim = Fake.dim or config.EMBED_DIM
        data = [{"index": i, "embedding": [1.0 if j == (len(t) % dim) else 0.0 for j in range(dim)]} for i, t in enumerate(body["input"])][::-1]  # out of order on purpose
        out = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture()
def remote(client):
    Fake.requests, Fake.fail_first, Fake.dim = [], 0, None
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", 9189), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pid = client.post("/api/admin/providers", json={"name": "Embedder", "kind": "openai", "base_url": "http://127.0.0.1:9189/v1", "model": "m", "api_key": "k"}).json()["id"]
    client.put("/api/admin/settings", json={"embed_provider_id": pid, "embed_model": "text-embed-x"})
    yield pid
    server.shutdown()
    server.server_close()


def test_the_configured_model_and_texts_are_sent_in_batches_and_order_is_restored(remote):
    texts = ["t" * n for n in range(1, 131)]
    vectors = embeddings.embed(texts)
    assert [len(r["input"]) for r in Fake.requests] == [64, 64, 2], "64 texts per request"
    assert all(r["model"] == "text-embed-x" for r in Fake.requests)
    assert [v.index(1.0) for v in vectors] == [n % config.EMBED_DIM for n in range(1, 131)], "answers come back in the order of the texts, however the provider ordered them"


def test_a_hiccup_is_retried_and_the_document_still_gets_indexed(client, remote):
    Fake.fail_first = 2
    pid = client.post("/api/projects", json={"name": "Remote", "platform": "generic"}).json()["id"]
    doc = client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"}).json()
    assert doc["indexed_chunks"] == 1, "two 503s, then success"
    assert len(Fake.requests) == 3
    assert db.one("SELECT count(*) AS n FROM chunks WHERE project_id=%s", (pid,))["n"] == 1


def test_a_provider_that_keeps_failing_gives_up_with_a_reason(remote):
    Fake.fail_first = 99
    with pytest.raises(RuntimeError, match="embeddings"):
        embeddings.embed(["one text"])
    assert len(Fake.requests) == 3, "three attempts, not endless"


def test_the_wrong_vector_size_is_explained(remote):
    Fake.dim = config.EMBED_DIM + 7
    with pytest.raises(RuntimeError, match="EMBED_DIM"):
        embeddings.embed(["one text"])


def test_a_missing_model_is_explained(client, remote):
    client.put("/api/admin/settings", json={"embed_model": ""})
    with pytest.raises(RuntimeError, match="model"):
        embeddings.embed(["x"])
    assert settings.get_settings()["embed_provider_id"] == remote


def test_a_document_that_could_not_be_indexed_is_stored_and_says_so(client, remote):
    Fake.fail_first = 99
    pid = client.post("/api/projects", json={"name": "Unindexed", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"})
    assert r.status_code == 201
    body = r.json()
    assert body["indexed_chunks"] == 0 and "after 3 attempts" in body["index_error"]
    assert [d["filename"] for d in client.get("/api/projects/%s/documents" % pid).json()] == ["b.md"], "the document itself is safe"
    Fake.fail_first = 0
    assert client.post("/api/admin/reindex").status_code == 202
