import pytest

from app import users

ROOT = ("root", "root-password-123")


@pytest.fixture()
def secured(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BASIC_USER", ROOT[0])
    monkeypatch.setattr(config, "BASIC_PASSWORD", ROOT[1])
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("vic", "viewer"), ("adm", "admin")):
        r = client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT)
        assert r.status_code == 201, r.text
    return client


def creds(name):
    return (name, name + "-password-1")


def test_password_hashing():
    h = users.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert users.verify_password("correct horse battery", h) and not users.verify_password("wrong", h)
    assert not users.verify_password("x", "garbage")


def test_roles_are_enforced(secured):
    c = secured
    assert c.get("/api/me", auth=creds("ana")).json() == {"username": "ana", "role": "analyst", "sso": False, "named": True}
    assert c.get("/api/projects", auth=("ana", "wrong-password!!")).status_code == 401
    # analyst: projects yes, admin no
    pid = c.post("/api/projects", json={"name": "P", "platform": "generic"}, auth=creds("ana")).json()["id"]
    assert c.get("/api/admin/overview", auth=creds("ana")).status_code == 403
    assert c.get("/api/integrations", auth=creds("ana")).status_code == 200
    # viewer: read-only
    assert c.get("/api/projects/%s" % pid, auth=creds("vic")).status_code == 200
    assert c.post("/api/projects", json={"name": "Q", "platform": "generic"}, auth=creds("vic")).status_code == 403
    assert c.delete("/api/projects/%s" % pid, auth=creds("vic")).status_code == 403
    # named admin and bootstrap admin
    assert c.get("/api/admin/overview", auth=creds("adm")).status_code == 200
    assert c.get("/api/admin/users", auth=ROOT).json()["bootstrap_user"] == "root"


def test_user_admin_lifecycle(secured):
    c = secured
    listing = c.get("/api/admin/users", auth=ROOT).json()
    assert {u["username"] for u in listing["users"]} == {"ana", "vic", "adm"}
    assert "password_hash" not in str(listing)
    assert c.post("/api/admin/users", json={"username": "ana", "role": "analyst", "password": "x" * 12}, auth=ROOT).status_code == 409
    assert c.post("/api/admin/users", json={"username": "bob", "role": "analyst", "password": "short"}, auth=ROOT).status_code == 400
    assert c.post("/api/admin/users", json={"username": "root", "role": "analyst", "password": "x" * 12}, auth=ROOT).status_code == 400
    ana = next(u for u in listing["users"] if u["username"] == "ana")
    # promote, then disable: cached credentials must not outlive the change
    assert c.get("/api/admin/overview", auth=creds("ana")).status_code == 403
    assert c.patch("/api/admin/users/%s" % ana["id"], json={"role": "admin"}, auth=ROOT).json()["role"] == "admin"
    assert c.get("/api/admin/overview", auth=creds("ana")).status_code == 200
    c.patch("/api/admin/users/%s" % ana["id"], json={"disabled": True}, auth=ROOT)
    assert c.get("/api/projects", auth=creds("ana")).status_code == 401
    c.patch("/api/admin/users/%s" % ana["id"], json={"disabled": False, "password": "new-password-123"}, auth=ROOT)
    assert c.get("/api/projects", auth=creds("ana")).status_code == 401
    assert c.get("/api/projects", auth=("ana", "new-password-123")).status_code == 200
    assert c.delete("/api/admin/users/%s" % ana["id"], auth=ROOT).status_code == 204
    assert c.get("/api/projects", auth=("ana", "new-password-123")).status_code == 401


def test_failed_logins_are_throttled(secured, monkeypatch):
    monkeypatch.setattr(users, "FAIL_LIMIT", 3)
    users._failures.clear()
    for _ in range(3):
        assert secured.get("/api/projects", auth=("ana", "wrong-password!!")).status_code == 401
    r = secured.get("/api/projects", auth=creds("ana"))
    assert r.status_code == 429 and r.headers["Retry-After"]
    users._failures.clear()
    assert secured.get("/api/projects", auth=creds("ana")).status_code == 200


def test_security_headers(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/api/meta").headers["X-Frame-Options"] == "DENY"



def test_viewers_can_discuss_but_not_edit(secured):
    c = secured
    pid = c.post("/api/projects", json={"name": "Review", "platform": "generic"}, auth=creds("ana")).json()["id"]
    req = c.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}, auth=creds("ana")).json()
    com = c.post("/api/projects/%s/comments" % pid, json={"item_id": req["id"], "body": "Which CSV columns does Finance need?"}, auth=creds("vic"))
    assert com.status_code == 201 and com.json()["author"] == "vic"
    assert c.patch("/api/projects/%s/requirements/%s" % (pid, req["id"]), json={"status": "approved"}, auth=creds("vic")).status_code == 403
    assert c.delete("/api/projects/%s/comments/%s" % (pid, com.json()["id"]), auth=creds("vic")).status_code == 403, "viewers cannot delete"
    assert c.delete("/api/projects/%s/comments/%s" % (pid, com.json()["id"]), auth=creds("ana")).status_code == 403, "only author or admin"
    r = c.post("/api/projects/%s/comments/%s/resolve" % (pid, com.json()["id"]), auth=creds("ana")).json()
    assert r["resolved"] and r["resolved_by"] == "ana"
