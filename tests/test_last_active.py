"""Administrators can see when each account was last active (access reviews: who still uses their access, who never did)."""
import time

from app import config, db, users

ROOT = ("root", "root-password-123")


def _setup(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    users._touched.clear()
    for name in ("ana", "ben"):
        client.post("/api/admin/users", json={"username": name, "role": "analyst", "password": name + "-password-1"}, auth=ROOT)


def _last_seen(client):
    return {u["username"]: u["last_seen"] for u in client.get("/api/admin/users", auth=ROOT).json()["users"]}


def test_an_account_that_never_signed_in_has_no_last_active_time(client, monkeypatch):
    _setup(client, monkeypatch)
    assert _last_seen(client) == {"ana": None, "ben": None}


def test_using_the_api_records_it_and_not_on_every_request(client, monkeypatch):
    _setup(client, monkeypatch)
    client.get("/api/projects", auth=("ana", "ana-password-1"))
    first = _last_seen(client)["ana"]
    assert first and _last_seen(client)["ben"] is None
    time.sleep(0.05)
    client.get("/api/projects", auth=("ana", "ana-password-1"))
    assert _last_seen(client)["ana"] == first, "recorded about once every quarter of an hour, not on every request (that would make every read a write)"


def test_a_token_counts_and_the_page_files_do_not(client, monkeypatch):
    _setup(client, monkeypatch)
    token = client.post("/api/me/tokens", json={"name": "t", "days": 1}, auth=("ben", "ben-password-1")).json()["token"]
    db.execute("UPDATE users SET last_seen = NULL")
    users._touched.clear()
    client.get("/static/app.css", auth=("ana", "ana-password-1"))
    assert _last_seen(client)["ana"] is None
    client.get("/api/projects", headers={"Authorization": "Bearer " + token})
    assert _last_seen(client)["ben"] is not None
