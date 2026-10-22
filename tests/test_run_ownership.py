"""Each running AI run belongs to the server instance executing it. A server starting (a rolling deploy) must not fail the runs of a server that is still
alive; the runs of an instance that stopped are failed within about a minute by whichever server notices."""
from app import db, main, workspace


def _project(client):
    return client.post("/api/projects", json={"name": "Owned", "platform": "generic"}).json()["id"]


def _running(pid, owner):
    rid = db.new_id()
    db.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created, updated, owner) "
               "VALUES (%s,%s,'running','requirements','x','','t',%s,%s,%s)", (rid, pid, db.now(), db.now(), owner))
    return rid


def _beat(instance, seconds_ago=0):
    from datetime import datetime, timedelta, timezone
    seen = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds")
    db.execute("INSERT INTO instances (id, started, seen) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET seen = EXCLUDED.seen", (instance, seen, seen))


def _status(rid):
    return db.one("SELECT status, error FROM runs WHERE id=%s", (rid,))


def _finish(pid):
    db.execute("UPDATE runs SET status='failed' WHERE project_id=%s AND status='running'", (pid,))  # the fixture waits for running rows at teardown


def test_a_server_starting_leaves_the_runs_of_a_live_server_alone(client):
    pid = _project(client)
    _beat("server-a", seconds_ago=3)
    rid = _running(pid, "server-a")
    main.recover_runs_after_restart()
    workspace.recover_all_stuck_runs()
    assert _status(rid)["status"] == "running", "server-a is alive and still executing it"
    _finish(pid)


def test_the_runs_of_a_server_that_stopped_are_failed_by_whoever_notices(client):
    pid = _project(client)
    _beat("server-gone", seconds_ago=120)
    rid = _running(pid, "server-gone")
    other = _running(pid, "never-beat")  # an owner that never registered counts as gone
    assert workspace.recover_all_stuck_runs() == 2
    for r in (rid, other):
        s = _status(r)
        assert s["status"] == "failed" and "stopped" in s["error"]
    trail = [e for e in client.get("/api/projects/%s/history?limit=50" % pid).json() if e["item_type"] == "run"]
    assert len(trail) == 2 and all(e["action"] == "ai run failed" for e in trail)


def test_a_run_from_before_owners_were_recorded_still_fails_at_startup(client):
    pid = _project(client)
    rid = _running(pid, None)
    main.recover_runs_after_restart()
    s = _status(rid)
    assert s["status"] == "failed" and "restart" in s["error"]


def test_a_run_is_claimed_by_the_instance_that_executes_it_and_that_instance_beats(client):
    pid = _project(client)
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"})
    client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    import time
    for _ in range(60):
        row = db.one("SELECT status, owner FROM runs WHERE project_id=%s", (pid,))
        if row["status"] not in ("running", "queued"):
            break
        time.sleep(0.2)
    assert row["owner"] == workspace.INSTANCE
    workspace.heartbeat()
    assert db.one("SELECT 1 AS x FROM instances WHERE id=%s", (workspace.INSTANCE,))


def test_old_instances_are_forgotten(client):
    _beat("ancient", seconds_ago=3 * 86400)
    workspace.heartbeat()
    assert db.one("SELECT 1 AS x FROM instances WHERE id='ancient'") is None
