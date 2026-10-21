"""Full project archive: export everything a team needs to leave or move, and import it back faithfully."""
import json

import pytest

from app import config


def _project_with_everything(client):
    pid = client.post("/api/projects", json={"name": "Order to cash", "platform": "salesforce", "description": "CRM order capture"}).json()["id"]
    api = "/api/projects/%s" % pid
    client.post(api + "/documents/text", json={"name": "brd.md", "text": "# Orders\nThe system shall allow buyers to place orders online.\nThe system shall email order confirmations."})
    r1 = client.post(api + "/requirements", json={"statement": "The system shall allow buyers to place orders online.", "status": "approved"}).json()
    r2 = client.post(api + "/requirements", json={"statement": "The system shall email order confirmations to buyers."}).json()
    client.post(api + "/requirements", json={"statement": "Legacy requirement that was dropped.", "status": "excluded"})
    s1 = client.post(api + "/stories", json={"title": "Place an order", "narrative": "As a buyer, I want to order, so that I get goods.",
                                              "acceptance_criteria": ["Given a cart, when I order, then I get a number."], "requirement_ids": [r1["key"]], "status": "approved"}).json()
    client.post(api + "/stories", json={"title": "Email confirmation", "requirement_ids": [r2["key"], r1["key"]]})
    client.patch(api + "/requirements/%s" % r2["id"], json={"notes": "Confirm wording with legal."})
    client.post(api + "/comments", json={"item_id": s1["id"], "body": "Should this include a PO number?"})
    client.post(api + "/baselines", json={"name": "Release 1", "note": "Signed off", "scope": "approved"})
    return pid, r1, s1


def _export(client, pid):
    r = client.get("/api/projects/%s/archive.json" % pid)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "order-to-cash" in r.headers["content-disposition"]
    return r.json()


def test_archive_contains_everything_a_team_needs_and_no_secrets(client):
    pid, r1, s1 = _project_with_everything(client)
    a = _export(client, pid)
    assert a["format"] == "fabulist-project" and a["version"] == 1 and a["exported_at"] and a["project"]["name"] == "Order to cash"
    assert a["project"]["platform"] == "salesforce" and a["project"]["description"] == "CRM order capture"
    assert [d["filename"] for d in a["documents"]] == ["brd.md"] and "place orders online" in a["documents"][0]["text"]
    assert [(r["seq"], r["status"]) for r in a["requirements"]] == [(0, "approved"), (1, "draft"), (2, "excluded")]
    assert len(a["stories"]) == 2 and a["stories"][0]["data"]["requirement_ids"] == [r1["key"]]
    assert a["baselines"][0]["name"] == "Release 1" and a["comments"][0]["body"].startswith("Should this include")
    assert {h["action"] for h in a["history"]} >= {"created", "edited", "commented", "baselined"}
    text = json.dumps(a)
    assert "token" not in text.lower().replace("tokens", "") or "api_key" not in text  # nothing credential-shaped is exported
    assert "token_enc" not in text and "api_key" not in text


def test_import_recreates_the_project_faithfully_as_a_new_one(client):
    pid, r1, s1 = _project_with_everything(client)
    original = _export(client, pid)
    r = client.post("/api/projects/import", json=original)
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != pid and new["name"] == "Order to cash (imported)"
    api = "/api/projects/%s" % new["id"]
    reqs = client.get(api + "/requirements").json()
    assert [(x["key"], x["status"]) for x in reqs] == [("REQ-001", "approved"), ("REQ-002", "draft"), ("REQ-003", "excluded")], "keys and order are kept"
    assert reqs[1]["notes"] == "Confirm wording with legal." and all(x["rev"] == 1 for x in reqs)
    stories = client.get(api + "/stories").json()
    assert [s["requirement_ids"] for s in stories] == [["REQ-001"], ["REQ-002", "REQ-001"]], "story links still resolve to the same keys"
    assert client.get(api + "/documents").json()[0]["filename"] == "brd.md"
    assert client.get(api + "/documents/%s" % client.get(api + "/documents").json()[0]["id"]).json()["text"].startswith("# Orders")
    baseline = client.get(api + "/baselines").json()[0]
    assert baseline["name"] == "Release 1" and baseline["version"] == 1 and baseline["requirements"] == 1
    comments = client.get(api + "/comments", params={"item_id": stories[0]["id"]}).json()
    assert comments and comments[0]["body"].startswith("Should this include"), "comments follow their item to its new id"
    history = client.get(api + "/history", params={"limit": 500}).json()
    assert history[0]["action"] == "imported" and "not verified" in history[0]["summary"]
    assert len(history) == len(original["history"]) + 1, "the original trail is carried over, plus one entry for the import"
    # the original is untouched
    assert len(client.get("/api/projects/%s/requirements" % pid).json()) == 3
    # importing again never collides
    assert client.post("/api/projects/import", json=original).json()["name"] == "Order to cash (imported 2)"


@pytest.mark.parametrize("mutate,message", [
    (lambda a: a.update(format="something-else"), "not a Fabulist project archive"),
    (lambda a: a.update(version=99), "version 99"),
    (lambda a: a["requirements"].append(dict(a["requirements"][0])), "duplicate"),
    (lambda a: a.update(history=[{"item_type": "evil", "item_id": "x", "action": "a", "actor": "b", "created": "2026-01-01"}]), "history"),
    (lambda a: a.pop("project"), "project"),
    (lambda a: a["stories"][0]["data"].update(requirement_ids="not-a-list"), "requirement_ids"),
])
def test_invalid_archives_are_refused_and_nothing_is_created(client, mutate, message):
    pid, _, _ = _project_with_everything(client)
    archive = _export(client, pid)
    before = len(client.get("/api/projects").json())
    mutate(archive)
    r = client.post("/api/projects/import", json=archive)
    assert r.status_code == 400 and message in r.json()["detail"], r.text
    assert len(client.get("/api/projects").json()) == before


def test_import_limits_and_non_objects(client, monkeypatch):
    assert client.post("/api/projects/import", json=["not", "an", "archive"]).status_code in (400, 422)
    pid, _, _ = _project_with_everything(client)
    archive = _export(client, pid)
    from app import portability
    monkeypatch.setattr(portability, "MAX_ITEMS", 2)
    r = client.post("/api/projects/import", json=archive)
    assert r.status_code == 400 and "too many" in r.json()["detail"]


def test_imported_content_is_clamped_like_manual_edits(client):
    pid, _, _ = _project_with_everything(client)
    archive = _export(client, pid)
    archive["requirements"][0]["data"]["statement"] = "The system shall " + "x" * 5000
    archive["requirements"][0]["status"] = "bogus"
    r = client.post("/api/projects/import", json=archive)
    assert r.status_code == 201
    first = client.get("/api/projects/%s/requirements" % r.json()["id"]).json()[0]
    assert len(first["statement"]) <= 1500 and first["status"] == "draft", "same limits and status rules as manual edits"


def test_viewers_cannot_import_but_anyone_who_can_read_can_export(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "viewer1", "role": "viewer", "password": "viewer1-password-1"}, auth=root)
    viewer = ("viewer1", "viewer1-password-1")
    pid = client.post("/api/projects", json={"name": "P", "platform": "generic"}, auth=root).json()["id"]
    archive = client.get("/api/projects/%s/archive.json" % pid, auth=viewer)
    assert archive.status_code == 200
    assert client.post("/api/projects/import", json=archive.json(), auth=viewer).status_code == 403
    assert client.post("/api/projects/import", json=archive.json(), auth=root).status_code == 201


def test_the_import_route_has_its_own_larger_size_limit(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(config, "MAX_DOC_CHARS", 100)
    assert config.max_body_bytes() < 2 * 1024 * 1024
    junk = b'{"format":"x","pad":"' + b"a" * (1_500_000) + b'"}'
    headers = {"Content-Type": "application/json"}
    # an ordinary endpoint refuses a 1.5 MB body at these limits ...
    assert client.post("/api/projects", content=junk, headers=headers).status_code == 413
    # ... the import route accepts it as far as the size check goes (it then fails validation, not size)
    monkeypatch.setattr(config, "MAX_ARCHIVE_MB", 3)
    r = client.post("/api/projects/import", content=junk, headers=headers)
    assert r.status_code == 400 and "not a Fabulist project archive" in r.json()["detail"]
    monkeypatch.setattr(config, "MAX_ARCHIVE_MB", 1)
    assert client.post("/api/projects/import", content=junk, headers=headers).status_code == 413
