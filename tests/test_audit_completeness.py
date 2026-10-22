"""The project's audit trail covers what happens to the project itself, not only to its requirements and stories: creation, edits, documents, repositories."""


def _trail(client, pid):
    return client.get("/api/projects/%s/history?limit=500" % pid).json()


def _actions(client, pid):
    return [(e["item_type"], e["action"]) for e in _trail(client, pid)]


def test_creating_and_editing_a_project_are_recorded(client):
    pid = client.post("/api/projects", json={"name": "Trail", "platform": "generic", "description": "first"}).json()["id"]
    assert ("project", "created") in _actions(client, pid)
    client.patch("/api/projects/%s" % pid, json={"name": "Trail renamed", "description": "first"})
    edit = next(e for e in _trail(client, pid) if e["action"] == "edited" and e["item_type"] == "project")
    assert edit["changes"] == {"name": {"from": "Trail", "to": "Trail renamed"}}, "only what changed"
    n = len(_trail(client, pid))
    client.patch("/api/projects/%s" % pid, json={"name": "Trail renamed"})
    assert len(_trail(client, pid)) == n, "saving without a change leaves nothing"


def test_adding_and_removing_documents_are_recorded(client):
    pid = client.post("/api/projects", json={"name": "Docs", "platform": "generic"}).json()["id"]
    did = client.post("/api/projects/%s/documents/text" % pid, json={"name": "a.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"}).json()["id"]
    up = client.post("/api/projects/%s/documents" % pid, files={"file": ("b.md", b"# B\nThe system shall email confirmations to buyers.\n", "text/markdown")})
    assert up.status_code == 201
    client.delete("/api/projects/%s/documents/%s" % (pid, did))
    docs = [(e["action"], e["summary"]) for e in _trail(client, pid) if e["item_type"] == "document"]
    assert sorted(docs) == [("added", "a.md"), ("added", "b.md"), ("removed", "a.md")]


def test_the_sample_project_is_traced_from_its_creation(client):
    pid = client.post("/api/projects/sample").json()["id"]
    acts = _actions(client, pid)
    assert ("project", "created") in acts and ("document", "added") in acts


def test_repositories_are_recorded_without_their_token(client):
    pid = client.post("/api/projects", json={"name": "Repos", "platform": "generic"}).json()["id"]
    r = client.post("/api/projects/%s/repos" % pid, json={"url": "https://github.com/example/brds.git", "branch": "main", "token": "ghp_supersecrettoken1234"})
    assert r.status_code == 201, r.text
    client.delete("/api/projects/%s/repos/%s" % (pid, r.json()["id"]))
    trail = [(e["action"], e["summary"]) for e in _trail(client, pid) if e["item_type"] == "repository"]
    assert ("connected", "https://github.com/example/brds.git") in trail and ("removed", "https://github.com/example/brds.git") in trail
    assert "supersecret" not in str(_trail(client, pid))
