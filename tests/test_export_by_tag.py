"""An auditor asks for one regulation's evidence: the export can be narrowed to a compliance tag."""
import json


def _setup(client):
    pid = client.post("/api/projects", json={"name": "Audit pack", "platform": "generic"}).json()["id"]
    post = lambda path, body: client.post("/api/projects/%s/%s" % (pid, path), json=body)  # noqa: E731
    post("requirements", {"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]})
    post("requirements", {"statement": "The system shall mask card numbers in reports.", "compliance_tags": ["PCI-DSS"]})
    post("requirements", {"statement": "The system shall email receipts to buyers."})
    post("stories", {"title": "Audit payments", "requirement_ids": ["REQ-001"]})
    post("stories", {"title": "Mask cards", "requirement_ids": ["REQ-002"]})
    post("stories", {"title": "Receipts", "requirement_ids": ["REQ-003"], "compliance_tags": ["sox"]})
    return pid


def test_the_export_can_be_narrowed_to_one_compliance_tag(client):
    pid = _setup(client)
    r = client.get("/api/projects/%s/export" % pid, params={"format": "json", "scope": "all", "tag": "sox"})
    assert r.status_code == 200, r.text
    got = json.loads(r.content)
    assert [x["key"] for x in got["requirements"]] == ["REQ-001"], "only requirements carrying the tag, ignoring case"
    assert sorted(s["title"] for s in got["stories"]) == ["Audit payments", "Receipts"], "stories that implement them, plus stories tagged themselves"


def test_a_tag_nothing_carries_is_a_clear_not_found_and_no_tag_changes_nothing(client):
    pid = _setup(client)
    r = client.get("/api/projects/%s/export" % pid, params={"format": "json", "scope": "all", "tag": "HIPAA"})
    assert r.status_code == 404
    full = json.loads(client.get("/api/projects/%s/export" % pid, params={"format": "json", "scope": "all"}).content)
    assert len(full["requirements"]) == 3 and len(full["stories"]) == 3


def test_a_frozen_baseline_can_be_narrowed_to_a_tag_too(client):
    pid = _setup(client)
    made = client.post("/api/projects/%s/baselines" % pid, json={"name": "Audit freeze", "scope": "all"})
    assert made.status_code in (200, 201), made.text
    bid = made.json()["id"]
    got = json.loads(client.get("/api/projects/%s/baselines/%s/export" % (pid, bid), params={"format": "json", "tag": "PCI-DSS"}).content)
    assert [x["key"] for x in got["requirements"]] == ["REQ-002"] and [s["title"] for s in got["stories"]] == ["Mask cards"]
    assert client.get("/api/projects/%s/baselines/%s/export" % (pid, bid), params={"format": "json", "tag": "HIPAA"}).status_code == 404
