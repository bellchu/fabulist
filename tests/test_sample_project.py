"""'Try a sample project': a new project holding a small BRD, so someone evaluating the app can see the workflow without a document of their own."""
import time


def test_a_sample_project_comes_with_a_brd_ready_to_extract(client):
    r = client.post("/api/projects/sample")
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    project = client.get("/api/projects/%s" % pid).json()
    assert project["name"].startswith("Sample: ") and project["documents"] == 1
    docs = client.get("/api/projects/%s/documents" % pid).json()
    assert docs[0]["filename"] == "order-to-cash-brd.md" and docs[0]["chars"] > 1000
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 202
    for _ in range(60):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            break
        time.sleep(0.2)
    assert client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] == "done"
    assert len(client.get("/api/projects/%s/requirements" % pid).json()) >= 8, "the offline engine reads the sample into real requirements"


def test_asking_again_makes_another_with_its_own_name(client):
    a = client.post("/api/projects/sample").json()["name"]
    b = client.post("/api/projects/sample").json()["name"]
    assert a != b and b.startswith("Sample: ")
    assert [p["name"] for p in client.get("/api/projects").json()].count(a) == 1


def test_viewers_cannot_make_one_and_it_is_in_the_api_reference(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "vic", "role": "viewer", "password": "vic-password-12"}, auth=root)
    assert client.post("/api/projects/sample", auth=("vic", "vic-password-12")).status_code == 403
    assert "/api/projects/sample" in client.get("/openapi.json", auth=root).json()["paths"]
