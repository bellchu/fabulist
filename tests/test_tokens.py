"""Personal API tokens for automation (Bearer fab_...)."""
import pytest

from app import config, db, users

ROOT = ("root", "root-password-123")


@pytest.fixture()
def accounts(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._failures.clear()
    for name, role in (("analyst1", "analyst"), ("viewer1", "viewer"), ("admin2", "admin")):
        client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT)
    yield {n: (n, n + "-password-1") for n in ("analyst1", "viewer1", "admin2")}
    users._failures.clear()  # the throttling test must not lock out later tests from the same client


def _bearer(token, csrf=False):
    h = {"Authorization": "Bearer " + token}
    if not csrf:
        h["X-Requested-With"] = ""  # automation clients do not send the browser CSRF header
    return h


def _new(client, auth, name="ci", **extra):
    r = client.post("/api/me/tokens", json=dict({"name": name}, **extra), auth=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_token_is_shown_once_stored_hashed_and_listed_without_the_secret(client, accounts):
    t = _new(client, accounts["analyst1"])
    assert t["token"].startswith("fab_") and len(t["token"]) > 40 and t["expires"]
    row = db.one("SELECT * FROM api_tokens WHERE id=%s", (t["id"],))
    assert t["token"] not in row.values() and row["token_hash"] == users.hash_token(t["token"])
    listed = client.get("/api/me/tokens", auth=accounts["analyst1"]).json()
    assert [x["name"] for x in listed] == ["ci"] and "token" not in listed[0] and listed[0]["prefix"] == t["token"][:8]
    assert client.get("/api/me/tokens", auth=accounts["viewer1"]).json() == []  # each user sees only their own


def test_bearer_authenticates_with_the_owners_role_and_without_the_csrf_header(client, accounts):
    t = _new(client, accounts["analyst1"])["token"]
    assert client.get("/api/me", headers=_bearer(t)).json()["username"] == "analyst1"
    assert client.post("/api/projects", json={"name": "via token", "platform": "generic"}, headers=_bearer(t)).status_code == 201
    assert client.get("/api/admin/users", headers=_bearer(t)).status_code == 403  # analyst role still applies
    assert db.one("SELECT last_used FROM api_tokens WHERE name='ci'")["last_used"]
    viewer = _new(client, accounts["viewer1"], "report")["token"]
    assert client.get("/api/projects", headers=_bearer(viewer)).status_code == 200
    assert client.post("/api/projects", json={"name": "x", "platform": "generic"}, headers=_bearer(viewer)).status_code == 403
    admin = _new(client, accounts["admin2"], "ops")["token"]
    assert client.get("/api/admin/users", headers=_bearer(admin)).status_code == 200


def test_bad_revoked_expired_and_disabled_tokens_are_rejected(client, accounts):
    assert client.get("/api/projects").status_code == 401
    bad = client.get("/api/projects", headers=_bearer("fab_" + "x" * 43))
    assert bad.status_code == 401 and "Bearer" in bad.headers["www-authenticate"]
    t = _new(client, accounts["analyst1"])
    assert client.get("/api/projects", headers=_bearer(t["token"])).status_code == 200
    assert client.delete("/api/me/tokens/%s" % t["id"], auth=accounts["viewer1"]).status_code == 404  # not theirs
    assert client.delete("/api/me/tokens/%s" % t["id"], auth=accounts["analyst1"]).status_code == 204
    assert client.get("/api/projects", headers=_bearer(t["token"])).status_code == 401
    e = _new(client, accounts["analyst1"], "old")
    db.execute("UPDATE api_tokens SET expires='2000-01-01T00:00:00+00:00' WHERE id=%s", (e["id"],))
    assert client.get("/api/projects", headers=_bearer(e["token"])).status_code == 401
    d = _new(client, accounts["analyst1"], "disabled-owner")
    db.execute("UPDATE users SET disabled=TRUE WHERE username='analyst1'")
    assert client.get("/api/projects", headers=_bearer(d["token"])).status_code == 401


def test_admin_can_list_and_revoke_any_token_and_deleting_a_user_removes_theirs(client, accounts):
    t = _new(client, accounts["analyst1"])
    rows = client.get("/api/admin/tokens", auth=ROOT).json()
    assert rows[0]["owner"] == "analyst1" and "token" not in rows[0]
    assert client.get("/api/admin/tokens", auth=accounts["analyst1"]).status_code == 403
    assert client.delete("/api/admin/tokens/%s" % t["id"], auth=ROOT).status_code == 204
    assert client.get("/api/projects", headers=_bearer(t["token"])).status_code == 401
    keep = _new(client, accounts["analyst1"], "gone-with-user")
    uid = db.one("SELECT id FROM users WHERE username='analyst1'")["id"]
    assert client.delete("/api/admin/users/%s" % uid, auth=ROOT).status_code == 204
    assert client.get("/api/projects", headers=_bearer(keep["token"])).status_code == 401


def test_token_creation_validates_input_and_needs_a_named_account(client, accounts):
    a = accounts["analyst1"]
    assert client.post("/api/me/tokens", json={"name": ""}, auth=a).status_code == 400
    assert client.post("/api/me/tokens", json={"name": "x" * 81}, auth=a).status_code == 400
    assert client.post("/api/me/tokens", json={"name": "ok", "days": 0}, auth=a).status_code == 400
    assert client.post("/api/me/tokens", json={"name": "ok", "days": 366}, auth=a).status_code == 400
    assert client.post("/api/me/tokens", json={"name": "ok", "days": 30}, auth=a).status_code == 201
    r = client.post("/api/me/tokens", json={"name": "bootstrap"}, auth=ROOT)
    assert r.status_code == 400 and "named account" in r.json()["detail"]
    for i in range(20):
        client.post("/api/me/tokens", json={"name": "t%d" % i}, auth=a)
    assert client.post("/api/me/tokens", json={"name": "one-too-many"}, auth=a).status_code == 400  # per-user cap


def test_repeated_bad_tokens_are_throttled_per_client(client, accounts):
    for _ in range(users.FAIL_LIMIT):
        assert client.get("/api/projects", headers=_bearer("fab_nope")).status_code == 401
    assert client.get("/api/projects", headers=_bearer("fab_nope")).status_code == 429
