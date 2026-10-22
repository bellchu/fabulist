"""API tests against a disposable Postgres database (TEST_DATABASE_URL)."""
import time

BRD = ("# Ordering\n## Vaccines\nThe system shall allow a customer to place orders for vaccines stored in cold chain.\n"
       "The system must confirm orders within 3 seconds.\n## Controls\nCompliance must receive an audit trail for controlled substance orders.\n")


def _wait(client, pid):
    for _ in range(60):
        runs = client.get("/api/projects/%s/runs?limit=1" % pid).json()
        if runs and runs[0]["status"] not in ("running", "queued"):
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
    assert cmp["from"] == "v1" and cmp["to"] == "current backlog (approved only)"
    assert cmp["stories"]["changed"] == [{"key": "US-001", "title": "Export invoices to CSV", "fields": ["title", "story_points"]}]
    assert cmp["stories"]["added"] == [] and cmp["requirements"]["changed"] == [], "a draft is not a change to an approved-only baseline"
    b2 = client.post("/api/projects/%s/baselines" % pid, json={"name": "Sprint 2", "scope": "all"}).json()
    assert b2["version"] == 2
    listing = client.get("/api/projects/%s/baselines" % pid).json()
    assert [(b["version"], b["stories"]) for b in listing] == [(2, 2), (1, 1)] and listing[1]["note"] == "Steering committee"
    assert client.get("/api/projects/%s/baselines/%s/compare" % (pid, b1["id"]), params={"to": b2["id"]}).json()["to"] == "v2"
    x = client.get("/api/projects/%s/baselines/%s/export" % (pid, b1["id"]), params={"format": "json"})
    assert x.status_code == 200 and "base-v1-json" in x.headers["content-disposition"]
    assert [s["title"] for s in x.json()["stories"]] == ["Export invoices"], "baseline keeps the frozen version"
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "baselined"


def test_baseline_compare_uses_the_baselines_own_scope(client):
    pid = client.post("/api/projects", json={"name": "Scope", "platform": "generic"}).json()["id"]
    story = lambda title, status: client.post("/api/projects/%s/stories" % pid, json={"title": title, "status": status}).json()  # noqa: E731
    kept, flipped = story("Kept", "approved"), story("Flipped", "approved")
    approved_only = client.post("/api/projects/%s/baselines" % pid, json={"name": "Sign-off"}).json()
    with_drafts = client.post("/api/projects/%s/baselines" % pid, json={"name": "Working copy", "scope": "all"}).json()
    new_draft, later = story("New draft", "draft"), story("Later approved", "draft")
    client.patch("/api/projects/%s/stories/%s" % (pid, later["id"]), json={"status": "approved"})
    client.patch("/api/projects/%s/stories/%s" % (pid, flipped["id"]), json={"status": "draft"})
    compare = lambda b: client.get("/api/projects/%s/baselines/%s/compare" % (pid, b["id"])).json()  # noqa: E731
    keys = lambda diff: sorted(x["key"] for x in diff)  # noqa: E731
    sign_off = compare(approved_only)
    # Against an approved-only baseline, only approval changes count: the new draft is invisible, the later approval is added,
    # and the story that left 'approved' is removed from the approved set.
    assert sign_off["to"] == "current backlog (approved only)"
    assert keys(sign_off["stories"]["added"]) == [later["key"]] and keys(sign_off["stories"]["removed"]) == [flipped["key"]]
    working = compare(with_drafts)
    assert working["to"] == "current backlog (approved and draft)"
    assert keys(working["stories"]["added"]) == sorted([new_draft["key"], later["key"]]) and working["stories"]["removed"] == []
    assert kept["key"] not in keys(sign_off["stories"]["added"]) + keys(sign_off["stories"]["removed"])


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


def _scrape(client):
    return client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})


def test_metrics_keep_working_and_report_the_database_state_during_an_outage(client, monkeypatch):
    from app import config, db
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-token")
    client.get("/api/projects")
    healthy = _scrape(client)
    assert healthy.status_code == 200 and "fabulist_database_up 1" in healthy.text and "fabulist_projects " in healthy.text
    real_one = db.one

    def down(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(db, "one", down)
    outage = _scrape(client)  # used to be a 500: the scrape failed and the alert said 'Fabulist is down'
    assert outage.status_code == 200 and "fabulist_database_up 0" in outage.text
    assert "fabulist_http_requests_total" in outage.text and "fabulist_projects " not in outage.text
    monkeypatch.setattr(db, "one", real_one)
    assert "fabulist_database_up 1" in _scrape(client).text


def _counter(text, series):
    """Value of one exposition line, 0 if it has not been emitted yet (counters are process-wide, so tests compare deltas)."""
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_database_503s_failed_logins_and_edit_conflicts_are_counted(client, monkeypatch):
    import psycopg
    from app import config, db, users
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-token")
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._failures.clear()
    root = ("root", "root-password-123")
    series = ['fabulist_auth_failures_total{kind="basic"}', 'fabulist_auth_failures_total{kind="token"}',
              "fabulist_edit_conflicts_total", "fabulist_database_unavailable_total"]
    before = {k: _counter(_scrape(client).text, k) for k in series}
    assert client.get("/api/projects", auth=("root", "wrong")).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer fab_nope"}).status_code == 401
    pid = client.post("/api/projects", json={"name": "Obs", "platform": "generic"}, auth=root).json()["id"]
    r = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}, auth=root).json()
    client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"notes": "x", "rev": 1}, auth=root)
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, r["id"]), json={"notes": "y", "rev": 1}, auth=root).status_code == 409
    real_conn = db.conn
    monkeypatch.setattr(db, "conn", lambda: (_ for _ in ()).throw(psycopg.OperationalError("down")))
    assert client.get("/api/projects", auth=root).status_code == 503
    monkeypatch.setattr(db, "conn", real_conn)
    after = {k: _counter(_scrape(client).text, k) for k in series}
    assert {k: after[k] - before[k] for k in series} == {k: 1.0 for k in series}
    users._failures.clear()


def test_push_remaining_is_counted(client, monkeypatch):
    from app import config
    import httpx
    from app import integrations
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-token")
    monkeypatch.setattr(config, "PUSH_BUDGET_SECONDS", 0)
    monkeypatch.setattr(integrations.httpx, "request", lambda m, u, headers=None, json=None, timeout=None: httpx.Response(
        201, json={"key": "FAB-1", "id": "1", "number": 1, "html_url": "https://x/1"}, request=httpx.Request(m, u)))
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "J", "base_url": "https://acme.atlassian.net",
                                                         "username": "a@b.com", "target": "FAB", "token": "tok-1234"}).json()
    pid = client.post("/api/projects", json={"name": "P", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/stories" % pid, json={"title": "One", "status": "approved"})
    client.post("/api/projects/%s/stories" % pid, json={"title": "Two", "status": "approved"})
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"}).json()
    assert r["remaining"] == 2
    assert 'fabulist_tracker_items_total{result="remaining",tracker="jira"} 2' in client.get("/metrics", headers={"Authorization": "Bearer scrape-token"}).text


def test_html_report_states_scope_and_date_for_exports_and_baselines(client):
    pid = client.post("/api/projects", json={"name": "Sign-off doc", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    client.post(api + "/requirements", json={"statement": "The system shall export invoices to CSV.", "status": "approved"})
    client.post(api + "/stories", json={"title": "Export invoices", "status": "approved"})
    approved = client.get(api + "/export", params={"format": "html", "scope": "approved"}).text
    assert "Scope: approved only" in approved and " UTC" in approved and "Generated " in approved
    everything = client.get(api + "/export", params={"format": "html", "scope": "all"}).text
    assert "Scope: everything in review" in everything
    client.post(api + "/baselines", json={"name": "Release 1", "scope": "approved"})
    bid = client.get(api + "/baselines").json()[0]["id"]
    frozen = client.get(api + "/baselines/%s/export" % bid, params={"format": "html"}).text
    assert "Baseline v1" in frozen and "Release 1" in frozen and "frozen " in frozen and "Scope: approved only" in frozen


def test_a_run_with_a_provider_that_needs_a_key_but_has_none_is_refused_at_the_start(client):
    pid = client.post("/api/projects", json={"name": "Keys", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."})
    anth = client.post("/api/admin/providers", json={"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "m"}).json()["id"]
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "provider_id": anth})
    assert r.status_code == 400 and "no API key" in r.json()["detail"] and "AI providers" in r.json()["detail"]
    assert client.get("/api/projects/%s/runs" % pid).json() == [], "no run was recorded"
    # self-hosted OpenAI-compatible endpoints legitimately need no key
    local = client.post("/api/admin/providers", json={"name": "Ollama", "kind": "openai", "base_url": "http://ollama:11434/v1", "model": "m"}).json()["id"]
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "provider_id": local}).status_code == 202
    _wait(client, pid)


def _running_run(pid, updated_minutes_ago=None, stage="requirements"):
    """Insert a run row that is 'running' and last made progress the given number of minutes ago."""
    from datetime import datetime, timedelta, timezone
    from app import db
    rid = db.new_id()
    created = db.now()
    updated = (datetime.now(timezone.utc) - timedelta(minutes=updated_minutes_ago)).isoformat(timespec="milliseconds") if updated_minutes_ago is not None else None
    db.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created, updated) VALUES (%s,%s,'running',%s,'Offline heuristic','','t',%s,%s)",
               (rid, pid, stage, created, updated))
    return rid


def test_a_run_with_no_progress_for_too_long_is_marked_stuck_and_no_longer_blocks_the_project(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "STALE_RUN_MINUTES", 15)
    pid = client.post("/api/projects", json={"name": "Stuck", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."})
    fresh = _running_run(pid, updated_minutes_ago=2)
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    assert r.status_code == 409 and "already in progress" in r.json()["detail"], "a run that is making progress still blocks"
    from app import db
    db.execute("UPDATE runs SET updated=%s WHERE id=%s", ("2000-01-01T00:00:00.000+00:00", fresh))
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    assert r.status_code == 202, r.text
    _wait(client, pid)
    old = next(x for x in client.get("/api/projects/%s/runs" % pid).json() if x["id"] == fresh)
    assert old["status"] == "failed" and "stuck" in old["error"] and "15 minutes" in old["error"]


def test_a_run_that_was_cancelled_or_marked_stuck_cannot_overwrite_results_when_its_thread_finally_finishes(client, monkeypatch):
    from app import db, pipeline, workspace
    pid = client.post("/api/projects", json={"name": "Zombie", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."})
    keep = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall keep this requirement."}).json()
    rid = _running_run(pid, updated_minutes_ago=0)

    def slow_extract(docs, platform, profile, opts, progress, usage):
        # while the AI call hung, an administrator's run was marked stuck/failed
        db.execute("UPDATE runs SET status='failed', error='Marked as stuck', cancel_requested=FALSE WHERE id=%s", (rid,))
        return [{"statement": "The system shall replace everything.", "type": "functional"}]
    monkeypatch.setattr(pipeline, "extract_requirements", slow_extract)
    providers = client.get("/api/admin/providers").json()["providers"]
    workspace._execute_run(rid, pid, "requirements", providers[0]["id"], "all")
    assert [r["statement"] for r in client.get("/api/projects/%s/requirements" % pid).json()] == [keep["statement"]], "existing results kept"
    row = db.one("SELECT status, error FROM runs WHERE id=%s", (rid,))
    assert row["status"] == "failed" and row["error"] == "Marked as stuck", "a late finish does not rewrite the status either"


def test_cancelling_during_a_provider_timeout_ends_as_cancelled_not_failed(client, monkeypatch):
    import httpx
    from app import db, llm
    pid = client.post("/api/projects", json={"name": "Cancel", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."})
    prov = client.post("/api/admin/providers", json={"name": "Hang", "kind": "openai", "base_url": "https://llm.test/v1", "model": "m", "api_key": "k"}).json()["id"]
    calls = []

    def hang(url, headers=None, json=None, timeout=None):
        calls.append(1)
        db.execute("UPDATE runs SET cancel_requested=TRUE WHERE project_id=%s AND status='running'", (pid,))  # the user clicks Cancel
        raise httpx.ReadTimeout("no answer")
    monkeypatch.setattr(llm.httpx, "post", hang)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "provider_id": prov}).status_code == 202
    run = _wait(client, pid)
    assert run["status"] == "cancelled" and "kept" in run["error"], run
    assert len(calls) == 1, "no further attempts after the cancel"


def test_ai_runs_use_their_own_bounded_pool_and_extra_runs_wait_in_a_queue(client, monkeypatch):
    import threading
    from app import config, pipeline
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(config, "MAX_QUEUED_RUNS", 1)
    release, started, names = threading.Event(), threading.Semaphore(0), []

    def blocking_extract(docs, platform, profile, opts, progress, usage):
        names.append(threading.current_thread().name)
        started.release()
        release.wait(20)
        return [{"statement": "The system shall export invoices to CSV.", "type": "functional"}]
    monkeypatch.setattr(pipeline, "extract_requirements", blocking_extract)
    pids = []
    for i in range(4):
        pid = client.post("/api/projects", json={"name": "Load %d" % i, "platform": "generic"}).json()["id"]
        client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall do a."})
        pids.append(pid)
    run = lambda p: client.post("/api/projects/%s/runs" % p, json={"stage": "requirements"})  # noqa: E731
    try:
        first = [run(p) for p in pids[:2]]
        assert [r.status_code for r in first] == [202, 202] and [r.json()["status"] for r in first] == ["running", "running"]
        assert started.acquire(timeout=10) and started.acquire(timeout=10)
        waiting = run(pids[2])
        assert waiting.status_code == 202 and waiting.json()["status"] == "queued", "both slots are busy: the run is accepted and waits"
        queued = client.get("/api/projects/%s" % pids[2]).json()["runs"]["requirements"]
        assert queued["status"] == "queued" and queued["queue_position"] == 1 and queued["queue_length"] == 1, queued
        full = run(pids[3])
        assert full.status_code == 429 and full.headers["retry-after"] and "queue is full" in full.json()["detail"], full.text
        assert run(pids[2]).status_code == 409, "a project has one run at a time, queued or running"
        assert client.get("/api/projects").status_code == 200, "the API keeps answering while runs are in progress"
        assert all(n.startswith("run") for n in names), "runs execute in their own pool, never in the request threads: %s" % names
    finally:
        release.set()
    for p in pids[:3]:
        assert _wait(client, p)["status"] == "done", "the queued run starts by itself when a slot frees up"
    assert run(pids[3]).status_code == 202
    _wait(client, pids[3])


def test_many_simultaneous_starts_never_label_more_runs_running_than_there_are_slots(client, monkeypatch):
    import threading
    from app import config, db, pipeline
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 3)
    release = threading.Event()
    monkeypatch.setattr(pipeline, "extract_requirements", lambda *a, **k: (release.wait(20), [{"statement": "The system shall export invoices to CSV.", "type": "functional"}])[1])
    pids = []
    for i in range(12):
        pid = client.post("/api/projects", json={"name": "Race %d" % i, "platform": "generic"}).json()["id"]
        client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall do a."})
        pids.append(pid)
    answers, gate = [], threading.Barrier(len(pids))

    def start(pid):
        gate.wait()  # all twelve at the same instant
        answers.append(client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).json()["status"])
    try:
        threads = [threading.Thread(target=start, args=(p,)) for p in pids]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        assert sorted(answers) == ["queued"] * 9 + ["running"] * 3, answers
        counts = {r["status"]: r["n"] for r in db.rows("SELECT status, count(*) AS n FROM runs GROUP BY status")}
        assert counts == {"running": 3, "queued": 9}, "the database agrees: only as many 'running' as there are slots (%s)" % counts
    finally:
        release.set()
    for p in pids:
        assert _wait(client, p)["status"] == "done"


def test_a_queued_run_can_be_cancelled_before_it_starts_and_never_runs(client, monkeypatch):
    import threading
    from app import config, pipeline
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 1)
    release, started, calls = threading.Event(), threading.Semaphore(0), []

    def blocking_extract(docs, platform, profile, opts, progress, usage):
        calls.append(docs[0]["filename"])
        started.release()
        release.wait(20)
        return [{"statement": "The system shall export invoices to CSV.", "type": "functional"}]
    monkeypatch.setattr(pipeline, "extract_requirements", blocking_extract)
    pids = []
    for name in ("first.md", "second.md"):
        pid = client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]
        client.post("/api/projects/%s/documents/text" % pid, json={"name": name, "text": "# A\nThe system shall do a."})
        pids.append(pid)
    try:
        assert client.post("/api/projects/%s/runs" % pids[0], json={"stage": "requirements"}).status_code == 202
        assert started.acquire(timeout=10)
        queued = client.post("/api/projects/%s/runs" % pids[1], json={"stage": "requirements"}).json()
        assert queued["status"] == "queued"
        assert client.post("/api/projects/%s/runs/%s/cancel" % (pids[1], queued["id"])).json()["status"] == "cancelled"
        assert client.get("/api/projects/%s/runs" % pids[1]).json()[0]["status"] == "cancelled"
    finally:
        release.set()
    _wait(client, pids[0])
    import time
    time.sleep(0.5)
    assert calls == ["first.md"], "the cancelled run was skipped when its turn came: %s" % calls
    assert client.get("/api/projects/%s/runs" % pids[1]).json()[0]["status"] == "cancelled"


def test_runs_that_were_waiting_when_the_server_restarted_are_failed_with_a_clear_message(client):
    from app import db, main
    pid = client.post("/api/projects", json={"name": "Waiting", "platform": "generic"}).json()["id"]
    rid = db.new_id()
    db.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created) VALUES (%s,%s,'queued','requirements','Offline heuristic','','t',%s)",
               (rid, pid, db.now()))
    main.recover_runs_after_restart()
    row = db.one("SELECT status, error FROM runs WHERE id=%s", (rid,))
    assert row["status"] == "failed" and "restart" in row["error"] and "start" in row["error"].lower()


def test_a_stuck_run_does_not_hold_a_slot_forever(client, monkeypatch):
    from app import config, db
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 1)
    monkeypatch.setattr(config, "STALE_RUN_MINUTES", 15)
    a = client.post("/api/projects", json={"name": "A", "platform": "generic"}).json()["id"]
    b = client.post("/api/projects", json={"name": "B", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % b, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."})
    stuck = _running_run(a, updated_minutes_ago=2)
    waits = client.post("/api/projects/%s/runs" % b, json={"stage": "requirements"})
    assert waits.status_code == 202 and waits.json()["status"] == "queued", "a live run holds the only slot, so this one waits"
    client.post("/api/projects/%s/runs/%s/cancel" % (b, waits.json()["id"]))
    db.execute("UPDATE runs SET updated=%s WHERE id=%s", ("2000-01-01T00:00:00.000+00:00", stuck))
    started = client.post("/api/projects/%s/runs" % b, json={"stage": "requirements"})
    assert started.status_code == 202 and started.json()["status"] == "running", "a run with no progress for 15 minutes no longer counts"
    _wait(client, b)
    db.execute("UPDATE runs SET status='failed' WHERE id=%s", (stuck,))  # the fixture waits for 'running' rows at teardown


def test_interactive_ai_endpoints_answer_429_when_too_many_are_in_flight_and_the_api_stays_up(client, monkeypatch):
    import threading
    import httpx
    from app import config, llm
    monkeypatch.setattr(config, "MAX_INTERACTIVE_AI_CALLS", 1)
    llm._reset_interactive_slots()
    prov = client.post("/api/admin/providers", json={"name": "Slow", "kind": "openai", "base_url": "https://llm.test/v1", "model": "m", "api_key": "k"}).json()["id"]
    pid = client.post("/api/projects", json={"name": "Busy", "platform": "generic"}).json()["id"]
    rid = client.post("/api/projects/%s/requirements" % pid, json={"statement": "Users should be able to do things quickly and easily."}).json()["id"]
    entered, release = threading.Event(), threading.Event()

    def slow(url, headers=None, json=None, timeout=None):
        entered.set()
        release.wait(10)
        return httpx.Response(200, json={"choices": [{"message": {"content": "[]"}}], "usage": {}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", slow)
    url = "/api/projects/%s/requirements/%s/refine" % (pid, rid)
    first = threading.Thread(target=lambda: client.post(url, json={"provider_id": prov}))
    first.start()
    try:
        assert entered.wait(5)
        busy = client.post(url, json={"provider_id": prov})
        assert busy.status_code == 429 and busy.headers["retry-after"] and "busy" in busy.json()["detail"].lower()
        assert client.get("/api/projects").status_code == 200, "the API keeps answering"
    finally:
        release.set()
        first.join(10)


def _project_for_estimates(client):
    pid = client.post("/api/projects", json={"name": "Estimate", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    client.post(api + "/documents/text", json={"name": "brd.md", "text": "# A\nThe system shall do a.\n\n# B\nThe system shall do b.\n\n# C\nThe system shall do c."})
    for i in range(30):
        client.post(api + "/requirements", json={"statement": "The system shall support capability number %d." % i, "epic": "E1", "section": "Big area", "status": "approved"})
    client.post(api + "/requirements", json={"statement": "The system shall obey the single rule.", "epic": "E1", "section": "Small area"})
    return pid


def test_run_estimate_counts_the_ai_requests_a_run_will_make(client):
    pid = _project_for_estimates(client)
    est = lambda **q: client.get("/api/projects/%s/runs/estimate" % pid, params=q).json()  # noqa: E731
    stories = est(stage="stories", scope="all")
    assert stories["requests"] == 3 and stories["limit"] >= 3 and stories["over_limit"] is False  # 30 -> 25 + 5, plus the small area
    assert est(stage="stories", scope="approved")["requests"] == 2, "only approved requirements are in scope"
    assert est(stage="requirements")["requests"] == 3 and est(stage="requirements")["items"] == 1, "one document, three headed sections"
    assert stories["seconds"] is None and stories["tokens"] is None, "nothing to base a time or cost guess on yet"
    assert client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "nope"}).status_code == 400


def test_run_estimate_is_calibrated_from_past_runs_and_flags_the_limit(client):
    from app import db
    pid = _project_for_estimates(client)
    db.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created, requests, duration_ms, tokens_in, tokens_out, item_count) "
               "VALUES (%s,%s,'done','stories','DeepSeek','m','t',%s,10,100000,6000,4000,12)", (db.new_id(), pid, db.now()))
    est = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "stories", "scope": "all"}).json()
    assert est["seconds"] == 30 and est["tokens"] == 3000 and "10 requests" in est["basis"], est  # 3 requests x 10 s and x 1000 tokens
    client.put("/api/admin/settings", json={"max_chunks_per_run": 2})
    over = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "stories", "scope": "all"}).json()
    assert over["over_limit"] is True and over["limit"] == 2


def test_a_finished_run_records_how_many_provider_requests_it_made(client, monkeypatch):
    import json as _json
    import httpx
    from app import db, llm
    pid = client.post("/api/projects", json={"name": "Counted", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    for i in range(30):  # 30 requirements in one feature area -> 25 + 5 = two story requests
        client.post(api + "/requirements", json={"statement": "The system shall support capability %d." % i, "epic": "E1", "section": "Area", "status": "approved"})
    prov = client.post("/api/admin/providers", json={"name": "Fake", "kind": "openai", "base_url": "https://llm.test/v1", "model": "m", "api_key": "k"}).json()["id"]
    story = [{"title": "A story", "narrative": "As a user, I want x, so that y.", "acceptance_criteria": ["Given a, when b, then c."], "requirement_ids": ["REQ-001"]}]

    def fake(url, headers=None, json=None, timeout=None):
        return httpx.Response(200, json={"choices": [{"message": {"content": _json.dumps(story)}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", fake)
    assert client.post(api + "/runs", json={"stage": "stories", "provider_id": prov}).status_code == 202
    run = _wait(client, pid)
    assert run["status"] == "done" and run["requests"] == 2 and run["tokens_in"] == 14
    assert db.one("SELECT requests FROM runs WHERE id=%s", (run["id"],))["requests"] == 2


def test_viewers_can_search_the_brds_and_comment_but_nothing_else(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "vera", "role": "viewer", "password": "vera-password-123"}, auth=root)
    vera = ("vera", "vera-password-123")
    pid = client.post("/api/projects", json={"name": "RO", "platform": "generic"}, auth=root).json()["id"]
    req = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}, auth=root).json()
    base = "/api/projects/%s" % pid
    # reading a passage is a read, even though the query travels in a POST body
    assert client.post(base + "/search", json={"query": "invoices"}, auth=vera).status_code == 200
    assert client.post(base + "/comments", json={"item_id": req["id"], "body": "In scope for release 1?"}, auth=vera).status_code == 201
    # everything that changes the backlog, or spends AI budget, stays closed
    for path, body in (("/requirements", {"statement": "The system shall add something new."}), ("/ask", {"question": "What is in scope?"}),
                       ("/runs", {"stage": "requirements"}), ("/baselines", {"name": "x"}), ("/requirements/bulk", {"ids": [req["id"]], "status": "approved"})):
        assert client.post(base + path, json=body, auth=vera).status_code == 403, path
    assert client.patch(base + "/requirements/" + req["id"], json={"status": "approved"}, auth=vera).status_code == 403


def test_an_extraction_that_is_over_the_limit_is_refused_up_front_not_started_and_failed(client):
    pid = _project_for_estimates(client)  # one document, three sections
    client.put("/api/admin/settings", json={"max_chunks_per_run": 2})
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "scope": "all"})
    assert r.status_code == 400 and "3 sections" in r.json()["detail"] and "limit of 2" in r.json()["detail"], r.text
    assert client.get("/api/projects/%s/runs" % pid).json() == [], "no run row is created for a request that can never succeed"
    client.put("/api/admin/settings", json={"max_chunks_per_run": 3})
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "scope": "all"}).status_code == 202


def test_listing_documents_counts_chunks_per_document_and_the_lookup_is_indexed(client):
    from app import db
    pid = client.post("/api/projects", json={"name": "Many", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    client.post(api + "/documents/text", json={"name": "one.md", "text": "# A\nThe system shall do a thing for the buyer."})
    client.post(api + "/documents/text", json={"name": "two.md", "text": "# A\nThe system shall do one.\n\n# B\nThe system shall do two."})
    docs = {d["filename"]: d for d in client.get(api + "/documents").json()}
    assert docs["one.md"]["chunks"] >= 1 and docs["two.md"]["chunks"] >= 1 and docs["one.md"]["chars"] > 0
    # the per-document count and the cascade on delete look chunks up by document: without this index each is a scan of every chunk
    defs = [r["indexdef"] for r in db.rows("SELECT indexdef FROM pg_indexes WHERE tablename = 'chunks'")]
    assert any(d.endswith("(document_id)") for d in defs), "chunks needs an index on document_id: %s" % defs


def test_duplicate_analysis_is_cached_per_project_and_recomputed_when_a_requirement_changes(client, monkeypatch):
    from app import quality
    calls = []
    real = quality.duplicate_pairs
    monkeypatch.setattr(quality, "duplicate_pairs", lambda reqs, *a, **k: (calls.append(len(reqs)), real(reqs, *a, **k))[1])
    pid = client.post("/api/projects", json={"name": "Cache", "platform": "generic"}).json()["id"]
    api = "/api/projects/%s" % pid
    a = client.post(api + "/requirements", json={"statement": "The system shall export every invoice to CSV for the finance team."}).json()
    client.post(api + "/requirements", json={"statement": "The system shall export every invoice to CSV for the finance team!"})
    first = client.get(api + "/requirements").json()
    n = len(calls)
    for _ in range(3):
        client.get(api + "/requirements")
        client.get(api)
    assert len(calls) == n, "unchanged requirements: the analysis is not repeated (%d calls became %d)" % (n, len(calls))
    assert any(i["code"] == "duplicate" for i in first[0]["issues"]) and any(i["code"] == "duplicate" for i in first[1]["issues"])
    client.patch(api + "/requirements/" + a["id"], json={"statement": "The system shall archive closed orders after seven years automatically."})
    after = client.get(api + "/requirements").json()
    assert len(calls) == n + 1, "an edit invalidates it"
    assert not any(i["code"] == "duplicate" for r in after for i in r["issues"]), "and the stale duplicate flags are gone"
    client.patch(api + "/requirements/" + after[1]["id"], json={"status": "excluded"})
    client.get(api + "/requirements")
    assert len(calls) == n + 2, "excluding one changes what is compared"


def test_only_a_few_documents_are_parsed_at_once_the_rest_wait_and_none_fail(client, monkeypatch):
    import threading
    import time
    from app import config, parsing
    monkeypatch.setattr(config, "MAX_CONCURRENT_PARSES", 2)
    lock, now, peak = threading.Lock(), [0], [0]

    def slow_parse(name, data):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.2)
        with lock:
            now[0] -= 1
        return "# A\nThe system shall do a thing for the buyer."
    monkeypatch.setattr(parsing, "extract_text", slow_parse)
    pid = client.post("/api/projects", json={"name": "Parse", "platform": "generic"}).json()["id"]
    codes = []

    def upload(i):
        r = client.post("/api/projects/%s/documents" % pid, files={"file": ("brd%d.docx" % i, b"x" * 100, "application/octet-stream")})
        codes.append(r.status_code)
    threads = [threading.Thread(target=upload, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert codes == [201] * 8, "all eight uploads are stored: %s" % codes
    assert peak[0] == 2, "but never more than MAX_CONCURRENT_PARSES parse at the same moment (%d)" % peak[0]


def test_an_upload_that_cannot_get_a_parsing_slot_in_time_is_told_to_retry(client, monkeypatch):
    import threading
    from app import config, parsing, workspace
    monkeypatch.setattr(config, "MAX_CONCURRENT_PARSES", 1)
    monkeypatch.setattr(config, "PARSE_WAIT_SECONDS", 0.3)
    workspace._reset_parse_slots()
    release = threading.Event()
    monkeypatch.setattr(parsing, "extract_text", lambda name, data: (release.wait(10), "# A\nThe system shall do a thing.")[1])
    pid = client.post("/api/projects", json={"name": "Busy", "platform": "generic"}).json()["id"]
    first = threading.Thread(target=lambda: client.post("/api/projects/%s/documents" % pid, files={"file": ("a.docx", b"x", "application/octet-stream")}))
    first.start()
    import time
    time.sleep(0.2)
    try:
        second = client.post("/api/projects/%s/documents" % pid, files={"file": ("b.docx", b"x", "application/octet-stream")})
        assert second.status_code == 503 and second.headers["retry-after"] and "busy" in second.json()["detail"].lower(), second.text
    finally:
        release.set()
        first.join(10)
