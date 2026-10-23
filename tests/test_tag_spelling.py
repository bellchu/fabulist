"""One spelling per compliance tag: saving an item with 'sox' in a project that already says 'SOX' keeps 'SOX'."""


def test_a_saved_item_reuses_the_spelling_the_project_already_has(client):
    pid = client.post("/api/projects", json={"name": "Spelling", "platform": "generic"}).json()["id"]
    post = lambda path, body: client.post("/api/projects/%s/%s" % (pid, path), json=body).json()  # noqa: E731
    post("requirements", {"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]})
    second = post("requirements", {"statement": "The system shall mask card numbers in reports.", "compliance_tags": ["sox", "SOX", "New-Tag"]})
    assert second["compliance_tags"] == ["New-Tag", "SOX"], "known spelling reused; a new tag keeps what was typed; duplicates by case collapse"
    story = post("stories", {"title": "Audit payments", "compliance_tags": ["Sox"]})
    assert story["compliance_tags"] == ["SOX"], "stories share the spelling with requirements"


def test_an_import_reuses_the_spelling_the_project_already_has(client):
    pid = client.post("/api/projects", json={"name": "Import spelling", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]})
    csv = "Requirement,Compliance\nThe system shall mask card numbers in reports.,sox;gdpr\nThe system shall email receipts to buyers.,GDPR\n"
    assert client.post("/api/projects/%s/requirements/import" % pid, json={"csv": csv}).json()["created"] == 2
    tags = {r["key"]: r["compliance_tags"] for r in client.get("/api/projects/%s/requirements" % pid).json()}
    assert tags["REQ-002"] == ["SOX", "gdpr"] or tags["REQ-002"] == ["gdpr", "SOX"]
    assert tags["REQ-003"] == ["gdpr"], "the first spelling met in the file wins for a new tag"


def test_requirements_added_by_a_run_use_the_projects_spelling_of_a_tag(client):
    import time
    pid = client.post("/api/projects", json={"name": "Run spelling", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall log every payment attempt.", "compliance_tags": ["Sarbanes"]})
    mine = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall keep an audit trail.", "compliance_tags": ["sox"]}).json()
    assert "sox" in mine["compliance_tags"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "BRD", "text": "# Finance\nThe system shall retain SOX audit evidence for seven years."})
    assert client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "documents": "new"}).status_code == 202
    for _ in range(100):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    tags = sum((r["compliance_tags"] for r in client.get("/api/projects/%s/requirements" % pid).json()), [])
    assert "SOX" not in tags, "the run's SOX was spelled the way the project already writes it (sox)"
    assert tags.count("sox") >= 2
