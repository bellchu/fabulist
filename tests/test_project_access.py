"""A project can be private to chosen people. The check is one guard on every route that names a project, so nothing that is added later can
forget it, and a person who may not see a project gets 'not found', not 'forbidden'."""
import pytest

from app import config

ROOT = ("root", "root-password-123")
ALICE, BOB, CAROL = ("alice", "alice-password-1"), ("bob", "bob-password-123"), ("carol", "carol-password-1")


@pytest.fixture()
def team(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", ROOT[1])
    for name, role, who in (("alice", "analyst", ALICE), ("bob", "analyst", BOB), ("carol", "viewer", CAROL)):
        assert client.post("/api/admin/users", json={"username": name, "role": role, "password": who[1]}, auth=ROOT).status_code == 201
    return client


def _private(client, who=ALICE, members=None):
    r = client.post("/api/projects", json={"name": "Secret programme", "platform": "generic", "visibility": "members"}, auth=who)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall keep this requirement confidential."}, auth=who)
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall do a confidential thing."}, auth=who)
    if members is not None:
        assert client.put("/api/projects/%s/access" % pid, json={"visibility": "members", "members": members}, auth=who).status_code == 200
    return pid


def test_a_private_project_is_invisible_to_people_who_were_not_chosen(team):
    pid = _private(team)
    names = lambda who: [p["name"] for p in team.get("/api/projects", auth=who).json()]  # noqa: E731
    assert names(ALICE) == ["Secret programme"] and names(ROOT) == ["Secret programme"], "the creator and administrators see it"
    assert names(BOB) == [] and names(CAROL) == [], "nobody else does"
    base = "/api/projects/%s" % pid
    for path in ("", "/requirements", "/stories", "/documents", "/runs", "/comments", "/history", "/baselines", "/archive.json", "/export?format=csv", "/access"):
        assert team.get(base + path, auth=BOB).status_code == 404, "bob must not read %s" % path
    assert team.post(base + "/search", json={"query": "confidential"}, auth=BOB).status_code == 404
    assert team.post(base + "/requirements", json={"statement": "The system shall add something sneaky here."}, auth=BOB).status_code == 404
    assert team.post(base + "/archive", json={}, auth=BOB).status_code == 404 and team.delete(base, auth=BOB).status_code == 404
    assert team.get(base, auth=CAROL).status_code == 404, "a viewer who was not chosen is shut out too"
    assert team.get(base, auth=ALICE).status_code == 200 and team.get(base, auth=ROOT).status_code == 200


def test_choosing_people_opens_the_project_to_them_and_only_them(team):
    pid = _private(team)
    base = "/api/projects/%s" % pid
    r = team.put(base + "/access", json={"visibility": "members", "members": ["bob", "carol"]}, auth=ALICE)
    assert r.status_code == 200 and sorted(r.json()["members"]) == ["alice", "bob", "carol"], "the person making the change always stays in"
    assert team.get(base + "/requirements", auth=BOB).status_code == 200
    assert team.get(base + "/requirements", auth=CAROL).status_code == 200, "a chosen viewer can read"
    assert team.post(base + "/requirements", json={"statement": "The system shall let a viewer write, which it must not."}, auth=CAROL).status_code == 403, "but a viewer is still read-only"
    assert [p["name"] for p in team.get("/api/projects", auth=BOB).json()] == ["Secret programme"]
    team.put(base + "/access", json={"visibility": "members", "members": ["alice"]}, auth=ALICE)
    assert team.get(base, auth=BOB).status_code == 404, "removed again"


def test_making_a_project_visible_to_everyone_again(team):
    pid = _private(team)
    assert team.get("/api/projects/%s" % pid, auth=BOB).status_code == 404
    assert team.put("/api/projects/%s/access" % pid, json={"visibility": "everyone", "members": []}, auth=ALICE).status_code == 200
    assert team.get("/api/projects/%s" % pid, auth=BOB).status_code == 200
    assert team.get("/api/projects/%s/access" % pid, auth=BOB).json()["visibility"] == "everyone"


def test_access_changes_are_validated_and_recorded(team):
    pid = _private(team)
    base = "/api/projects/%s/access" % pid
    assert team.put(base, json={"visibility": "members", "members": ["nobody-by-that-name"]}, auth=ALICE).status_code == 400
    assert team.put(base, json={"visibility": "secret", "members": []}, auth=ALICE).status_code == 400
    assert team.put(base, json={"visibility": "members", "members": ["bob"]}, auth=BOB).status_code == 404, "someone who cannot see the project cannot change who can"
    team.put(base, json={"visibility": "members", "members": ["bob"]}, auth=ALICE)
    history = team.get("/api/projects/%s/history" % pid, auth=ALICE).json()
    assert any(h["action"] == "access changed" and h["actor"] == "alice" for h in history), [h["action"] for h in history]
    assert [p["username"] for p in team.get("/api/people", auth=BOB).json()] == ["alice", "bob", "carol"], "the picker lists named accounts"


def test_an_ordinary_project_is_visible_to_everyone_as_before(team):
    pid = team.post("/api/projects", json={"name": "Open programme", "platform": "generic"}, auth=ALICE).json()["id"]
    assert team.get("/api/projects/%s" % pid, auth=BOB).status_code == 200
    assert "Open programme" in [p["name"] for p in team.get("/api/projects", auth=CAROL).json()]
    assert team.get("/api/projects/%s" % pid, auth=BOB).json()["visibility"] == "everyone"


def test_without_named_accounts_nothing_is_hidden(client):
    pid = client.post("/api/projects", json={"name": "Local", "platform": "generic", "visibility": "members"}).json()["id"]
    assert client.get("/api/projects/%s" % pid).status_code == 200, "no sign-in configured: everyone is the administrator"
    assert [p["name"] for p in client.get("/api/projects").json()] == ["Local"]
