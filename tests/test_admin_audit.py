"""Administrative changes leave an append-only trail, without ever writing a secret into it."""
import pytest

from app import config, users

ROOT = ("root", "root-password-123")


@pytest.fixture()
def secured(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", ROOT[0])
    monkeypatch.setattr(config, "BASIC_PASSWORD", ROOT[1])
    users._cache.clear()
    for name, role in (("ana", "analyst"), ("vic", "viewer"), ("adm", "admin")):
        assert client.post("/api/admin/users", json={"username": name, "role": role, "password": name + "-password-1"}, auth=ROOT).status_code == 201
    return client


def creds(name):
    return (name, name + "-password-1")


def trail(c, auth=None, **params):
    r = c.get("/api/admin/audit", params=params, auth=auth)
    assert r.status_code == 200, r.text
    return r.json()


def test_provider_changes_are_recorded_without_the_key(client):
    client.post("/api/admin/providers", json={"name": "Other", "kind": "mock"})  # the last provider cannot be deleted
    p = client.post("/api/admin/providers", json={"name": "Corp AI", "kind": "openai", "base_url": "https://llm.example.com/v1",
                                                  "model": "m1", "api_key": "sk-super-secret-1234"}).json()
    client.put("/api/admin/providers/%s" % p["id"], json={"name": "Corp AI", "kind": "openai", "base_url": "https://llm.example.com/v1",
                                                          "model": "m2", "api_key": "sk-another-secret-9999"})
    client.post("/api/admin/providers/%s/default" % p["id"])
    client.delete("/api/admin/providers/%s" % p["id"])
    rows = trail(client)
    assert [r["action"] for r in rows[:4]] == ["provider deleted", "provider set as default", "provider updated", "provider created"]
    assert all(r["target"] == "Corp AI" for r in rows[:4])
    other = [r for r in rows if r["target"] == "Other"][0]
    assert other["summary"] == "mock", "no 'model -' filler for a provider that has no model"
    assert "secret" not in str(rows)


def test_only_real_setting_changes_are_recorded(client):
    client.put("/api/admin/settings", json={"temperature": 0.5})
    client.put("/api/admin/settings", json={"temperature": 0.5})  # no change
    rows = [r for r in trail(client) if r["action"] == "settings changed"]
    assert len(rows) == 1
    assert rows[0]["changes"]["temperature"] == {"from": 0.2, "to": 0.5}


def test_account_changes_are_recorded_without_passwords(secured):
    c = secured
    uid = c.post("/api/admin/users", json={"username": "newbie", "role": "viewer", "password": "newbie-password-1"}, auth=ROOT).json()["id"]
    c.patch("/api/admin/users/%s" % uid, json={"role": "analyst"}, auth=ROOT)
    c.patch("/api/admin/users/%s" % uid, json={"disabled": True}, auth=ROOT)
    c.patch("/api/admin/users/%s" % uid, json={"password": "another-password-22"}, auth=ROOT)
    c.delete("/api/admin/users/%s" % uid, auth=ROOT)
    rows = [r for r in trail(c, ROOT) if r["target"] == "newbie"]
    assert [r["action"] for r in rows] == ["user deleted", "password reset", "user disabled", "role changed", "user created"]
    assert rows[3]["changes"] == {"role": {"from": "viewer", "to": "analyst"}}
    assert all(r["actor"] == "root" for r in rows)
    assert "password" not in str([r["summary"] + str(r["changes"]) for r in rows]).replace("password reset", "")


def test_integrations_and_taxonomy_are_recorded(client):
    i = client.post("/api/admin/integrations", json={"kind": "webhook", "name": "Hook", "base_url": "https://hooks.example.com/x",
                                                     "token": "tok-123456"}).json()
    client.delete("/api/admin/integrations/%s" % i["id"])
    c = client.post("/api/admin/taxonomy/classes", json={"name": "Vaccine", "attributes": ["storage"], "keywords": ["vaccine"]}).json()
    client.delete("/api/admin/taxonomy/classes/%s" % c["id"])
    client.put("/api/admin/taxonomy/rules", json={"rules": {"storage": "Stored at 2-8 C"}})
    actions = [r["action"] for r in trail(client)]
    for a in ("integration created", "integration deleted", "taxonomy class created", "taxonomy class deleted", "taxonomy rules replaced"):
        assert a in actions, a
    assert "tok-123456" not in str(trail(client))


def test_trail_pages_filters_and_exports(client):
    for t in (0.1, 0.2, 0.3, 0.4):
        client.put("/api/admin/settings", json={"temperature": t})
    first = trail(client, limit=2)
    second = trail(client, limit=2, before=first[-1]["n"])
    assert len(first) == 2 and second and second[0]["n"] < first[-1]["n"]
    assert trail(client, action="nothing like this") == []
    r = client.get("/api/admin/audit.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.text.splitlines()[0].startswith("When,")


def test_only_administrators_can_read_it(secured):
    c = secured
    assert c.get("/api/admin/audit", auth=creds("ana")).status_code == 403
    assert c.get("/api/admin/audit.csv", auth=creds("vic")).status_code == 403
    assert c.get("/api/admin/audit", auth=creds("adm")).status_code == 200


def test_every_admin_and_token_write_route_records(client):
    """A route added later without a call to audit.record fails here."""
    from app.main import app
    import inspect
    missing, checked = [], 0
    routes = []
    for r in app.routes:  # included routers are wrapped; their own routes are on original_router
        routes += list(r.original_router.routes) if hasattr(r, "original_router") else [r]
    for route in routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.startswith(("/api/admin/", "/api/me/tokens")) and methods & {"POST", "PUT", "PATCH", "DELETE"} and path not in EXEMPT:
            checked += 1
            if "audit.record" not in inspect.getsource(route.endpoint):
                missing.append(path)
    assert checked >= 15, checked  # the walk really found the routes (it once found none, and passed)
    assert not missing, missing


# reads that use POST, and actions that change nothing
EXEMPT = {"/api/admin/providers/models", "/api/admin/providers/{pid}/test", "/api/admin/integrations/{iid}/test", "/api/admin/reindex"}


def test_token_lifecycle_is_recorded_without_the_token(secured):
    c = secured
    made = c.post("/api/me/tokens", json={"name": "ci-bot", "days": 30}, auth=creds("ana")).json()
    c.delete("/api/me/tokens/%s" % made["id"], auth=creds("ana"))
    other = c.post("/api/me/tokens", json={"name": "reports", "days": 10}, auth=creds("vic")).json()
    c.delete("%s/%s" % (ADMIN_TOKENS, other["id"]), auth=ROOT)
    rows = trail(c, ROOT)
    assert [(r["action"], r["target"], r["actor"]) for r in rows[:4]] == [
        ("token revoked by an administrator", "reports", "root"), ("token created", "reports", "vic"),
        ("token revoked", "ci-bot", "ana"), ("token created", "ci-bot", "ana")]
    assert made["token"] not in str(rows) and other["token"] not in str(rows)


ADMIN_TOKENS = "/api/admin/tokens"


def test_permanently_deleting_a_project_outlives_the_project(client):
    pid = client.post("/api/projects", json={"name": "Doomed", "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."})
    client.post("/api/projects/%s/archive" % pid, json={})
    assert client.delete("/api/projects/%s" % pid).status_code == 204
    gone = [r for r in trail(client) if r["action"] == "project deleted"]
    assert len(gone) == 1 and gone[0]["target"] == "Doomed" and gone[0]["summary"] == "0 BRDs, 1 requirements, 0 stories"
    assert gone[0]["changes"] == {"project_id": pid}
    assert client.delete("/api/projects/%s" % pid).status_code == 404 and len(trail(client, action="project deleted")) == 1


def test_a_refused_delete_leaves_no_entry(client):
    pid = client.post("/api/projects", json={"name": "Alive", "platform": "generic"}).json()["id"]
    assert client.delete("/api/projects/%s" % pid).status_code == 409  # not archived
    assert trail(client, action="project deleted") == []


def test_starting_the_taxonomy_from_scratch_removes_classes_and_rules_and_is_recorded(client):
    from app import taxonomy
    client.post("/api/admin/taxonomy/classes", json={"name": "Vaccine", "attributes": ["storage"], "keywords": ["vaccine"]})
    client.put("/api/admin/taxonomy/rules", json={"rules": {"storage": "Stored at 2-8 C"}})
    assert client.get("/api/admin/taxonomy").json()["classes"], "seeded and added classes are there"
    r = client.delete("/api/admin/taxonomy")
    assert r.status_code == 200 and r.json()["removed_classes"] >= 1 and r.json()["removed_rules"] == 1
    t = client.get("/api/admin/taxonomy").json()
    assert t["classes"] == [] and t["attribute_rules"] == []
    taxonomy.seed()  # what every restart does: an emptied taxonomy must stay empty, not be refilled with the built-in medical classes
    assert client.get("/api/admin/taxonomy").json()["classes"] == []
    assert taxonomy.match_classes("The vaccine shall be stored cold.") == []
    rows = [x for x in trail(client) if x["action"] == "taxonomy cleared"]
    assert len(rows) == 1 and "classes" in rows[0]["summary"]
    assert client.delete("/api/admin/taxonomy").json() == {"removed_classes": 0, "removed_rules": 0}
