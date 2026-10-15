"""Tracker push against a fake HTTP layer, through the real API and database."""
import json
import time

import httpx
import pytest

from app import integrations

BRD = "# Orders\n## Capture\nThe system shall allow buyers to place orders online.\nThe system shall email order confirmations to buyers.\n"


class FakeTracker:
    def __init__(self, fail_summary=None):
        self.calls, self.n, self.fail_summary = [], 100, fail_summary

    def __call__(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        req = httpx.Request(method, url)
        if url.endswith("/rest/api/2/myself"):
            return httpx.Response(200, json={"displayName": "Ana"}, request=req)
        if method == "POST" and url.endswith("/rest/api/2/issue"):
            if self.fail_summary and json["fields"]["summary"].startswith(self.fail_summary):
                return httpx.Response(400, json={"errorMessages": [], "errors": {"summary": "too spicy"}}, request=req)
            self.n += 1
            return httpx.Response(201, json={"key": "FAB-%d" % self.n}, request=req)
        if method == "PUT":
            return httpx.Response(204, request=req)
        if "/_apis/wit/workitems" in url:
            self.n += 1
            wid = url.split("/workitems/")[1].split("?")[0] if method == "PATCH" else str(self.n)
            return httpx.Response(200, json={"id": int(wid), "_links": {"html": {"href": "https://dev.azure.com/org/P/_workitems/edit/" + wid}}}, request=req)
        return httpx.Response(404, json={"message": "not found"}, request=req)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(integrations.time, "sleep", lambda s: None)


def _project_with_stories(client):
    pid = client.post("/api/projects", json={"name": "Push", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": BRD})
    for stage in ("requirements", "stories"):
        client.post("/api/projects/%s/runs" % pid, json={"stage": stage})
        for _ in range(50):
            if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] != "running":
                break
            time.sleep(0.1)
    stories = client.get("/api/projects/%s/stories" % pid).json()
    client.post("/api/projects/%s/stories/bulk" % pid, json={"ids": [s["id"] for s in stories], "status": "approved"})
    return pid, stories


def test_jira_push_is_idempotent(client, monkeypatch):
    fake = FakeTracker()
    monkeypatch.setattr(integrations.httpx, "request", fake)
    assert client.post("/api/admin/integrations", json={"kind": "jira", "name": "J", "base_url": "http://x"}).status_code == 400
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "Jira", "base_url": "https://acme.atlassian.net",
                                                         "username": "ana@example.com", "target": "FAB", "token": "jira-tok-1234"}).json()
    assert conn["has_token"] and "token" not in conn and "jira-tok" not in json.dumps(client.get("/api/integrations").json())
    assert client.post("/api/admin/integrations/%s/test" % conn["id"]).json() == {"ok": True, "message": "Signed in as Ana"}
    assert fake.calls[0]["headers"]["Authorization"].startswith("Basic ")

    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r["created"] == 1 + len(stories) and r["updated"] == 0 and r["failed"] == []
    posts = [c for c in fake.calls if c["method"] == "POST" and c["url"].endswith("/issue")]
    assert posts[0]["json"]["fields"]["issuetype"] == {"name": "Epic"}
    story_post = posts[1]["json"]["fields"]
    assert story_post["parent"] == {"key": "FAB-101"} and story_post["project"] == {"key": "FAB"} and "fabulist" in story_post["labels"]
    assert "Acceptance criteria" in story_post["description"]
    linked = client.get("/api/projects/%s/stories" % pid).json()
    assert all(s["external"] and s["external"][0]["url"].startswith("https://acme.atlassian.net/browse/FAB-") for s in linked)

    before = len([c for c in fake.calls if c["method"] == "POST"])
    r2 = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r2["created"] == 0 and r2["updated"] == 1 + len(stories)
    assert len([c for c in fake.calls if c["method"] == "POST"]) == before, "re-push must not create duplicates"
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] == "pushed to jira"


def test_push_reports_partial_failures(client, monkeypatch):
    pid, stories = _project_with_stories(client)
    monkeypatch.setattr(integrations.httpx, "request", FakeTracker(fail_summary=stories[0]["title"][:20]))
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "Jira", "base_url": "https://acme.atlassian.net",
                                                         "username": "a@example.com", "target": "FAB", "token": "t"}).json()
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert len(r["failed"]) == 1 and "too spicy" in r["failed"][0]["error"] and r["created"] == len(stories)
    assert client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "scope": "approved", "target": ""}).status_code == 200


def test_azure_push_uses_json_patch_and_parent_link(client, monkeypatch):
    fake = FakeTracker()
    monkeypatch.setattr(integrations.httpx, "request", fake)
    conn = client.post("/api/admin/integrations", json={"kind": "azure", "name": "ADO", "base_url": "https://dev.azure.com/org",
                                                         "target": "My Project", "token": "pat-5678"}).json()
    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r["created"] == 1 + len(stories) and not r["failed"]
    epic_call, story_call = fake.calls[0], fake.calls[1]
    assert "/My%20Project/_apis/wit/workitems/$Epic" in epic_call["url"]
    assert story_call["headers"]["Content-Type"] == "application/json-patch+json"
    paths = {op["path"] for op in story_call["json"]}
    assert {"/fields/System.Title", "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "/relations/-"} <= paths
    r2 = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r2["updated"] == 1 + len(stories) and all(c["method"] == "PATCH" for c in fake.calls[len(fake.calls) - r2["updated"]:])


def test_push_requires_target_token_and_stories(client, monkeypatch):
    monkeypatch.setattr(integrations.httpx, "request", FakeTracker())
    pid = client.post("/api/projects", json={"name": "Empty", "platform": "generic"}).json()["id"]
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "J", "base_url": "https://a.atlassian.net"}).json()
    assert client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).status_code == 400  # no target
    assert client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB"}).status_code == 400  # no stories
    assert client.post("/api/admin/integrations/%s/test" % conn["id"]).json()["ok"] is False  # no token
