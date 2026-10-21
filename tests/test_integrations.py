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
        for _ in range(200):  # wait for the run itself: moving on with it still running leaks it into later tests
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


def test_github_push_and_update(client, monkeypatch):
    calls = []

    def fake(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        req = httpx.Request(method, url)
        if url.endswith("/user"):
            return httpx.Response(200, json={"login": "octo"}, request=req)
        n = int(url.rsplit("/", 1)[1]) if method == "PATCH" else len(calls)
        return httpx.Response(201, json={"number": n, "html_url": "https://github.com/acme/brds/issues/%d" % n}, request=req)
    monkeypatch.setattr(integrations.httpx, "request", fake)
    conn = client.post("/api/admin/integrations", json={"kind": "github", "name": "GH", "base_url": "https://api.github.com",
                                                         "target": "acme/brds", "token": "ghp_x"}).json()
    assert client.post("/api/admin/integrations/%s/test" % conn["id"]).json()["message"] == "Signed in as octo"
    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r["created"] == 1 + len(stories) and not r["failed"]
    story_call = next(c for c in calls if c[0] == "POST" and c[2]["title"] != "Epic: Orders")
    assert "- [ ] Given" in story_call[2]["body"] and "Epic: #" in story_call[2]["body"]
    r2 = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r2["updated"] == 1 + len(stories)
    bad = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "not a repo"}).json()
    assert len(bad["failed"]) == 1 + len(stories) and "owner/repository" in bad["failed"][0]["error"]


def test_webhook_delivery_is_signed(client, monkeypatch):
    import hashlib
    import hmac
    sent = []

    def fake_post(url, content=None, headers=None, timeout=None):
        sent.append((url, content, headers))
        return httpx.Response(204, request=httpx.Request("POST", url))
    monkeypatch.setattr(integrations.httpx, "post", fake_post)
    conn = client.post("/api/admin/integrations", json={"kind": "webhook", "name": "Hook", "base_url": "https://hooks.example.com/fabulist",
                                                         "token": "s3cret"}).json()
    assert client.post("/api/admin/integrations/%s/test" % conn["id"]).json() == {"ok": True, "message": "Ping delivered"}
    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r["created"] == len(stories)
    url, body, headers = sent[-1]
    payload = json.loads(body)
    assert payload["event"] == "backlog.pushed" and len(payload["stories"]) == len(stories) and payload["project"]["name"] == "Push"
    assert headers["X-Fabulist-Signature"] == "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()



def test_confluence_publish_creates_then_versions_page(client, monkeypatch):
    calls = []
    state = {"version": 1}

    def fake(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        req = httpx.Request(method, url)
        if url.endswith("/rest/api/space?limit=1"):
            return httpx.Response(200, json={"results": [{"key": "OTC"}], "size": 1}, request=req)
        if method == "GET" and "expand=version" in url:
            return httpx.Response(200, json={"id": "777", "version": {"number": state["version"]}}, request=req)
        if method in ("POST", "PUT"):
            state["version"] += method == "PUT"
            return httpx.Response(200, json={"id": "777", "_links": {"webui": "/spaces/OTC/pages/777"}}, request=req)
        return httpx.Response(404, json={}, request=req)
    monkeypatch.setattr(integrations.httpx, "request", fake)
    conn = client.post("/api/admin/integrations", json={"kind": "confluence", "name": "Wiki", "base_url": "https://acme.atlassian.net/wiki",
                                                         "username": "ana@example.com", "target": "OTC", "token": "t"}).json()
    assert client.post("/api/admin/integrations/%s/test" % conn["id"]).json()["message"] == "Connected: 1 space(s) visible"
    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r["created"] == 1 and r["items"][0]["url"] == "https://acme.atlassian.net/wiki/spaces/OTC/pages/777"
    post = next(c for c in calls if c[0] == "POST")
    body = post[2]["body"]["storage"]["value"]
    assert post[2]["space"] == {"key": "OTC"} and "<h2>Traceability</h2>" in body and "<script" not in body
    r2 = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"]}).json()
    assert r2["updated"] == 1 and r2["created"] == 0
    put = next(c for c in calls if c[0] == "PUT")
    assert put[2]["version"] == {"number": 2} and put[2]["id"] == "777"


def test_push_stops_at_the_time_budget_and_a_second_push_finishes_without_duplicates(client, monkeypatch):
    from app import config, db
    fake = FakeTracker()
    monkeypatch.setattr(integrations.httpx, "request", fake)
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "Jira", "base_url": "https://acme.atlassian.net",
                                                         "username": "ana@example.com", "target": "FAB", "token": "jira-tok-1234"}).json()
    pid, stories = _project_with_stories(client)
    assert len(stories) >= 2
    # out of time before the first story: only the epics go out, every story is reported as remaining
    monkeypatch.setattr(config, "PUSH_BUDGET_SECONDS", 0)
    first = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"}).json()
    assert first["remaining"] == len(stories) and first["items"] == []
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["summary"].endswith("%d not pushed (time limit)" % len(stories))
    # the unit: it stops mid-list and says how many are left
    calls = []

    def out_of_time():
        calls.append(1)
        return len(calls) > 1  # one story fits
    part = integrations.push(pid, db.one("SELECT * FROM integrations WHERE id=%s", (conn["id"],)), "FAB", stories, out_of_time=out_of_time)
    assert len(part["items"]) == 1 and part["remaining"] == len(stories) - 1
    # pushing again with a normal budget finishes the rest; stories already pushed are updated, never duplicated
    monkeypatch.setattr(config, "PUSH_BUDGET_SECONDS", 60)
    done = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"}).json()
    assert done["remaining"] == 0 and len(done["items"]) == len(stories)
    created = [c for c in fake.calls if c["method"] == "POST" and c["url"].endswith("/rest/api/2/issue")]
    again = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"}).json()
    assert again["created"] == 0 and again["remaining"] == 0
    assert len([c for c in fake.calls if c["method"] == "POST" and c["url"].endswith("/rest/api/2/issue")]) == len(created)


@pytest.mark.parametrize("status,hint", [(401, "credentials"), (403, "permission"), (404, "base URL")])
def test_systemic_tracker_errors_stop_the_push_with_one_actionable_message(client, monkeypatch, status, hint):
    calls = []

    def rejecting(method, url, headers=None, json=None, timeout=None):
        calls.append(url)
        return httpx.Response(status, json={"message": "nope"}, request=httpx.Request(method, url))
    monkeypatch.setattr(integrations.httpx, "request", rejecting)
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "Jira", "base_url": "https://acme.atlassian.net",
                                                         "username": "ana@example.com", "target": "FAB", "token": "jira-tok-1234"}).json()
    pid, stories = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"})
    assert r.status_code == 400 and "HTTP %d" % status in r.json()["detail"] and hint in r.json()["detail"]
    epics = {s["epic"] for s in stories}
    assert len(calls) == len(epics), "no story was attempted once every epic failed the same way"
    assert client.get("/api/projects/%s/history?limit=1" % pid).json()[0]["action"] != "pushed to jira"


def test_item_level_errors_do_not_abort_the_push(client, monkeypatch):
    fake = FakeTracker(fail_summary="Capture")  # a 400 on some issues is a per-item problem, not a systemic one
    monkeypatch.setattr(integrations.httpx, "request", fake)
    conn = client.post("/api/admin/integrations", json={"kind": "jira", "name": "Jira", "base_url": "https://acme.atlassian.net",
                                                         "username": "ana@example.com", "target": "FAB", "token": "jira-tok-1234"}).json()
    pid, _ = _project_with_stories(client)
    r = client.post("/api/projects/%s/push" % pid, json={"integration_id": conn["id"], "target": "FAB", "scope": "approved"})
    assert r.status_code == 200


def test_webhook_carries_a_timestamp_a_delivery_id_and_a_signature_that_covers_the_timestamp(monkeypatch):
    import hashlib
    import hmac
    sent = []

    def flaky_post(url, content=None, headers=None, timeout=None):
        sent.append((content, dict(headers)))
        return httpx.Response(503 if len(sent) == 1 else 204, request=httpx.Request("POST", url))
    monkeypatch.setattr(integrations.httpx, "post", flaky_post)
    monkeypatch.setattr(integrations.time, "sleep", lambda s: None)
    hook = integrations.Webhook({"base_url": "https://hooks.example.com/x"}, "s3cret")
    hook._post({"event": "ping", "sent_at": "now"})
    assert len(sent) == 2, "the 503 was retried"
    (body1, h1), (body2, h2) = sent
    assert h1["X-Fabulist-Delivery"] == h2["X-Fabulist-Delivery"] and len(h1["X-Fabulist-Delivery"]) == 32, "a retry is the same delivery"
    assert abs(int(h2["X-Fabulist-Timestamp"]) - __import__("time").time()) < 5
    # the reference verification documented in the README
    def verify(secret, body, headers, tolerance=300, now=None):
        ts = headers["X-Fabulist-Timestamp"]
        if abs((now or __import__("time").time()) - int(ts)) > tolerance:
            return False
        expected = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, headers["X-Fabulist-Signature-V2"])
    assert verify("s3cret", body2, h2)
    assert not verify("wrong", body2, h2), "bad secret"
    assert not verify("s3cret", body2 + b" ", h2), "tampered body"
    assert not verify("s3cret", body2, h2, now=int(h2["X-Fabulist-Timestamp"]) + 3600), "a captured delivery replayed an hour later"
    forged = dict(h2, **{"X-Fabulist-Timestamp": str(int(h2["X-Fabulist-Timestamp"]) + 3600)})
    assert not verify("s3cret", body2, forged, now=int(h2["X-Fabulist-Timestamp"]) + 3600), "a fresh timestamp cannot be forged onto an old signature"
    # existing receivers that check the body-only signature keep working
    assert h2["X-Fabulist-Signature"] == "sha256=" + hmac.new(b"s3cret", body2, hashlib.sha256).hexdigest()


def test_unsigned_webhook_still_identifies_the_delivery(monkeypatch):
    sent = []
    monkeypatch.setattr(integrations.httpx, "post", lambda url, content=None, headers=None, timeout=None:
                        sent.append(dict(headers)) or httpx.Response(204, request=httpx.Request("POST", url)))
    integrations.Webhook({"base_url": "https://hooks.example.com/x"}, "")._post({"event": "ping"})
    assert "X-Fabulist-Delivery" in sent[0] and "X-Fabulist-Timestamp" in sent[0]
    assert "X-Fabulist-Signature" not in sent[0] and "X-Fabulist-Signature-V2" not in sent[0]
