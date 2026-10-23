"""A password an administrator chose is known to the administrator: the person can be required to set their own before doing anything else."""
from app import config, users

ROOT = ("root", "root-password-123")


def _setup(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()


def _make(client, name="newbie", must_change=True, **extra):
    return client.post("/api/admin/users", json=dict({"username": name, "role": "analyst", "password": "temporary-pass-1", "must_change": must_change}, **extra), auth=ROOT)


def test_a_required_change_blocks_everything_but_the_change(client, monkeypatch):
    _setup(client, monkeypatch)
    assert _make(client).json()["must_change"] is True
    who = ("newbie", "temporary-pass-1")
    me = client.get("/api/me", auth=who).json()
    assert me["must_change"] is True and me["username"] == "newbie"
    blocked = client.get("/api/projects", auth=who)
    assert blocked.status_code == 403 and blocked.json()["password_change_required"] is True
    assert client.post("/api/projects", json={"name": "x", "platform": "generic"}, auth=who).status_code == 403
    assert client.get("/api/meta", auth=who).status_code == 200, "the page can still load what it needs to show the change form"
    assert client.get("/healthz").status_code == 200


def test_changing_it_lifts_the_block(client, monkeypatch):
    _setup(client, monkeypatch)
    _make(client)
    r = client.post("/api/me/password", json={"current_password": "temporary-pass-1", "new_password": "my-own-secret-pass"}, auth=("newbie", "temporary-pass-1"))
    assert r.status_code == 200
    mine = ("newbie", "my-own-secret-pass")
    assert client.get("/api/me", auth=mine).json()["must_change"] is False
    assert client.get("/api/projects", auth=mine).status_code == 200


def test_it_is_off_unless_asked_for_and_a_reset_can_ask_again(client, monkeypatch):
    _setup(client, monkeypatch)
    assert _make(client, "plain", must_change=False).json()["must_change"] is False
    assert client.get("/api/projects", auth=("plain", "temporary-pass-1")).status_code == 200
    uid = client.get("/api/admin/users", auth=ROOT).json()["users"][0]["id"]
    client.patch("/api/admin/users/%s" % uid, json={"password": "another-temporary-1", "must_change": True}, auth=ROOT)
    assert client.get("/api/projects", auth=("plain", "another-temporary-1")).status_code == 403
    assert client.get("/api/projects", auth=ROOT).status_code == 200, "administrators are not locked out of anything by it"


def test_a_token_cannot_be_used_around_it(client, monkeypatch):
    _setup(client, monkeypatch)
    _make(client, "tokenuser", must_change=False)
    token = client.post("/api/me/tokens", json={"name": "t", "days": 1}, auth=("tokenuser", "temporary-pass-1")).json()["token"]
    uid = next(u["id"] for u in client.get("/api/admin/users", auth=ROOT).json()["users"] if u["username"] == "tokenuser")
    client.patch("/api/admin/users/%s" % uid, json={"password": "reset-temporary-12", "must_change": True}, auth=ROOT)
    assert client.get("/api/projects", headers={"Authorization": "Bearer " + token}).status_code == 403, "a reset account is blocked however it signs in"


def test_it_is_recorded_in_the_administrative_trail(client, monkeypatch):
    _setup(client, monkeypatch)
    _make(client)
    rows = client.get("/api/admin/audit", auth=ROOT).json()
    created = next(r for r in rows if r["action"] == "user created")
    assert "must change" in created["summary"]


def test_a_named_administrator_is_held_to_it_too_but_the_bootstrap_login_is_not(client, monkeypatch):
    _setup(client, monkeypatch)
    _make(client, "newadmin", role="admin")
    assert client.get("/api/projects", auth=("newadmin", "temporary-pass-1")).status_code == 403
    assert client.get("/api/admin/overview", auth=("newadmin", "temporary-pass-1")).status_code == 403
    assert client.get("/api/admin/overview", auth=ROOT).status_code == 200
