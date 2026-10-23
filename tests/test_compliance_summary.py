"""The overview shows, per compliance tag, how many requirements carry it and how many of those a story already covers."""


def test_the_project_summarises_each_compliance_tag_with_its_story_coverage(client):
    pid = client.post("/api/projects", json={"name": "Compliance", "platform": "generic"}).json()["id"]
    post = lambda body: client.post("/api/projects/%s/requirements" % pid, json=body).json()  # noqa: E731
    post({"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX", "PCI-DSS"]})
    post({"statement": "The system shall mask card numbers in reports.", "compliance_tags": ["PCI-DSS"]})
    post({"statement": "The system shall email receipts to buyers.", "compliance_tags": ["SOX"], "status": "excluded"})
    post({"statement": "The system shall export invoices to CSV."})
    client.post("/api/projects/%s/stories" % pid, json={"title": "Audit payments", "requirement_ids": ["REQ-001"]})
    got = client.get("/api/projects/%s" % pid).json()["compliance"]
    assert got == {"PCI-DSS": {"requirements": 2, "covered": 1}, "SOX": {"requirements": 1, "covered": 1}}, "excluded requirements do not count"


def test_a_project_without_tags_has_an_empty_summary(client):
    pid = client.post("/api/projects", json={"name": "Plain", "platform": "generic"}).json()["id"]
    assert client.get("/api/projects/%s" % pid).json()["compliance"] == {}
