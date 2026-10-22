"""The running app uses a database role that can read and write data but cannot change the schema or rewrite the audit tables; a separate
migration role (MIGRATION_DATABASE_URL) creates the schema and grants the rights. Needs TEST_RUNTIME_DATABASE_URL (a login role that owns nothing)."""
import os
import time

import psycopg
import pytest

RUNTIME = os.environ.get("TEST_RUNTIME_DATABASE_URL", "")


@pytest.fixture()
def as_runtime(client, monkeypatch):
    if not RUNTIME:
        pytest.skip("TEST_RUNTIME_DATABASE_URL not set")
    from app import config, db
    owner = config.DATABASE_URL
    monkeypatch.setattr(config, "MIGRATION_DATABASE_URL", owner)
    monkeypatch.setattr(config, "DATABASE_URL", RUNTIME)
    db.init()  # as the migration role: schema, then the grants for the runtime role
    yield client
    db.close()


def _wait(client, pid):
    time.sleep(0.3)
    for _ in range(100):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            return
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_the_whole_workflow_works_without_owning_anything(as_runtime):
    c = as_runtime
    pid = c.post("/api/projects", json={"name": "Least privilege", "platform": "generic"}).json()["id"]
    c.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"})
    assert c.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 202
    _wait(c, pid)
    ids = [r["id"] for r in c.get("/api/projects/%s/requirements" % pid).json()]
    assert ids
    c.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": ids, "status": "approved"})
    assert c.post("/api/projects/%s/runs" % pid, json={"stage": "stories"}).status_code == 202
    _wait(c, pid)
    assert c.get("/api/projects/%s/export?format=xlsx" % pid).status_code == 200
    assert c.post("/api/projects/%s/search" % pid, json={"query": "invoices"}).status_code == 200
    assert c.post("/api/admin/providers", json={"name": "P", "kind": "mock"}).status_code == 201  # writes the administrative trail
    assert c.put("/api/admin/settings", json={"temperature": 0.6}).status_code == 200
    assert c.post("/api/projects/%s/archive" % pid, json={}).status_code == 200
    assert c.delete("/api/projects/%s" % pid).status_code == 204, "deleting a project takes its history with it, although the runtime role cannot delete history itself"
    assert any(e["action"] == "project deleted" for e in c.get("/api/admin/audit").json())


@pytest.mark.parametrize("sql", [
    "UPDATE admin_audit SET actor = 'x'",
    "DELETE FROM admin_audit",
    "UPDATE history SET actor = 'x'",
    "DELETE FROM history",
    "CREATE TABLE sneaky (x int)",
    "ALTER TABLE projects ADD COLUMN sneaky int",
    "DROP TRIGGER history_append_only ON history",
    "DROP TABLE history",
    "TRUNCATE admin_audit",
])
def test_the_runtime_role_cannot_change_the_schema_or_the_audit_tables(as_runtime, sql):
    as_runtime.post("/api/projects", json={"name": "Seed", "platform": "generic"})
    as_runtime.put("/api/admin/settings", json={"temperature": 0.7})
    from app import db
    with pytest.raises(psycopg.Error) as err:
        db.execute(sql)
    assert "permission denied" in str(err.value) or "must be owner" in str(err.value), err.value


def test_without_a_migration_url_nothing_changes(client):
    from app import config
    assert config.MIGRATION_DATABASE_URL == ""
