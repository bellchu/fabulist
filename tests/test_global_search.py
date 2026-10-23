"""Search across every project the person may see: requirements and stories by their words, projects by name."""
from app import config, users

ROOT = ("root", "root-password-123")


def _project(client, name, statements=(), auth=None, **extra):
    pid = client.post("/api/projects", json=dict({"name": name, "platform": "generic"}, **extra), auth=auth).json()["id"]
    for s in statements:
        client.post("/api/projects/%s/requirements" % pid, json={"statement": s}, auth=auth)
    return pid


def _search(client, q, auth=None, **params):
    return client.get("/api/search", params=dict({"q": q}, **params), auth=auth)


def test_requirements_are_found_in_every_project_with_where_they_live(client):
    a = _project(client, "Billing", ["The system shall export invoices to CSV on request.", "Orders shall be confirmed within 3 seconds."])
    b = _project(client, "Portal", ["Users shall download their INVOICES as PDF."])
    r = _search(client, "invoices").json()
    found = {(x["project_name"], x["key"]) for x in r["requirements"]}
    assert found == {("Billing", "REQ-001"), ("Portal", "REQ-001")}
    hit = next(x for x in r["requirements"] if x["project_id"] == a)
    assert hit["statement"].startswith("The system shall export invoices") and hit["status"] == "draft"
    assert {x["project_id"] for x in r["requirements"]} == {a, b}


def test_projects_are_found_by_name_and_wildcards_are_plain_text(client):
    _project(client, "Order to cash", ["Prices shall include 100% of the tax."])
    _project(client, "Other", ["Totally unrelated words appear here."])
    assert [p["name"] for p in _search(client, "cash").json()["projects"]] == ["Order to cash"]
    assert len(_search(client, "100%").json()["requirements"]) == 1, "a % in the query is not a wildcard"
    assert _search(client, "%%").json()["requirements"] == [], "and does not match everything"
    assert _search(client, "un_elated").json()["requirements"] == [], "nor does an underscore"


def test_stories_are_found_by_title_or_narrative(client):
    pid = _project(client, "Stories")
    client.post("/api/projects/%s/stories" % pid, json={"title": "Export invoices", "narrative": "As a buyer I want a CSV so that I can reconcile.", "persona": "Buyer"})
    r = _search(client, "reconcile").json()
    assert [(s["key"], s["title"]) for s in r["stories"]] == [("US-001", "Export invoices")] and r["stories"][0]["project_id"] == pid


def test_short_queries_are_refused_and_results_are_bounded(client):
    _project(client, "Many", ["The system shall support capability number %d for buyers." % i for i in range(60)])
    assert _search(client, "x").status_code == 400
    assert len(_search(client, "capability").json()["requirements"]) == 30, "30 by default"
    assert len(_search(client, "capability", limit=5).json()["requirements"]) == 5
    assert len(_search(client, "capability", limit=500).json()["requirements"]) == 50, "never more than 50"


def test_archived_projects_are_left_out(client):
    pid = _project(client, "Old", ["The system shall retire the legacy export."])
    client.post("/api/projects/%s/archive" % pid, json={})
    r = _search(client, "legacy").json()
    assert r["requirements"] == [] and r["projects"] == []


def test_private_projects_are_invisible_to_people_who_were_not_chosen(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("bob", "analyst"), ("vic", "viewer")):
        client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT)
    ana, bob, vic = ("ana", "ana-password-1"), ("bob", "bob-password-1"), ("vic", "vic-password-1")
    secret = _project(client, "Secret programme", ["The system shall protect the confidential merger data."], auth=ana, visibility="members")
    _project(client, "Open programme", ["The system shall publish the open merger FAQ."], auth=ana)
    seen = lambda who: sorted(x["project_name"] for x in _search(client, "merger", auth=who).json()["requirements"])  # noqa: E731
    assert seen(ana) == ["Open programme", "Secret programme"] and seen(ROOT) == ["Open programme", "Secret programme"]
    assert seen(bob) == ["Open programme"] and seen(vic) == ["Open programme"], "no hint that the other one exists"
    assert [p["name"] for p in _search(client, "Secret", auth=bob).json()["projects"]] == []
    client.put("/api/projects/%s/access" % secret, json={"visibility": "members", "members": ["bob"]}, auth=ana)
    assert seen(bob) == ["Open programme", "Secret programme"]


def test_a_compliance_tag_finds_every_requirement_and_story_that_carries_it(client):
    a = _project(client, "Billing")
    b = _project(client, "Portal")
    client.post("/api/projects/%s/requirements" % a, json={"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]})
    client.post("/api/projects/%s/requirements" % a, json={"statement": "The system shall mask card numbers in reports."})
    client.post("/api/projects/%s/stories" % b, json={"title": "Audit portal logins", "compliance_tags": ["SOX"]})
    r = _search(client, "sox").json()
    assert [(x["project_name"], x["key"]) for x in r["requirements"]] == [("Billing", "REQ-001")]
    assert [(x["project_name"], x["key"]) for x in r["stories"]] == [("Portal", "US-001")]
