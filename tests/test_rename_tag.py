"""Rename a compliance tag across a project (fix a typo, or fold 'sox' into 'SOX'); every changed item is in the audit trail."""


def _setup(client):
    pid = client.post("/api/projects", json={"name": "Rename", "platform": "generic"}).json()["id"]
    post = lambda path, body: client.post("/api/projects/%s/%s" % (pid, path), json=body).json()  # noqa: E731
    post("requirements", {"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX", "PCI-DSS"]})
    post("requirements", {"statement": "The system shall mask card numbers in reports."})
    post("stories", {"title": "Audit payments", "compliance_tags": ["SOX"]})
    return pid


def _rename(client, pid, old, new):
    return client.post("/api/projects/%s/tags/rename" % pid, json={"from": old, "to": new})


def test_renaming_a_tag_changes_it_on_requirements_and_stories(client):
    pid = _setup(client)
    r = _rename(client, pid, "sox", "Sarbanes-Oxley")
    assert r.status_code == 200 and r.json() == {"requirements": 1, "stories": 1}, r.text
    assert "Sarbanes-Oxley" in client.get("/api/projects/%s/requirements" % pid).json()[0]["compliance_tags"]
    assert client.get("/api/projects/%s/stories" % pid).json()[0]["compliance_tags"] == ["Sarbanes-Oxley"]
    assert all("SOX" not in r["compliance_tags"] for r in client.get("/api/projects/%s/requirements" % pid).json())
    trail = {(e["item_key"], e["action"]) for e in client.get("/api/projects/%s/history?limit=100" % pid).json()}
    assert ("REQ-001", "edited") in trail and ("US-001", "edited") in trail


def test_renaming_into_an_existing_tag_folds_the_two_together(client):
    pid = _setup(client)
    assert _rename(client, pid, "SOX", "PCI-DSS").json()["requirements"] == 1
    assert client.get("/api/projects/%s/requirements" % pid).json()[0]["compliance_tags"] == ["PCI-DSS"], "no duplicate left behind"


def test_a_rename_needs_two_names_and_an_existing_tag(client):
    pid = _setup(client)
    assert _rename(client, pid, "SOX", "  ").status_code == 400
    assert _rename(client, pid, "", "X").status_code == 400
    assert _rename(client, pid, "HIPAA", "X").status_code == 404, "nothing carries that tag"
