"""The audit trail can be narrowed to one item by its key (REQ-002, US-001), so 'what happened to this?' has an answer without scrolling."""


def test_the_trail_can_be_filtered_to_one_item_by_key(client):
    pid = client.post("/api/projects", json={"name": "Trail", "platform": "generic"}).json()["id"]
    a = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall log every payment attempt."}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall mask card numbers in reports."})
    client.patch("/api/projects/%s/requirements/%s" % (pid, a), json={"status": "approved"})
    got = client.get("/api/projects/%s/history?item=req-001" % pid).json()
    assert got and {e["item_key"] for e in got} == {"REQ-001"}, "case does not matter"
    assert [e["action"] for e in got] == ["status changed", "created"]
    assert client.get("/api/projects/%s/history?item=REQ-009" % pid).json() == []
    assert len(client.get("/api/projects/%s/history" % pid).json()) >= 3
