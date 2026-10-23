"""One view of every compliance tag across the projects a person may see: where it applies and how much of it a story covers."""
from app import config, users

ROOT = ("root", "root-password-123")


def _project(client, name, reqs, auth=None, **extra):
    pid = client.post("/api/projects", json=dict({"name": name, "platform": "generic"}, **extra), auth=auth).json()["id"]
    for statement, tags, status in reqs:
        client.post("/api/projects/%s/requirements" % pid, json={"statement": statement, "compliance_tags": tags, "status": status}, auth=auth)
    return pid


def test_each_tag_is_summed_across_projects_with_the_stories_that_cover_it(client):
    a = _project(client, "Billing", [("The system shall log every payment attempt.", ["SOX"], "draft"), ("The system shall mask card numbers in reports.", ["PCI-DSS"], "draft")])
    b = _project(client, "Portal", [("The system shall log every portal login.", ["sox"], "draft"), ("The system shall keep old data for 7 years.", ["SOX"], "excluded")])
    client.post("/api/projects/%s/stories" % a, json={"title": "Audit payments", "requirement_ids": ["REQ-001"]})
    got = client.get("/api/compliance").json()["tags"]
    sox = next(t for t in got if t["tag"].lower() == "sox")
    assert (sox["requirements"], sox["covered"]) == (2, 1), "sox and SOX are one tag; the excluded requirement does not count"
    assert sorted((p["name"], p["requirements"], p["covered"]) for p in sox["projects"]) == [("Billing", 1, 1), ("Portal", 1, 0)]
    pci = next(t for t in got if t["tag"] == "PCI-DSS")
    assert (pci["requirements"], pci["covered"]) == (1, 0) and [p["id"] for p in pci["projects"]] == [a]
    assert b and [t["tag"].lower() for t in got] == sorted(t["tag"].lower() for t in got), "sorted by tag"


def test_archived_projects_are_left_out(client):
    pid = _project(client, "Old programme", [("The system shall log every payment attempt.", ["SOX"], "draft")])
    client.post("/api/projects/%s/archive" % pid, json={})
    assert not [t for t in client.get("/api/compliance").json()["tags"] if t["tag"] == "SOX"]


def test_private_projects_count_only_for_their_members_and_administrators(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("bob", "analyst")):
        client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT)
    ana, bob = ("ana", "ana-password-1"), ("bob", "bob-password-1")
    _project(client, "Secret programme", [("The system shall protect the merger data.", ["HIPAA"], "draft")], auth=ana, visibility="members")
    tags = lambda who: [t["tag"] for t in client.get("/api/compliance", auth=who).json()["tags"]]  # noqa: E731
    assert "HIPAA" in tags(ana) and "HIPAA" in tags(ROOT)
    assert "HIPAA" not in tags(bob), "no hint that the tag exists elsewhere"
