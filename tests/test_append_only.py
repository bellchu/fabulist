"""The audit tables are append-only in the database itself, not just by the API's good manners: a bug or an injected statement cannot rewrite history."""
import psycopg
import pytest

from app import db


def _seed(client):
    pid = client.post("/api/projects", json={"name": "Trail", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."})
    client.put("/api/admin/settings", json={"temperature": 0.5})
    return pid


def test_history_cannot_be_rewritten(client):
    _seed(client)
    with pytest.raises(psycopg.Error, match="append-only"):
        db.execute("UPDATE history SET actor = 'someone else'")
    assert db.one("SELECT count(*) AS n FROM history WHERE actor = 'someone else'")["n"] == 0


def test_the_administrative_trail_cannot_be_rewritten_or_erased(client):
    _seed(client)
    with pytest.raises(psycopg.Error, match="append-only"):
        db.execute("UPDATE admin_audit SET actor = 'someone else'")
    with pytest.raises(psycopg.Error, match="append-only"):
        db.execute("DELETE FROM admin_audit")
    assert db.one("SELECT count(*) AS n FROM admin_audit")["n"] >= 1


def test_deleting_a_project_still_removes_its_own_trail(client):
    pid = _seed(client)
    client.post("/api/projects/%s/archive" % pid, json={})
    assert client.delete("/api/projects/%s" % pid).status_code == 204
    assert db.one("SELECT count(*) AS n FROM history WHERE project_id=%s", (pid,))["n"] == 0


def test_the_triggers_survive_a_second_startup(client):
    _seed(client)
    db.init()
    db.init()
    with pytest.raises(psycopg.Error, match="append-only"):
        db.execute("UPDATE history SET actor = 'x'")
