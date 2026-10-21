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
    assert client.delete("/api/projects/%s" % pid).status_code == 409  # active projects are archived first
    assert client.post("/api/projects/%s/archive" % pid).status_code == 200
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


def test_change_history_is_recorded(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    client.post("/api/admin/users", json={"username": "analyst1", "role": "analyst", "password": "analyst-password-1"}, auth=("root", "root-password-123"))
    auth = {"Authorization": "Basic " + __import__("base64").b64encode(b"analyst1:analyst-password-1").decode()}
    pid = client.post("/api/projects", json={"name": "Audit", "platform": "generic"}, headers=auth).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}, headers=auth).json()
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"statement": "The system shall export invoices to CSV and PDF."}, headers=auth)
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"statement": "The system shall export invoices to CSV and PDF."}, headers=auth)  # no-op
    client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}, headers=auth)
    client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}, headers=auth)  # no-op
    h = client.get("/api/projects/%s/history" % pid, params={"item_id": r["id"]}, headers=auth).json()
    assert [e["action"] for e in h] == ["status changed", "edited", "created"]
    assert all(e["actor"] == "analyst1" and e["item_key"] == "REQ-001" for e in h)
    assert h[0]["changes"] == {"status": {"from": "draft", "to": "approved"}}
    assert h[1]["changes"]["statement"]["to"].endswith("CSV and PDF.")
    assert client.delete("/api/projects/%s/requirements/%s" % (pid, r["id"]), headers=auth).status_code == 204
    latest = client.get("/api/projects/%s/history" % pid, headers=auth).json()[0]
    assert latest["action"] == "deleted" and latest["summary"].startswith("The system shall export")
    assert client.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": [r["id"]], "status": "approved"}, headers=auth).json() == {"updated": 0}


def test_repository_sync_lifecycle(client, tmp_path, monkeypatch):
    from app import repos
    monkeypatch.setattr(repos, "_resolve", lambda host: ["140.82.112.3"])
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


def test_cancel_run_keeps_existing_data(client, monkeypatch):
    from app import db, pipeline
    pid = client.post("/api/projects", json={"name": "Cancel", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": BRD})
    client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    _wait(client, pid)
    before = client.get("/api/projects/%s/requirements" % pid).json()

    def slow_extract(docs, platform, profile, opts, progress, usage):
        progress("Reading section 1")
        db.execute("UPDATE runs SET cancel_requested=TRUE WHERE project_id=%s AND status='running'", (pid,))
        progress("Reading section 2")
        raise AssertionError("should have been cancelled")
    monkeypatch.setattr(pipeline, "extract_requirements", slow_extract)
    client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    run = _wait(client, pid)
    assert run["status"] == "cancelled" and "kept" in run["error"]
    assert client.get("/api/projects/%s/requirements" % pid).json() == before
    assert client.post("/api/projects/%s/runs/%s/cancel" % (pid, run["id"])).status_code == 409


def test_refine_story_proposes_without_saving(client):
    pid = client.post("/api/projects", json={"name": "Refine", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall let buyers cancel open orders.", "actors": ["buyer"]}).json()
    s = client.post("/api/projects/%s/stories" % pid, json={"title": "Cancel open orders", "persona": "user", "narrative": "cancel orders",
                                                            "acceptance_criteria": ["order status becomes cancelled"], "requirement_ids": [r["key"]]}).json()
    assert s["quality_score"] < 70
    p = client.post("/api/projects/%s/stories/%s/refine" % (pid, s["id"]), json={}).json()
    assert p["story"]["persona"] == "buyer" and p["story"]["narrative"].startswith("As a buyer, I want to cancel open orders")
    assert p["story"]["acceptance_criteria"] == ["Then order status becomes cancelled"]
    assert p["quality_score"] > p["before_score"] and p["story"]["requirement_ids"] == [r["key"]]
    assert client.get("/api/projects/%s/stories" % pid).json()[0]["narrative"] == "cancel orders", "refine must not save"
    assert client.post("/api/projects/%s/stories/nope/refine" % pid, json={}).status_code == 404


def test_duplicate_flag_ignores_excluded(client):
    pid = client.post("/api/projects", json={"name": "Dupes", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall allow buyers to cancel open orders before shipment."})
    b = client.post("/api/projects/%s/requirements" % pid, json={"statement": "Buyers must be able to cancel open orders before shipment."}).json()
    reqs = client.get("/api/projects/%s/requirements" % pid).json()
    assert all(any(i["code"] == "duplicate" for i in r["issues"]) for r in reqs)
    client.patch("/api/projects/%s/requirements/%s" % (pid, b["id"]), json={"status": "excluded"})
    reqs = client.get("/api/projects/%s/requirements" % pid).json()
    assert not any(i["code"] == "duplicate" for r in reqs for i in r["issues"])


def test_metrics_endpoint_and_access_log(client, monkeypatch, caplog):
    from app import config
    assert client.get("/metrics").status_code == 404  # disabled without a token
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-token")
    assert client.get("/metrics").status_code == 404
    pid = client.post("/api/projects", json={"name": "M", "platform": "generic"}).json()["id"]
    client.get("/api/projects/%s" % pid)
    client.get("/api/projects/does-not-exist")
    body = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"}).text
    assert 'fabulist_http_requests_total{method="GET",route="/api/projects/{pid}",status="200"}' in body
    assert 'route="/api/projects/{pid}",status="404"' in body
    assert "fabulist_http_request_duration_seconds_bucket" in body and "fabulist_projects 1" in body
    assert pid not in body, "raw paths must not become labels"


def test_html_report_inline_preview(client):
    pid = client.post("/api/projects", json={"name": "Report", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."})
    r = client.get("/api/projects/%s/export" % pid, params={"format": "html", "scope": "all", "inline": "true"})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    assert "Report backlog" in r.text and "REQ-001" in r.text
    assert client.get("/api/projects/%s/export" % pid, params={"format": "csv-nope"}).status_code == 400


def test_refine_requirement_endpoint(client):
    pid = client.post("/api/projects", json={"name": "RR", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "Agents should be able to place orders and the screens must be fast."}).json()
    p = client.post("/api/projects/%s/requirements/%s/refine" % (pid, r["id"]), json={}).json()
    assert len(p["proposals"]) == 2 and p["proposals"][0]["requirement"]["statement"].startswith("Agents shall be able to")
    assert p["proposals"][0]["quality_score"] > p["before_score"]
    assert len(client.get("/api/projects/%s/requirements" % pid).json()) == 1, "refine must not save"


def test_provider_model_list_endpoint(client, monkeypatch):
    import httpx
    from app import llm
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(headers)
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-embed"}]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(llm.httpx, "get", fake_get)
    r = client.post("/api/admin/providers/models", json={"kind": "openai", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-new"}).json()
    assert [m["id"] for m in r["models"]] == ["deepseek-chat", "deepseek-embed"] and [m["id"] for m in r["embedding_models"]] == ["deepseek-embed"]
    p = client.post("/api/admin/providers", json={"name": "DS", "kind": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": "sk-saved"}).json()
    client.post("/api/admin/providers/models", json={"kind": "openai", "base_url": "https://api.deepseek.com/v1", "provider_id": p["id"]})
    assert calls[-1] == {"Authorization": "Bearer sk-saved"}, "blank key falls back to the saved key"
    assert client.post("/api/admin/providers/models", json={"kind": "mock", "base_url": "x"}).status_code == 400
    assert client.post("/api/admin/providers/models", json={"kind": "openai", "base_url": ""}).status_code == 400


def test_ask_brds_with_citations(client):
    pid = client.post("/api/projects", json={"name": "Ask", "platform": "generic"}).json()["id"]
    assert "No indexed" in client.post("/api/projects/%s/ask" % pid, json={"question": "What about returns?"}).json()["answer"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "returns.md", "text":
        "# Returns\nCustomers may return non-expired med-surg supplies within 30 days.\n\nRecalled lots must be quarantined within 4 hours."})
    r = client.post("/api/projects/%s/ask" % pid, json={"question": "How many days do customers have to return supplies?"}).json()
    assert "30 days" in r["answer"] and "[1]" in r["answer"] and r["citations"][0]["filename"] == "returns.md"
    assert client.post("/api/projects/%s/ask" % pid, json={"question": "x"}).status_code == 422


def test_baselines_snapshot_compare_and_export(client):
    pid = client.post("/api/projects", json={"name": "Base", "platform": "generic"}).json()["id"]
    assert client.post("/api/projects/%s/baselines" % pid, json={"name": "empty"}).status_code == 400
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV.", "status": "approved"}).json()
    s1 = client.post("/api/projects/%s/stories" % pid, json={"title": "Export invoices", "narrative": "As a finance analyst, I want CSV, so that I reconcile.",
                                                             "acceptance_criteria": ["Given invoices, when I export, then a CSV downloads."],
                                                             "requirement_ids": [r["key"]], "status": "approved"}).json()
    b1 = client.post("/api/projects/%s/baselines" % pid, json={"name": "Sign-off", "note": "Steering committee"}).json()
    assert b1["version"] == 1
    client.patch("/api/projects/%s/stories/%s" % (pid, s1["id"]), json={"title": "Export invoices to CSV", "story_points": 3})
    client.post("/api/projects/%s/stories" % pid, json={"title": "Email invoices", "status": "draft"})
    cmp = client.get("/api/projects/%s/baselines/%s/compare" % (pid, b1["id"])).json()
    assert cmp["from"] == "v1" and cmp["to"] == "current backlog"
    assert cmp["stories"]["changed"] == [{"key": "US-001", "title": "Export invoices to CSV", "fields": ["title", "story_points"]}]
    assert [a["key"] for a in cmp["stories"]["added"]] == ["US-002"] and cmp["requirements"]["changed"] == []
    b2 = client.post("/api/projects/%s/baselines" % pid, json={"name": "Sprint 2", "scope": "all"}).json()
    assert b2["version"] == 2
    listing = client.get("/api/projects/%s/baselines" % pid).json()
    assert [(b["version"], b["stories"]) for b in listing] == [(2, 2), (1, 1)] and listing[1]["note"] == "Steering committee"
    assert client.get("/api/projects/%s/baselines/%s/compare" % (pid, b1["id"]), params={"to": b2["id"]}).json()["to"] == "v2"
    x = client.get("/api/projects/%s/baselines/%s/export" % (pid, b1["id"]), params={"format": "json"})
    assert x.status_code == 200 and "base-v1-json" in x.headers["content-disposition"]
    assert [s["title"] for s in x.json()["stories"]] == ["Export invoices"], "baseline keeps the frozen version"
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "baselined"


def test_estimate_and_apply_points(client):
    pid = client.post("/api/projects", json={"name": "Est", "platform": "generic"}).json()["id"]
    small = client.post("/api/projects/%s/stories" % pid, json={"title": "Show order status", "acceptance_criteria": ["Given an order, then its status shows."]}).json()
    big = client.post("/api/projects/%s/stories" % pid, json={"title": "Integrate EDI 850 orders", "acceptance_criteria": ["a", "b", "c", "d"],
                                                              "compliance_tags": ["DSCSA"], "variant_criteria": [{"scope": "Biologics", "criteria": "cold chain"}]}).json()
    client.post("/api/projects/%s/stories" % pid, json={"title": "Already sized", "story_points": 2})
    props = client.post("/api/projects/%s/stories/estimate" % pid, json={}).json()["proposals"]
    assert {p["key"] for p in props} == {small["key"], big["key"]}, "only unestimated stories by default"
    by = {p["key"]: p for p in props}
    assert by[small["key"]]["points"] < by[big["key"]]["points"] and "acceptance criteria" in by[big["key"]]["rationale"]
    assert client.get("/api/projects/%s/stories" % pid).json()[0].get("story_points") is None, "estimate must not save"
    r = client.post("/api/projects/%s/stories/points" % pid, json={"points": {p["id"]: p["points"] for p in props}}).json()
    assert r == {"updated": 2}
    assert client.post("/api/projects/%s/stories/points" % pid, json={"points": {small["id"]: 4}}).status_code == 400
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "estimated"
    assert len(client.post("/api/projects/%s/stories/estimate" % pid, json={"scope": "all"}).json()["proposals"]) == 3


def test_comments_thread_counts_and_history(client):
    pid = client.post("/api/projects", json={"name": "Talk", "platform": "generic"}).json()["id"]
    s = client.post("/api/projects/%s/stories" % pid, json={"title": "Cancel orders"}).json()
    a = client.post("/api/projects/%s/comments" % pid, json={"item_id": s["id"], "body": "Is cancellation allowed after picking?"}).json()
    client.post("/api/projects/%s/comments" % pid, json={"item_id": s["id"], "body": "Only before the pick wave starts."})
    assert client.post("/api/projects/%s/comments" % pid, json={"item_id": "nope", "body": "x"}).status_code == 404
    assert client.post("/api/projects/%s/comments" % pid, json={"item_id": s["id"], "body": ""}).status_code == 422
    assert [c["body"][:5] for c in client.get("/api/projects/%s/comments" % pid, params={"item_id": s["id"]}).json()] == ["Is ca", "Only "]
    assert client.get("/api/projects/%s/comments" % pid).json() == {s["id"]: 2}
    client.post("/api/projects/%s/comments/%s/resolve" % (pid, a["id"]))
    assert client.get("/api/projects/%s/comments" % pid).json() == {s["id"]: 1}
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "commented"
    assert client.delete("/api/projects/%s/comments/%s" % (pid, a["id"])).status_code == 204


def test_product_class_coverage(client):
    pid = client.post("/api/projects", json={"name": "Prod", "platform": "generic"}).json()["id"]
    vac = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall ship vaccines in validated cold chain packaging."}).json()
    ret = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall accept returns of vaccines within 30 days."}).json()
    implant = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall capture UDI for implants at receipt."}).json()
    assert "Biologics and vaccines" in vac["product_scope"] and "Implants and devices" in implant["product_scope"]
    client.post("/api/projects/%s/stories" % pid, json={"title": "Ship vaccines", "requirement_ids": [vac["key"]],
                                                        "variant_criteria": [{"scope": "Biologics and vaccines", "criteria": "Temperature logged"}]})
    client.post("/api/projects/%s/stories" % pid, json={"title": "Accept returns", "requirement_ids": [ret["key"]], "product_scope": []})
    prods = {p["product_class"]: p for p in client.get("/api/projects/%s/traceability" % pid).json()["products"]}
    bio = prods["Biologics and vaccines"]
    assert bio["requirements"] == 2 and bio["covered"] == 1
    assert bio["gaps"] == [{"key": ret["key"], "statement": ret["statement"], "reason": "linked stories do not address this product class"}]
    assert prods["Implants and devices"]["gaps"][0]["reason"] == "no linked story"


def test_audit_trail_csv_export_is_complete_oldest_first_and_formula_safe(client):
    import csv, io
    pid = client.post("/api/projects", json={"name": "Audit Export", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}).json()
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"statement": "=HYPERLINK(\"x\") The system shall export invoices."})
    resp = client.get("/api/projects/%s/history.csv" % pid)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert 'filename="audit-export-audit-trail.csv"' in resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert [x["Action"] for x in rows] == ["created", "edited"]
    assert rows[1]["Item"] == "REQ-001" and rows[1]["Actor"] == "anonymous"
    assert "statement" in rows[1]["Changes"]
    assert not any(c.startswith("=") for x in rows for c in x.values())
    assert client.get("/api/projects/nope/history.csv").status_code == 404


def test_meta_reports_build_version(client, monkeypatch):
    from app import config
    assert client.get("/api/meta").json()["version"] == config.VERSION
    monkeypatch.setattr(config, "VERSION", "abc1234")
    assert client.get("/api/meta").json()["version"] == "abc1234"


def test_api_responses_are_not_cacheable_and_hsts_follows_https(client):
    r = client.get("/api/projects")
    assert r.headers["cache-control"] == "no-store" and "strict-transport-security" not in r.headers
    r = client.get("/api/projects", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"


def test_history_filters_and_cursor_paging(client):
    pid = client.post("/api/projects", json={"name": "Audit View", "platform": "generic"}).json()["id"]
    for i in range(5):
        client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export report %d to CSV." % i})
    base = "/api/projects/%s/history" % pid
    page1 = client.get(base, params={"limit": 2}).json()
    assert len(page1) == 2 and page1[0]["n"] > page1[1]["n"]
    page2 = client.get(base, params={"limit": 2, "before": page1[-1]["n"]}).json()
    assert len(page2) == 2 and page2[0]["n"] < page1[-1]["n"]
    assert client.get(base, params={"action": "created"}).json().__len__() == 5
    assert client.get(base, params={"action": "deleted"}).json() == []
    assert client.get(base, params={"actor": "nobody"}).json() == []
    f = client.get(base + "/filters").json()
    assert f == {"actors": ["anonymous"], "actions": ["created"]}
    assert client.get("/api/projects/nope/history/filters").status_code == 404


def test_oversized_request_bodies_are_rejected_before_parsing(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(config, "MAX_DOC_CHARS", 100)
    limit = config.max_body_bytes()
    assert limit < 2 * 1024 * 1024
    pid = client.post("/api/projects", json={"name": "Limits", "platform": "generic"}).json()["id"]
    big = client.post("/api/projects/%s/documents/text" % pid, content=b"x" * (limit + 1), headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and "too large" in big.json()["detail"].lower()
    assert client.post("/api/projects/%s/documents/text" % pid, json={"name": "ok", "text": "small"}).status_code == 201
    assert client.post("/api/projects", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "abc"}).status_code == 400


def test_archive_restore_and_guarded_permanent_delete(client, monkeypatch):
    from app import config
    pid = client.post("/api/projects", json={"name": "Retire me", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}).json()
    names = lambda **q: [p["name"] for p in client.get("/api/projects", params=q).json()]
    assert client.delete("/api/projects/%s" % pid).status_code == 409  # must be archived first

    assert client.post("/api/projects/%s/archive" % pid).status_code == 200
    assert "Retire me" not in names() and "Retire me" in names(archived="true")
    assert client.get("/api/projects/%s" % pid).json()["archived"]
    # read-only while archived: edits and runs are refused, reads and read-only POSTs still work
    assert client.patch("/api/projects/%s" % pid, json={"name": "x"}).status_code == 409
    assert client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall do something else."}).status_code == 409
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"status": "approved"}).status_code == 409
    assert client.get("/api/projects/%s/requirements" % pid).status_code == 200
    assert client.post("/api/projects/%s/search" % pid, json={"query": "export invoices"}).status_code == 200
    assert client.post("/api/projects/%s/archive" % pid).status_code == 409  # already archived

    assert client.post("/api/projects/%s/restore" % pid).status_code == 200
    assert "Retire me" in names()
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"status": "approved"}).status_code == 200
    actions = [e["action"] for e in client.get("/api/projects/%s/history" % pid).json()]
    assert {"archived", "restored"} <= set(actions)

    # only admins delete permanently, and only archived projects
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "analyst1", "role": "analyst", "password": "analyst-password-1"}, auth=root)
    analyst = ("analyst1", "analyst-password-1")
    assert client.post("/api/projects/%s/archive" % pid, auth=analyst).status_code == 200
    assert client.delete("/api/projects/%s" % pid, auth=analyst).status_code == 403
    assert client.delete("/api/projects/%s" % pid, auth=root).status_code == 204
    assert client.get("/api/projects/%s" % pid, auth=root).status_code == 404


def test_concurrent_edits_are_detected_with_a_revision(client):
    pid = client.post("/api/projects", json={"name": "Concurrent", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    r = client.post(api + "/requirements", json={"statement": "The system shall export invoices to CSV."}).json()
    assert r["rev"] == 1
    mine = client.patch(api + "/requirements/%s" % r["id"], json={"statement": "The system shall export invoices to CSV and PDF.", "rev": 1})
    assert mine.status_code == 200
    item = lambda: next(x for x in client.get(api + "/requirements").json() if x["id"] == r["id"])
    assert item()["rev"] == 2
    # a second person who opened the item before that save is told, and nothing is overwritten
    stale = client.patch(api + "/requirements/%s" % r["id"], json={"statement": "The system shall export invoices to XML.", "rev": 1})
    assert stale.status_code == 409 and "changed by someone else" in stale.json()["detail"]
    assert item()["statement"].endswith("CSV and PDF.") and item()["rev"] == 2
    assert [e["action"] for e in client.get(api + "/history", params={"item_id": r["id"]}).json()] == ["edited", "created"]
    # clients that send no revision (scripts) keep working
    assert client.patch(api + "/requirements/%s" % r["id"], json={"notes": "scripted"}).status_code == 200 and item()["rev"] == 3
    # a bulk approval by someone else also makes an open editor stale (its payload would revert the status)
    client.post(api + "/requirements/bulk", json={"ids": [r["id"]], "status": "approved"})
    assert item()["rev"] == 4
    assert client.patch(api + "/requirements/%s" % r["id"], json={"statement": "The system shall export invoices to JSON.", "status": "draft", "rev": 3}).status_code == 409
    assert item()["status"] == "approved"
    # stories behave the same way
    s = client.post(api + "/stories", json={"title": "Export invoices"}).json()
    assert s["rev"] == 1
    assert client.patch(api + "/stories/%s" % s["id"], json={"title": "Export invoices now", "rev": 1}).status_code == 200
    assert client.patch(api + "/stories/%s" % s["id"], json={"title": "Export invoices later", "rev": 1}).status_code == 409
    assert client.patch(api + "/stories/%s" % s["id"], json={"title": "Export invoices", "rev": "abc"}).status_code == 400


def test_database_outage_is_a_retryable_503_with_a_clear_message(client, monkeypatch):
    import psycopg
    import psycopg_pool
    from app import db

    def pool_timeout():
        raise psycopg_pool.PoolTimeout("couldn't get a connection after 10.00 sec")

    def dropped():
        raise psycopg.OperationalError("server closed the connection unexpectedly")
    real_conn = db.conn
    for failure in (pool_timeout, dropped):
        monkeypatch.setattr(db, "conn", failure)
        for path in ("/api/projects", "/healthz"):
            r = client.get(path)
            assert r.status_code == 503, (failure.__name__, path, r.status_code)
            assert r.headers["retry-after"] == "5" and "database" in r.json()["detail"].lower()
    monkeypatch.setattr(db, "conn", real_conn)
    assert client.get("/healthz").status_code == 200  # recovers as soon as the database is back


def test_other_server_errors_stay_500(client, monkeypatch):
    from app import db
    from fastapi.testclient import TestClient
    from app.main import app

    def broken():
        raise RuntimeError("bug")
    real_conn = db.conn
    with TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "fabulist"}) as c:  # started before the patch
        monkeypatch.setattr(db, "conn", broken)
        assert c.get("/api/projects").status_code == 500
        monkeypatch.setattr(db, "conn", real_conn)  # the fixture's teardown needs the database
