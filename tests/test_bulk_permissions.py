"""Every bulk tool changes many items at once, so each must refuse a viewer and an archived project like any other edit."""
from app import config, users

ROOT = ("root", "root-password-123")
VIC = ("vic", "vic-password-12")


def _setup(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    client.post("/api/admin/users", json={"username": "vic", "role": "viewer", "password": "vic-password-12"}, auth=ROOT)
    pid = client.post("/api/projects", json={"name": "Perms", "platform": "generic"}, auth=ROOT).json()["id"]
    ids = [client.post("/api/projects/%s/requirements" % pid, json={"statement": s, "compliance_tags": ["SOX"]}, auth=ROOT).json()["id"]
           for s in ("The system shall log every payment attempt.", "The system shall mask card numbers in reports.")]
    return pid, ids


def _calls(pid, ids):
    base = "/api/projects/%s" % pid
    return [
        (base + "/requirements/tag", {"ids": ids, "tag": "GDPR"}),
        (base + "/stories/tag", {"ids": ids, "tag": "GDPR"}),
        (base + "/requirements/set", {"ids": ids, "field": "epic", "value": "X"}),
        (base + "/requirements/merge", {"keep": ids[0], "merge": [ids[1]]}),
        (base + "/requirements/%s/split" % ids[0], {"statements": ["The system shall log payments.", "The system shall log refunds."]}),
        (base + "/tags/rename", {"from": "SOX", "to": "Sarbanes"}),
        (base + "/requirements/assign", {"ids": ids, "assignee": ""}),
    ]


def test_a_viewer_cannot_use_any_bulk_tool(client, monkeypatch):
    pid, ids = _setup(client, monkeypatch)
    for path, body in _calls(pid, ids):
        assert client.post(path, json=body, auth=VIC).status_code == 403, path
    tags = [r["compliance_tags"] for r in client.get("/api/projects/%s/requirements" % pid, auth=ROOT).json()]
    assert tags == [["SOX"], ["SOX"]], "nothing changed"


def test_an_archived_project_refuses_every_bulk_tool(client, monkeypatch):
    pid, ids = _setup(client, monkeypatch)
    assert client.post("/api/projects/%s/archive" % pid, json={}, auth=ROOT).status_code == 200
    for path, body in _calls(pid, ids):
        assert client.post(path, json=body, auth=ROOT).status_code == 409, path
