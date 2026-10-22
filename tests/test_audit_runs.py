"""Who started which AI run, and how it ended, is in the project's audit trail (and its CSV), not only on the Overview's run list."""
import csv
import io
import time

BRD = "# A\nThe system shall export invoices to CSV on request.\n"


def _run(client, stage="requirements", text=BRD):
    pid = client.post("/api/projects", json={"name": "Runs in the trail", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": text})
    client.post("/api/projects/%s/runs" % pid, json={"stage": stage})
    for _ in range(60):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            break
        time.sleep(0.2)
    time.sleep(0.2)
    return pid


def _runs_in_trail(client, pid):
    return [e for e in client.get("/api/projects/%s/history?limit=500" % pid).json() if e["item_type"] == "run"]


def test_a_finished_run_is_in_the_trail_with_who_and_what(client):
    pid = _run(client)
    entries = _runs_in_trail(client, pid)
    assert len(entries) == 1
    e = entries[0]
    assert e["action"] == "ai run done" and e["actor"] == "anonymous"
    assert "requirements" in e["summary"] and "1 item" in e["summary"]


def test_a_failed_run_says_why(client):
    pid = _run(client, stage="stories")  # nothing to write stories for
    e = _runs_in_trail(client, pid)[0]
    assert e["action"] == "ai run failed" and "requirements" in e["summary"].lower()


def test_a_cancelled_queued_run_is_not_in_the_trail_because_it_never_ran(client):
    pid = client.post("/api/projects", json={"name": "Never ran", "platform": "generic"}).json()["id"]
    assert _runs_in_trail(client, pid) == []


def test_runs_come_out_in_the_trails_csv(client):
    pid = _run(client)
    rows = list(csv.DictReader(io.StringIO(client.get("/api/projects/%s/history.csv" % pid).text)))
    assert any(r["Action"] == "ai run done" and r["Type"] == "run" for r in rows), rows


def test_a_run_marked_stuck_is_in_the_trail_too(client, monkeypatch):
    from app import config, db, workspace
    from tests.test_api import _running_run
    monkeypatch.setattr(config, "STALE_RUN_MINUTES", 15)
    pid = client.post("/api/projects", json={"name": "Hung", "platform": "generic"}).json()["id"]
    hung = _running_run(pid, updated_minutes_ago=2)
    db.execute("UPDATE runs SET updated=%s WHERE id=%s", ("2000-01-01T00:00:00.000+00:00", hung))
    assert workspace.recover_all_stuck_runs() == 1
    e = _runs_in_trail(client, pid)[0]
    assert e["action"] == "ai run failed" and "stuck" in e["summary"]
