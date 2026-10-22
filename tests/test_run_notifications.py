"""A webhook can opt in to hear when an AI run finishes (runs take minutes to hours: nobody wants to watch the page)."""
import json
import time

from app import integrations

HOOK = {"kind": "webhook", "name": "Chat bridge", "base_url": "https://hooks.example.com/runs", "token": "signing-secret-1"}
BRD = "# A\nThe system shall export invoices to CSV on request.\n"


def _capture(monkeypatch):
    sent = []

    class Resp:
        status_code = 200
    monkeypatch.setattr(integrations.httpx, "post", lambda url, content, headers, timeout: sent.append((url, json.loads(content), headers)) or Resp())
    return sent


def _run(client, stage="requirements", text=BRD, name="Notified project"):
    pid = client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": text})
    client.post("/api/projects/%s/runs" % pid, json={"stage": stage})
    for _ in range(60):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            break
        time.sleep(0.2)
    return pid


def _wait_for(sent, count):
    for _ in range(50):
        if len(sent) >= count:
            break
        time.sleep(0.1)
    time.sleep(0.2)


def test_an_opted_in_webhook_hears_when_a_run_finishes(client, monkeypatch):
    sent = _capture(monkeypatch)
    hook = client.post("/api/admin/integrations", json=dict(HOOK, notify_runs=True)).json()
    assert hook["notify_runs"] is True
    pid = _run(client)
    _wait_for(sent, 1)
    assert len(sent) == 1
    url, payload, headers = sent[0]
    assert url == HOOK["base_url"] and payload["event"] == "run.finished"
    assert payload["project"] == {"id": pid, "name": "Notified project"}
    run = payload["run"]
    assert run["stage"] == "requirements" and run["status"] == "done" and run["items"] == 1 and run["error"] == ""
    assert "X-Fabulist-Signature-V2" in headers and "signing-secret-1" not in json.dumps(payload)


def test_a_failed_run_is_reported_with_its_error(client, monkeypatch):
    sent = _capture(monkeypatch)
    client.post("/api/admin/integrations", json=dict(HOOK, notify_runs=True))
    _run(client, stage="stories")  # no requirements to write stories for
    _wait_for(sent, 1)
    assert sent[0][1]["run"]["status"] == "failed" and "requirements" in sent[0][1]["run"]["error"].lower()


def test_webhooks_that_did_not_opt_in_hear_nothing(client, monkeypatch):
    sent = _capture(monkeypatch)
    client.post("/api/admin/integrations", json=HOOK)
    _run(client)
    time.sleep(0.5)  # a notification, if one were going to be sent, goes out on a thread right after the run ends
    assert sent == []


def test_a_broken_receiver_never_fails_the_run(client, monkeypatch):
    def boom(*a, **k):
        raise integrations.httpx.ConnectError("down")
    monkeypatch.setattr(integrations.httpx, "post", boom)
    monkeypatch.setattr(integrations.time, "sleep", lambda s: None)
    client.post("/api/admin/integrations", json=dict(HOOK, notify_runs=True))
    pid = _run(client)
    assert client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] == "done"


def test_the_choice_can_be_changed_and_is_ignored_for_trackers(client):
    made = client.post("/api/admin/integrations", json=dict(HOOK)).json()
    assert made["notify_runs"] is False
    assert client.put("/api/admin/integrations/%s" % made["id"], json=dict(HOOK, notify_runs=True)).json()["notify_runs"] is True
    assert client.put("/api/admin/integrations/%s" % made["id"], json=dict(HOOK)).json()["notify_runs"] is False
    jira = client.post("/api/admin/integrations", json={"kind": "jira", "name": "J", "base_url": "https://x.atlassian.net", "token": "t", "notify_runs": True}).json()
    assert jira["notify_runs"] is False


def test_a_run_marked_stuck_is_reported_too(client, monkeypatch):
    from app import config, db
    from tests.test_api import _running_run
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "STALE_RUN_MINUTES", 15)
    client.post("/api/admin/integrations", json=dict(HOOK, notify_runs=True))
    pid = client.post("/api/projects", json={"name": "Hung", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": BRD})
    hung = _running_run(pid, updated_minutes_ago=2)
    db.execute("UPDATE runs SET updated=%s WHERE id=%s", ("2000-01-01T00:00:00.000+00:00", hung))
    client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})  # starting another run is what notices the hung one
    _wait_for(sent, 2)
    stuck = [s for s in sent if s[1]["run"]["id"] == hung]
    assert len(stuck) == 1 and stuck[0][1]["run"]["status"] == "failed" and "stuck" in stuck[0][1]["run"]["error"]


def test_stuck_runs_are_found_without_anyone_starting_another_run(client, monkeypatch):
    """The sweeper (a background task, once a minute) marks hung runs in every project and reports them."""
    from app import config, db, workspace
    from tests.test_api import _running_run
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "STALE_RUN_MINUTES", 15)
    client.post("/api/admin/integrations", json=dict(HOOK, notify_runs=True))
    pids = [client.post("/api/projects", json={"name": "Hung %d" % i, "platform": "generic"}).json()["id"] for i in range(2)]
    hung = [_running_run(p, updated_minutes_ago=2) for p in pids]
    db.execute("UPDATE runs SET updated=%s WHERE id = ANY(%s)", ("2000-01-01T00:00:00.000+00:00", hung))
    live = _running_run(client.post("/api/projects", json={"name": "Fine", "platform": "generic"}).json()["id"], updated_minutes_ago=1)
    assert workspace.recover_all_stuck_runs() == 2
    _wait_for(sent, 2)
    assert sorted(s[1]["run"]["id"] for s in sent) == sorted(hung)
    assert db.one("SELECT status FROM runs WHERE id=%s", (live,))["status"] == "running", "a run that is making progress is left alone"
    assert workspace.recover_all_stuck_runs() == 0
    db.execute("UPDATE runs SET status='failed' WHERE id=%s", (live,))  # the fixture waits for 'running' rows at teardown (30 s if one never ends)
