"""People can change their own password (named accounts, signed in with Basic credentials), proving they know the current one."""
from app import config, users

ROOT = ("root", "root-password-123")


def _setup(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("vic", "viewer")):
        assert client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT).status_code == 201


def _change(client, who, current, new, **kw):
    return client.post("/api/me/password", json={"current_password": current, "new_password": new}, auth=who, **kw)


def test_a_person_changes_their_own_password(client, monkeypatch):
    _setup(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    r = _change(client, ana, "ana-password-1", "a-brand-new-password")
    assert r.status_code == 200, r.text
    assert client.get("/api/me", auth=ana).status_code == 401, "the old password stops working at once"
    assert client.get("/api/me", auth=("ana", "a-brand-new-password")).json()["username"] == "ana"


def test_every_role_can_do_it_including_viewers(client, monkeypatch):
    _setup(client, monkeypatch)
    assert _change(client, ("vic", "vic-password-1"), "vic-password-1", "viewer-new-password-9").status_code == 200


def test_the_current_password_must_be_right(client, monkeypatch):
    _setup(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    r = _change(client, ana, "not-my-password!!", "a-brand-new-password")
    assert r.status_code == 403 and "current password is wrong" in r.json()["detail"]
    assert client.get("/api/me", auth=ana).status_code == 200, "nothing changed"


def test_the_new_password_must_be_acceptable_and_different(client, monkeypatch):
    _setup(client, monkeypatch)
    ana = ("ana", "ana-password-1")
    assert _change(client, ana, "ana-password-1", "short").status_code == 400
    assert _change(client, ana, "ana-password-1", "ana-password-1").status_code == 400
    assert client.get("/api/me", auth=ana).status_code == 200, "nothing changed"


def test_the_bootstrap_login_and_tokens_cannot_use_it(client, monkeypatch):
    _setup(client, monkeypatch)
    assert _change(client, ROOT, "root-password-123", "another-root-password").status_code == 400, "set in the deployment, not in the app"
    token = client.post("/api/me/tokens", json={"name": "t", "days": 1}, auth=("ana", "ana-password-1")).json()["token"]
    r = client.post("/api/me/password", json={"current_password": "ana-password-1", "new_password": "token-made-password"}, headers={"Authorization": "Bearer " + token})
    assert r.status_code == 403, "a leaked token must not be able to take over the account"


def test_it_is_in_the_administrative_trail_without_the_passwords(client, monkeypatch):
    _setup(client, monkeypatch)
    _change(client, ("ana", "ana-password-1"), "ana-password-1", "a-brand-new-password")
    rows = [e for e in client.get("/api/admin/audit", auth=ROOT).json() if e["action"] == "password changed"]
    assert len(rows) == 1 and rows[0]["target"] == "ana" and rows[0]["actor"] == "ana"
    assert "password" not in (rows[0]["summary"] + str(rows[0]["changes"])).replace("password changed", "")
