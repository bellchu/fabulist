"""The README's automation cookbook, step for step with a personal API token: it must actually work."""
import time

BRD = b"# Orders\n## Capture\nThe system shall allow buyers to place orders online.\nThe system shall email order confirmations to buyers.\n"


def _wait(api, pid):
    time.sleep(0.3)
    for _ in range(100):
        status = api.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"]
        if status not in ("running", "queued"):
            return status
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_the_cookbook_takes_a_brd_to_a_backlog_with_a_token(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "bot", "role": "analyst", "password": "bot-password-12"}, auth=root)
    token = client.post("/api/me/tokens", json={"name": "cookbook", "days": 1}, auth=("bot", "bot-password-12"), headers={"X-Requested-With": "fabulist"}).json()["token"]

    class Api:  # what `api()` in the cookbook does: a Bearer token and no X-Requested-With
        headers = {"Authorization": "Bearer " + token}

        def get(self, path):
            return client.get(path, headers=self.headers)

        def post(self, path, **kw):
            return client.post(path, headers=self.headers, **kw)
    api = Api()
    r = api.post("/api/projects", json={"name": "Order to cash", "platform": "salesforce"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert api.post("/api/projects/%s/documents" % pid, files={"file": ("brd.md", BRD, "text/markdown")}).status_code == 201
    assert api.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"}).status_code == 202
    assert _wait(api, pid) == "done"
    ids = [x["id"] for x in api.get("/api/projects/%s/requirements" % pid).json()]
    assert ids
    assert api.post("/api/projects/%s/requirements/bulk" % pid, json={"ids": ids, "status": "approved"}).status_code == 200
    assert api.post("/api/projects/%s/runs" % pid, json={"stage": "stories"}).status_code == 202
    assert _wait(api, pid) == "done"
    export = api.get("/api/projects/%s/export?format=xlsx" % pid)
    assert export.status_code == 200 and export.content[:2] == b"PK", "an Excel workbook"
