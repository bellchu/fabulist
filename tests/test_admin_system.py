"""The admin Overview says how the installation itself is doing, without a Grafana: version, uptime, database size, runs working and waiting, memory."""


def test_the_overview_carries_a_system_block(client):
    s = client.get("/api/admin/overview").json()["system"]
    assert s["version"] and s["uptime_seconds"] >= 0
    assert s["database_bytes"] > 100_000, "a real database size"
    assert s["runs_running"] == 0 and s["runs_queued"] == 0
    assert set(s) >= {"memory_bytes", "memory_limit_bytes", "max_concurrent_runs", "max_queued_runs"}
    assert s["max_concurrent_runs"] >= 1 and s["max_queued_runs"] >= 1


def test_running_and_queued_runs_are_counted(client):
    from app import db
    from tests.test_api import _running_run
    pid = client.post("/api/projects", json={"name": "Busy", "platform": "generic"}).json()["id"]
    _running_run(pid, updated_minutes_ago=1)
    s = client.get("/api/admin/overview").json()["system"]
    assert s["runs_running"] == 1
    db.execute("UPDATE runs SET status='failed' WHERE project_id=%s", (pid,))  # the fixture waits for running rows at teardown


def test_it_is_for_administrators_only(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "ana", "role": "analyst", "password": "ana-password-12"}, auth=root)
    assert client.get("/api/admin/overview", auth=("ana", "ana-password-12")).status_code == 403
    assert "system" in client.get("/api/admin/overview", auth=root).json()
