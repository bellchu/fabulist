"""Compliance tags can be added to, or removed from, many requirements at once; each change is in the audit trail."""


def _setup(client):
    pid = client.post("/api/projects", json={"name": "Tags", "platform": "generic"}).json()["id"]
    ids = [client.post("/api/projects/%s/requirements" % pid, json=b).json()["id"] for b in (
        {"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]},
        {"statement": "The system shall mask card numbers in reports."},
        {"statement": "The system shall email receipts to buyers.", "compliance_tags": ["GDPR", "PCI-DSS"]})]
    return pid, ids


def _tags(client, pid):
    return {r["key"]: r["compliance_tags"] for r in client.get("/api/projects/%s/requirements" % pid).json()}


def test_adding_a_tag_reaches_everything_selected_without_duplicating(client):
    pid, ids = _setup(client)
    r = client.post("/api/projects/%s/requirements/tag" % pid, json={"ids": ids[:2], "tag": " SOX "})
    assert r.status_code == 200 and r.json() == {"updated": 1}, "REQ-001 already had it"
    assert _tags(client, pid) == {"REQ-001": ["SOX"], "REQ-002": ["SOX"], "REQ-003": ["GDPR", "PCI-DSS"]}
    trail = [(e["item_key"], e["action"]) for e in client.get("/api/projects/%s/history?limit=100" % pid).json()]
    assert ("REQ-002", "edited") in trail


def test_removing_a_tag_leaves_the_others(client):
    pid, ids = _setup(client)
    r = client.post("/api/projects/%s/requirements/tag" % pid, json={"ids": ids, "tag": "GDPR", "remove": True})
    assert r.json() == {"updated": 1}
    assert _tags(client, pid)["REQ-003"] == ["PCI-DSS"] and _tags(client, pid)["REQ-001"] == ["SOX"]


def test_a_blank_tag_is_refused_and_other_projects_are_untouched(client):
    pid, ids = _setup(client)
    assert client.post("/api/projects/%s/requirements/tag" % pid, json={"ids": ids, "tag": "  "}).status_code == 400
    other, other_ids = _setup(client)
    client.post("/api/projects/%s/requirements/tag" % pid, json={"ids": other_ids, "tag": "HIPAA"})
    assert "HIPAA" not in sum(_tags(client, other).values(), []), "ids from another project are ignored"


def test_stories_can_be_tagged_in_bulk_too(client):
    pid = client.post("/api/projects", json={"name": "Story tags", "platform": "generic"}).json()["id"]
    ids = [client.post("/api/projects/%s/stories" % pid, json={"title": t}).json()["id"] for t in ("Export invoices", "Email receipts")]
    assert client.post("/api/projects/%s/stories/tag" % pid, json={"ids": ids, "tag": "SOX"}).json() == {"updated": 2}
    assert client.post("/api/projects/%s/stories/tag" % pid, json={"ids": ids[:1], "tag": "SOX", "remove": True}).json() == {"updated": 1}
    tags = {s["title"]: s["compliance_tags"] for s in client.get("/api/projects/%s/stories" % pid).json()}
    assert tags == {"Export invoices": [], "Email receipts": ["SOX"]}
