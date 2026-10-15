"""API tests against a real Postgres (DATABASE_URL), using the mock LLM provider."""
import os
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

BRD = ("# Orders\nThe system shall allow a customer to place orders for vaccines stored in cold chain.\n"
       "Compliance must receive an audit trail for controlled substance orders.\n")


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_full_flow(client):
    pid = client.post("/api/projects", json={"name": "Test Proj", "platform": "salesforce"}).json()["id"]
    try:
        assert client.post("/api/projects", json={"name": "x", "platform": "nope"}).status_code == 400
        assert client.post("/api/projects/%s/runs" % pid, json={}).status_code == 400  # no docs yet
        r = client.post("/api/projects/%s/documents" % pid, files={"file": ("brd.md", BRD.encode(), "text/markdown")})
        assert r.status_code == 201 and r.json()["indexed_chunks"] >= 1
        assert client.post("/api/projects/%s/documents" % pid, files={"file": ("a.exe", b"x")}).status_code == 400
        hits = client.post("/api/projects/%s/search" % pid, json={"query": "cold chain vaccines"}).json()
        assert isinstance(hits, list)
        assert client.post("/api/projects/%s/runs" % pid, json={"provider": "bogus"}).status_code == 400
        assert client.post("/api/projects/%s/runs" % pid, json={"provider": "mock"}).status_code == 202
        for _ in range(40):
            run = client.get("/api/projects/%s/runs/latest" % pid).json()
            if run["status"] != "running":
                break
            time.sleep(0.25)
        assert run["status"] == "done" and run["story_count"] == 2
        stories = client.get("/api/projects/%s/stories" % pid).json()
        sid = stories[0]["id"]
        p = client.patch("/api/projects/%s/stories/%s" % (pid, sid), json={"status": "approved", "title": "Edited"})
        assert p.json()["status"] == "approved" and p.json()["title"] == "Edited"
        assert client.patch("/api/projects/%s/stories/%s" % (pid, sid), json={"status": "bad"}).status_code == 400
        client.patch("/api/projects/%s/stories/%s" % (pid, stories[1]["id"]), json={"status": "rejected"})
        for fmt in ("csv", "json", "md", "xlsx", "jira-csv", "traceability"):
            e = client.get("/api/projects/%s/export" % pid, params={"format": fmt})
            assert e.status_code == 200 and e.content, fmt
        assert b"Edited" in client.get("/api/projects/%s/export" % pid, params={"format": "csv"}).content
        assert client.get("/api/projects/%s/export" % pid, params={"format": "zip"}).status_code == 400
    finally:
        assert client.delete("/api/projects/%s" % pid).status_code == 204


def test_basic_auth(monkeypatch, client):
    from app import config
    monkeypatch.setattr(config, "BASIC_USER", "u")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "p")
    assert client.get("/api/config").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/config", auth=("u", "p")).status_code == 200
    assert client.get("/api/config", auth=("u", "wrong")).status_code == 401
