"""Each project says when something last happened in it, so a long list can be sorted by what people are working on."""
import time


def _project(client, name):
    return client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]


def _listed(client):
    return {p["name"]: p for p in client.get("/api/projects").json()}


def test_a_new_project_is_active_from_its_creation(client):
    _project(client, "Fresh")
    p = _listed(client)["Fresh"]
    assert p["last_activity"] and p["last_activity"] >= p["created"]


def test_work_moves_it_forward_and_other_projects_stay_put(client):
    a, b = _project(client, "A"), _project(client, "B")
    before = _listed(client)
    time.sleep(0.05)
    client.post("/api/projects/%s/requirements" % a, json={"statement": "The system shall export invoices to CSV."})
    after = _listed(client)
    assert after["A"]["last_activity"] > before["A"]["last_activity"]
    assert after["B"]["last_activity"] == before["B"]["last_activity"]


def test_it_is_in_the_project_summary_too(client):
    pid = _project(client, "Summary")
    assert client.get("/api/projects/%s" % pid).json()["last_activity"]
