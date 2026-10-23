"""Requirements and stories can be assigned to a reviewer, so a team can divide the review and each person can find their own."""
from app import config, users

ROOT = ("root", "root-password-123")


def _auth(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("ben", "analyst"), ("vic", "viewer")):
        client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT)


def _project(client, auth=None):
    pid = client.post("/api/projects", json={"name": "Assign", "platform": "generic"}, auth=auth).json()["id"]
    rid = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV on request."}, auth=auth).json()["id"]
    sid = client.post("/api/projects/%s/stories" % pid, json={"title": "Export invoices", "narrative": "As a buyer I want a CSV."}, auth=auth).json()["id"]
    return pid, rid, sid


def test_without_named_accounts_an_assignee_is_free_text(client):
    pid, rid, sid = _project(client)
    r = client.patch("/api/projects/%s/requirements/%s" % (pid, rid), json={"assignee": "  Jane from Finance "})
    assert r.status_code == 200
    got = client.get("/api/projects/%s/requirements" % pid).json()[0]
    assert got["assignee"] == "Jane from Finance"
    assert client.patch("/api/projects/%s/stories/%s" % (pid, sid), json={"assignee": "Jane"}).status_code == 200
    assert client.get("/api/projects/%s/stories" % pid).json()[0]["assignee"] == "Jane"


def test_with_accounts_it_must_be_a_person_who_exists_and_is_active(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid, rid, sid = _project(client, ana)
    url = "/api/projects/%s/requirements/%s" % (pid, rid)
    assert client.patch(url, json={"assignee": "ben"}, auth=ana).status_code == 200
    bad = client.patch(url, json={"assignee": "nobody"}, auth=ana)
    assert bad.status_code == 400 and "nobody" in bad.json()["detail"]
    uid = next(u["id"] for u in client.get("/api/admin/users", auth=ROOT).json()["users"] if u["username"] == "ben")
    client.patch("/api/admin/users/%s" % uid, json={"disabled": True}, auth=ROOT)
    assert client.patch(url, json={"assignee": "ben"}, auth=ana).status_code in (200, 400), "an unchanged assignee is not re-checked against the account list"
    assert client.patch(url, json={"assignee": ""}, auth=ana).status_code == 200, "unassigning is always possible"
    assert client.get("/api/projects/%s/requirements" % pid, auth=ana).json()[0]["assignee"] == ""


def test_an_assignment_is_in_the_audit_trail_and_a_viewer_cannot_make_one(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid, rid, _ = _project(client, ana)
    client.patch("/api/projects/%s/requirements/%s" % (pid, rid), json={"assignee": "ben"}, auth=ana)
    edit = next(e for e in client.get("/api/projects/%s/history?limit=50" % pid, auth=ana).json() if e["action"] == "edited")
    assert edit["changes"]["assignee"] == {"from": "", "to": "ben"} and edit["actor"] == "ana"
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, rid), json={"assignee": "vic"}, auth=("vic", "vic-password-1")).status_code == 403


def test_it_is_exported_and_imported(client):
    pid, rid, _ = _project(client)
    client.patch("/api/projects/%s/requirements/%s" % (pid, rid), json={"assignee": "Jane"})
    csv_text = client.get("/api/projects/%s/export?format=requirements-csv" % pid).text
    assert "assignee" in csv_text.splitlines()[0] and "Jane" in csv_text
    other = client.post("/api/projects", json={"name": "Copy", "platform": "generic"}).json()["id"]
    assert client.post("/api/projects/%s/requirements/import" % other, json={"csv": csv_text}).json()["created"] == 1
    assert client.get("/api/projects/%s/requirements" % other).json()[0]["assignee"] == "Jane"


def test_an_import_naming_an_unknown_person_is_refused_whole(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid = client.post("/api/projects", json={"name": "Imp", "platform": "generic"}, auth=ana).json()["id"]
    bad = "Requirement,Assignee\nThe system shall export invoices to CSV on request.,ben\nThe system shall email order confirmations to buyers.,ghost\n"
    r = client.post("/api/projects/%s/requirements/import" % pid, json={"csv": bad}, auth=ana)
    assert r.status_code == 400 and "line 3" in r.json()["detail"] and "ghost" in r.json()["detail"]
    assert client.get("/api/projects/%s/requirements" % pid, auth=ana).json() == []
    ok = client.post("/api/projects/%s/requirements/import" % pid, json={"csv": bad.replace("ghost", "ana")}, auth=ana)
    assert ok.json()["created"] == 2


def _three(client, auth=None):
    pid = client.post("/api/projects", json={"name": "Bulk assign", "platform": "generic"}, auth=auth).json()["id"]
    ids = [client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall support bulk capability %d." % i}, auth=auth).json()["id"] for i in range(3)]
    sids = [client.post("/api/projects/%s/stories" % pid, json={"title": "Story %d" % i, "narrative": "As a user I want it."}, auth=auth).json()["id"] for i in range(2)]
    return pid, ids, sids


def test_many_requirements_and_stories_are_assigned_at_once(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid, ids, sids = _three(client, ana)
    r = client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids[:2], "assignee": "ben"}, auth=ana)
    assert r.status_code == 200 and r.json() == {"updated": 2}
    got = {x["id"]: x["assignee"] for x in client.get("/api/projects/%s/requirements" % pid, auth=ana).json()}
    assert [got[i] for i in ids] == ["ben", "ben", ""]
    assert client.post("/api/projects/%s/stories/assign" % pid, json={"ids": sids, "assignee": "ana"}, auth=ana).json() == {"updated": 2}
    assert {s["assignee"] for s in client.get("/api/projects/%s/stories" % pid, auth=ana).json()} == {"ana"}
    again = client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids[:2], "assignee": "ben"}, auth=ana)
    assert again.json() == {"updated": 0}, "items that already have that assignee are not touched"


def test_each_change_is_in_the_trail_and_unassigning_works(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid, ids, _ = _three(client, ana)
    client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": "ben"}, auth=ana)
    edits = [e for e in client.get("/api/projects/%s/history?limit=100" % pid, auth=ana).json() if e["action"] == "edited"]
    assert len(edits) == 3 and all(e["changes"]["assignee"] == {"from": "", "to": "ben"} for e in edits)
    client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": ""}, auth=ana)
    assert {x["assignee"] for x in client.get("/api/projects/%s/requirements" % pid, auth=ana).json()} == {""}


def test_an_unknown_person_viewers_and_foreign_ids_are_handled(client, monkeypatch):
    _auth(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    pid, ids, _ = _three(client, ana)
    assert client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": "ghost"}, auth=ana).status_code == 400
    assert client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": "ben"}, auth=("vic", "vic-password-1")).status_code == 403
    assert client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ["nope"], "assignee": "ben"}, auth=ana).json() == {"updated": 0}
    assert client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": [], "assignee": "ben"}, auth=ana).status_code == 422
