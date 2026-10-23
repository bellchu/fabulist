"""The quality check says 'possible duplicate: merge or exclude one'; merging is now something the app does: one requirement keeps everything, the others are retired."""


def _setup(client):
    pid = client.post("/api/projects", json={"name": "Merge", "platform": "generic"}).json()["id"]
    ids = {}
    for key, body in (
        ("a", {"statement": "The system shall export invoices to CSV on request.", "source_ref": "BRD 1 > Billing", "actors": ["Buyer"], "compliance_tags": ["SOX"], "status": "approved"}),
        ("b", {"statement": "Invoices shall be exportable as CSV when a user asks.", "source_ref": "BRD 2 > Finance", "actors": ["Accountant", "Buyer"], "product_scope": ["Devices"]}),
        ("c", {"statement": "The system shall email order confirmations to buyers.", "source_ref": "BRD 1 > Orders"}),
    ):
        ids[key] = client.post("/api/projects/%s/requirements" % pid, json=body).json()["id"]
    return pid, ids


def _by(client, pid):
    return {r["key"]: r for r in client.get("/api/projects/%s/requirements" % pid).json()}


def test_merging_keeps_one_requirement_with_everything_and_retires_the_other(client):
    pid, ids = _setup(client)
    r = client.post("/api/projects/%s/requirements/merge" % pid, json={"keep": ids["a"], "merge": [ids["b"]]})
    assert r.status_code == 200 and r.json() == {"kept": "REQ-001", "merged": ["REQ-002"], "stories_relinked": 0}, r.text
    got = _by(client, pid)
    kept, gone = got["REQ-001"], got["REQ-002"]
    assert kept["status"] == "approved" and kept["statement"].startswith("The system shall export invoices")
    assert sorted(kept["actors"]) == ["Accountant", "Buyer"] and kept["product_scope"] == ["Devices"] and kept["compliance_tags"] == ["SOX"]
    assert "BRD 1 > Billing" in kept["source_ref"] and "BRD 2 > Finance" in kept["source_ref"], "where each was stated is kept"
    assert "REQ-002" in kept["notes"]
    assert gone["status"] == "excluded" and "REQ-001" in gone["notes"], "the other is retired, with a pointer, not deleted"


def test_stories_follow_the_merge(client):
    pid, ids = _setup(client)
    sid = client.post("/api/projects/%s/stories" % pid, json={"title": "Export", "narrative": "As a buyer I want a CSV.", "requirement_ids": ["REQ-002", "REQ-003"]}).json()["id"]
    both = client.post("/api/projects/%s/stories" % pid, json={"title": "Both", "narrative": "As a buyer I want both.", "requirement_ids": ["REQ-001", "REQ-002"]}).json()["id"]
    r = client.post("/api/projects/%s/requirements/merge" % pid, json={"keep": ids["a"], "merge": [ids["b"]]}).json()
    assert r["stories_relinked"] == 2
    stories = {s["id"]: s for s in client.get("/api/projects/%s/stories" % pid).json()}
    assert stories[sid]["requirement_ids"] == ["REQ-001", "REQ-003"]
    assert stories[both]["requirement_ids"] == ["REQ-001"], "no duplicate link when the story already had the kept one"


def test_the_trail_says_what_happened_to_each(client):
    pid, ids = _setup(client)
    client.post("/api/projects/%s/requirements/merge" % pid, json={"keep": ids["a"], "merge": [ids["b"], ids["c"]]})
    trail = [(e["item_key"], e["action"]) for e in client.get("/api/projects/%s/history?limit=100" % pid).json() if e["action"] in ("merged", "merged into")]
    assert sorted(trail) == [("REQ-001", "merged"), ("REQ-002", "merged into"), ("REQ-003", "merged into")]


def test_refusals(client):
    pid, ids = _setup(client)
    url = "/api/projects/%s/requirements/merge" % pid
    assert client.post(url, json={"keep": ids["a"], "merge": []}).status_code == 422
    assert client.post(url, json={"keep": ids["a"], "merge": [ids["a"]]}).status_code == 400, "not into itself"
    assert client.post(url, json={"keep": ids["a"], "merge": ["nope"]}).status_code == 404
    assert client.post(url, json={"keep": "nope", "merge": [ids["a"]]}).status_code == 404
    client.post(url, json={"keep": ids["a"], "merge": [ids["b"]]})
    assert client.post(url, json={"keep": ids["b"], "merge": [ids["c"]]}).status_code == 400, "an already merged requirement cannot be the survivor"
