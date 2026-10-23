"""A webhook can opt in to hear when work is assigned (a chat bridge can then tell the person), without any e-mail or chat code in Fabulist."""
import json
import time

from app import config, integrations, users

ROOT = ("root", "root-password-123")
HOOK = {"kind": "webhook", "name": "Chat bridge", "base_url": "https://hooks.example.com/assign", "token": "signing-secret-1"}


def _capture(monkeypatch):
    sent = []

    class Resp:
        status_code = 200
    monkeypatch.setattr(integrations.httpx, "post", lambda url, content, headers, timeout: sent.append(json.loads(content)) or Resp())
    return sent


def _wait(sent, n):
    for _ in range(50):
        if len(sent) >= n:
            break
        time.sleep(0.1)
    time.sleep(0.2)


def _setup(client, monkeypatch, **hook):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    for name in ("ana", "ben"):
        client.post("/api/admin/users", json={"username": name, "role": "analyst", "password": name + "-password-1"}, auth=ROOT)
    client.post("/api/admin/integrations", json=dict(HOOK, **hook), auth=ROOT)
    ana = ("ana", "ana-password-1")
    pid = client.post("/api/projects", json={"name": "Assign hook", "platform": "generic"}, auth=ana).json()["id"]
    ids = [client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall support hooked capability %d." % i}, auth=ana).json()["id"] for i in range(3)]
    return ana, pid, ids


def test_one_assignment_is_announced_with_who_what_and_to_whom(client, monkeypatch):
    sent = _capture(monkeypatch)
    ana, pid, ids = _setup(client, monkeypatch, notify_assignments=True)
    client.patch("/api/projects/%s/requirements/%s" % (pid, ids[0]), json={"assignee": "ben"}, auth=ana)
    _wait(sent, 1)
    assert len(sent) == 1
    e = sent[0]
    assert e["event"] == "item.assigned" and e["project"] == {"id": pid, "name": "Assign hook"} and e["assigned_by"] == "ana" and e["assignee"] == "ben"
    assert e["items"] == [{"kind": "requirement", "key": "REQ-001", "title": "The system shall support hooked capability 0.", "previous": ""}]


def test_a_bulk_assignment_is_one_announcement_and_unassigning_is_not_announced(client, monkeypatch):
    sent = _capture(monkeypatch)
    ana, pid, ids = _setup(client, monkeypatch, notify_assignments=True)
    client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": "ben"}, auth=ana)
    _wait(sent, 1)
    assert len(sent) == 1 and [i["key"] for i in sent[0]["items"]] == ["REQ-001", "REQ-002", "REQ-003"]
    client.post("/api/projects/%s/requirements/assign" % pid, json={"ids": ids, "assignee": ""}, auth=ana)
    time.sleep(0.5)
    assert len(sent) == 1, "clearing an assignee tells nobody anything"


def test_webhooks_that_did_not_opt_in_hear_nothing_and_the_choices_are_independent(client, monkeypatch):
    sent = _capture(monkeypatch)
    ana, pid, ids = _setup(client, monkeypatch, notify_runs=True)  # runs only
    client.patch("/api/projects/%s/requirements/%s" % (pid, ids[0]), json={"assignee": "ben"}, auth=ana)
    time.sleep(0.5)
    assert sent == []
    hooks = client.get("/api/integrations", auth=ana).json()["integrations"]
    assert hooks[0]["notify_runs"] is True and hooks[0]["notify_assignments"] is False
    both = client.put("/api/admin/integrations/%s" % hooks[0]["id"], json=dict(HOOK, notify_runs=True, notify_assignments=True), auth=ROOT).json()
    assert both["notify_runs"] is True and both["notify_assignments"] is True
    client.patch("/api/projects/%s/requirements/%s" % (pid, ids[1]), json={"assignee": "ben"}, auth=ana)
    _wait(sent, 1)
    assert [s["event"] for s in sent] == ["item.assigned"]


def test_a_broken_receiver_never_fails_the_assignment(client, monkeypatch):
    def boom(*a, **k):
        raise integrations.httpx.ConnectError("down")
    monkeypatch.setattr(integrations.httpx, "post", boom)
    monkeypatch.setattr(integrations.time, "sleep", lambda s: None)
    ana, pid, ids = _setup(client, monkeypatch, notify_assignments=True)
    assert client.patch("/api/projects/%s/requirements/%s" % (pid, ids[0]), json={"assignee": "ben"}, auth=ana).status_code == 200
