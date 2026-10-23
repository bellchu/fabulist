"""The opposite of merging: one requirement that says several things becomes several, each keeping where it came from."""


def _setup(client):
    pid = client.post("/api/projects", json={"name": "Split", "platform": "generic"}).json()["id"]
    rid = client.post("/api/projects/%s/requirements" % pid, json={
        "statement": "The system shall export invoices to CSV and email them to the buyer.", "source_ref": "BRD 1 > Billing", "epic": "Billing",
        "section": "Invoices", "actors": ["Buyer"], "compliance_tags": ["SOX"], "status": "approved", "assignee": ""}).json()["id"]
    return pid, rid


def _by(client, pid):
    return {r["key"]: r for r in client.get("/api/projects/%s/requirements" % pid).json()}


def test_splitting_keeps_the_first_part_in_place_and_creates_the_rest_with_the_same_context(client):
    pid, rid = _setup(client)
    r = client.post("/api/projects/%s/requirements/%s/split" % (pid, rid), json={"statements": [
        "The system shall export invoices to CSV on request.", "The system shall email invoices to the buyer."]})
    assert r.status_code == 200 and r.json() == {"kept": "REQ-001", "created": ["REQ-002"]}, r.text
    got = _by(client, pid)
    assert got["REQ-001"]["statement"] == "The system shall export invoices to CSV on request." and got["REQ-001"]["status"] == "approved"
    new = got["REQ-002"]
    assert new["statement"] == "The system shall email invoices to the buyer." and new["status"] == "draft", "a new part has not been reviewed"
    assert (new["epic"], new["section"], new["actors"], new["compliance_tags"], new["source_ref"]) == ("Billing", "Invoices", ["Buyer"], ["SOX"], "BRD 1 > Billing")
    assert "REQ-001" in new["notes"], "it says where it was split from"
    trail = {(e["item_key"], e["action"]) for e in client.get("/api/projects/%s/history?limit=100" % pid).json()}
    assert ("REQ-001", "edited") in trail and ("REQ-002", "created") in trail


def test_a_split_needs_two_real_statements_and_an_existing_requirement(client):
    pid, rid = _setup(client)
    url = "/api/projects/%s/requirements/%s/split" % (pid, rid)
    assert client.post(url, json={"statements": ["Only one statement here, long enough."]}).status_code == 422
    assert client.post(url, json={"statements": ["Long enough statement one.", "short"]}).status_code == 400
    assert client.post("/api/projects/%s/requirements/nope/split" % pid, json={"statements": ["Long enough statement one.", "Long enough statement two."]}).status_code == 404
    assert len(_by(client, pid)) == 1, "a refused split changes nothing"
