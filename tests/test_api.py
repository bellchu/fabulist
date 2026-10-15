"""API tests against a disposable Postgres database (TEST_DATABASE_URL)."""
import time

BRD = ("# Ordering\n## Vaccines\nThe system shall allow a customer to place orders for vaccines stored in cold chain.\n"
       "The system must confirm orders within 3 seconds.\n## Controls\nCompliance must receive an audit trail for controlled substance orders.\n")


def _wait(client, pid):
    for _ in range(60):
        runs = client.get("/api/projects/%s/runs?limit=1" % pid).json()
        if runs and runs[0]["status"] != "running":
            return runs[0]
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_full_workflow(client):
    pid = client.post("/api/projects", json={"name": "Test Proj", "platform": "salesforce"}).json()["id"]
    assert client.post("/api/projects", json={"name": "x", "platform": "nope"}).status_code == 400
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 400  # no docs
    r = client.post("/api/projects/%s/documents" % pid, files={"file": ("brd.md", BRD.encode(), "text/markdown")})
    assert r.status_code == 201 and r.json()["indexed_chunks"] >= 1
    assert client.post("/api/projects/%s/documents" % pid, files={"file": ("a.exe", b"x")}).status_code == 400
    assert isinstance(client.post("/api/projects/%s/search" % pid, json={"query": "cold chain"}).json(), list)

    # stage 1: requirements, then review
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "bogus"}).status_code == 400
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 202
    run = _wait(client, pid)
    assert run["status"] == "done" and run["item_count"] == 3
    reqs = client.get("/api/projects/%s/requirements" % pid).json()
    assert [r["key"] for r in reqs] == ["REQ-001", "REQ-002", "REQ-003"] and "quality_score" in reqs[0]
    ids = [r["id"] for r in reqs]
    assert client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": ids[:2], "status": "approved"}).json()["updated"] == 2
    upd = client.patch("/api/projects/%s/requirements/%s" % (pid, ids[2]), json={"statement": "Compliance must view an audit trail of controlled substance orders.", "status": "approved"}).json()
    assert upd["status"] == "approved" and upd["statement"].startswith("Compliance must view")
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, ids[2]), json={"status": "nope"}).status_code == 400
    manual = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall email order confirmations.", "type": "functional"}).json()
    assert manual["key"] == "REQ-004" and manual["status"] == "draft"

    # stage 2: stories from approved only
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "stories", "scope": "approved"}).status_code == 202
    assert _wait(client, pid)["status"] == "done"
    stories = client.get("/api/projects/%s/stories" % pid).json()
    linked = {k for s in stories for k in s["requirement_ids"]}
    assert linked == {"REQ-001", "REQ-002", "REQ-003"}
    assert all({"quality_score", "ready", "issues", "key"} <= set(s) for s in stories)
    trace = client.get("/api/projects/%s/traceability" % pid).json()
    assert trace["total"] == 4 and trace["covered"] == 3 and trace["coverage"] == 75

    s = client.patch("/api/projects/%s/stories/%s" % (pid, stories[0]["id"]), json={"status": "approved", "title": "Edited", "story_points": 5}).json()
    assert s["title"] == "Edited" and s["story_points"] == 5 and s["status"] == "approved"
    for f in ("xlsx", "jira-csv", "azure-csv", "md", "stories-csv", "requirements-csv", "rtm-csv", "json"):
        e = client.get("/api/projects/%s/export" % pid, params={"format": f, "scope": "all"})
        assert e.status_code == 200 and e.content, f
    approved = client.get("/api/projects/%s/export" % pid, params={"format": "json", "scope": "approved"}).json()
    assert [x["title"] for x in approved["stories"]] == ["Edited"]
    assert client.get("/api/projects/%s/export" % pid, params={"format": "zip"}).status_code == 400

    summary = client.get("/api/projects/%s" % pid).json()
    assert summary["requirements"] == 4 and summary["stories_approved"] == 1 and summary["coverage"] == 75

    # re-extraction resets the backlog
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 202
    _wait(client, pid)
    assert client.get("/api/projects/%s/stories" % pid).json() == []
    assert client.delete("/api/projects/%s" % pid).status_code == 204


def test_admin_providers_and_settings(client):
    data = client.get("/api/admin/providers").json()
    assert [p["kind"] for p in data["providers"]] == ["mock"] and data["providers"][0]["is_default"]
    bad = client.post("/api/admin/providers", json={"name": "x", "kind": "openai", "base_url": "ftp://x", "model": "m"})
    assert bad.status_code == 400
    p = client.post("/api/admin/providers", json={"name": "Local", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m1", "api_key": "sk-secret-1234"}).json()
    assert p["has_key"] and p["key_hint"] == "…1234" and "api_key" not in p
    listed = client.get("/api/admin/providers").text
    assert "sk-secret" not in listed
    from app import db, settings
    row = db.one("SELECT api_key_enc FROM providers WHERE id = %s", (p["id"],))
    assert "sk-secret" not in row["api_key_enc"] and settings.get_provider(p["id"])["api_key"] == "sk-secret-1234"
    # edit without key keeps it; clear_key removes it
    assert client.put("/api/admin/providers/%s" % p["id"], json={"name": "Local2", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m2"}).json()["has_key"]
    assert not client.put("/api/admin/providers/%s" % p["id"], json={"name": "Local2", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m2", "clear_key": True}).json()["has_key"]
    assert client.post("/api/admin/providers/%s/default" % p["id"]).status_code == 200
    assert client.get("/api/meta").json()["providers"][0]["id"] == p["id"]
    t = client.post("/api/admin/providers/%s/test" % p["id"]).json()
    assert t["ok"] is False and t["error"]
    # deleting the default falls back to another provider; last one can't be deleted
    assert client.delete("/api/admin/providers/%s" % p["id"]).status_code == 204
    remaining = client.get("/api/admin/providers").json()["providers"]
    assert len(remaining) == 1 and remaining[0]["is_default"]
    assert client.delete("/api/admin/providers/%s" % remaining[0]["id"]).status_code == 400

    s = client.put("/api/admin/settings", json={"temperature": 0.5, "rag_top_k": 0, "redact_pii": False}).json()
    assert s["values"]["temperature"] == 0.5 and s["values"]["redact_pii"] is False and not s["reindex_recommended"]
    assert client.put("/api/admin/settings", json={"temperature": 5}).status_code == 400
    assert client.put("/api/admin/settings", json={"unknown": 1}).status_code == 400
    assert client.put("/api/admin/settings", json={"embed_provider_id": remaining[0]["id"]}).status_code == 400  # mock can't embed
    o = client.get("/api/admin/overview").json()
    assert o["encryption_configured"] and "totals" in o
    assert isinstance(client.get("/api/admin/runs").json(), list)


def test_basic_auth(monkeypatch, client):
    from app import config
    monkeypatch.setattr(config, "BASIC_USER", "u")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "p")
    assert client.get("/api/meta").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/meta", auth=("u", "p")).status_code == 200
    assert client.get("/api/meta", auth=("u", "wrong")).status_code == 401


def test_mutations_require_csrf_header(client):
    assert client.post("/api/projects", json={"name": "x"}, headers={"X-Requested-With": ""}).status_code == 403
    assert client.get("/api/projects", headers={"X-Requested-With": ""}).status_code == 200
    assert client.post("/api/projects", json={"name": "ok", "platform": "generic"}).status_code == 201


def test_change_history_is_recorded(client):
    pid = client.post("/api/projects", json={"name": "Audit", "platform": "generic"}).json()["id"]
    auth = {"Authorization": "Basic " + __import__("base64").b64encode(b"analyst1:pw").decode()}
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}, headers=auth).json()
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"statement": "The system shall export invoices to CSV and PDF."}, headers=auth)
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"statement": "The system shall export invoices to CSV and PDF."}, headers=auth)  # no-op
    client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}, headers=auth)
    client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}, headers=auth)  # no-op
    h = client.get("/api/projects/%s/history" % pid, params={"item_id": r["id"]}).json()
    assert [e["action"] for e in h] == ["status changed", "edited", "created"]
    assert all(e["actor"] == "analyst1" and e["item_key"] == "REQ-001" for e in h)
    assert h[0]["changes"] == {"status": {"from": "draft", "to": "approved"}}
    assert h[1]["changes"]["statement"]["to"].endswith("CSV and PDF.")
    assert client.delete("/api/projects/%s/requirements/%s" % (pid, r["id"]), headers=auth).status_code == 204
    latest = client.get("/api/projects/%s/history" % pid).json()[0]
    assert latest["action"] == "deleted" and latest["summary"].startswith("The system shall export")
    assert client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}).json() == {"updated": 0}


def test_repository_sync_lifecycle(client, tmp_path, monkeypatch):
    from app import repos
    work = tmp_path / "work"
    (work / "brd").mkdir(parents=True)
    (work / "brd" / "orders.md").write_text("# Orders\nThe system shall allow buyers to place orders online.")
    (work / "brd" / "billing.md").write_text("# Billing\nThe system shall issue invoices for shipped orders.")
    (work / "README.md").write_text("not a BRD")
    state = {"sha": "a" * 40, "calls": []}

    def fake_fetch(url, branch, token, dest):
        import shutil
        state["calls"].append((url, branch, token))
        shutil.copytree(str(work), dest)
        return state["sha"]
    monkeypatch.setattr(repos, "fetch", fake_fetch)

    pid = client.post("/api/projects", json={"name": "Repo", "platform": "generic"}).json()["id"]
    assert client.post("/api/projects/%s/repos" % pid, json={"url": "http://insecure/x.git"}).status_code == 400
    repo = client.post("/api/projects/%s/repos" % pid, json={"url": "https://git.example.com/org/brds.git", "branch": "main",
                                                             "path_glob": "brd/*", "token": "tok-9876"}).json()
    assert repo["has_token"] and repo["key_hint"] == "…9876" and "token" not in repo and "token_enc" not in repo

    r = client.post("/api/projects/%s/repos/%s/sync" % (pid, repo["id"])).json()
    assert sorted(r["added"]) == ["brd/billing.md", "brd/orders.md"] and r["commit"] == "a" * 40
    assert state["calls"][0] == ("https://git.example.com/org/brds.git", "main", "tok-9876")
    docs = client.get("/api/projects/%s/documents" % pid).json()
    assert {d["path"] for d in docs} == {"brd/orders.md", "brd/billing.md"} and all(d["source"] == "repo" for d in docs)

    (work / "brd" / "orders.md").write_text("# Orders\nThe system shall allow buyers to place and cancel orders online.")
    (work / "brd" / "billing.md").unlink()
    state["sha"] = "b" * 40
    r = client.post("/api/projects/%s/repos/%s/sync" % (pid, repo["id"])).json()
    assert r["updated"] == ["brd/orders.md"] and r["removed"] == ["brd/billing.md"] and r["added"] == []
    assert client.post("/api/projects/%s/repos/%s/sync" % (pid, repo["id"])).json()["unchanged"] == 1
    listed = client.get("/api/projects/%s/repos" % pid).json()[0]
    assert listed["last_commit"] == "b" * 40 and listed["documents"] == 1 and listed["last_error"] == ""
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "synced"

    def failing(url, branch, token, dest):
        raise repos.RepoError("git clone failed: repository not found")
    monkeypatch.setattr(repos, "fetch", failing)
    bad = client.post("/api/projects/%s/repos/%s/sync" % (pid, repo["id"]))
    assert bad.status_code == 400 and "not found" in bad.json()["detail"]
    assert client.get("/api/projects/%s/repos" % pid).json()[0]["last_error"]

    assert client.delete("/api/projects/%s/repos/%s" % (pid, repo["id"])).status_code == 204
    assert client.get("/api/projects/%s/documents" % pid).json() == []
