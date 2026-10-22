"""Generate stories only for requirements that no story covers yet, adding to the existing stories and leaving every reviewed or edited one
alone. The offline provider keeps these tests off the network."""
import time

from app import pipeline


def _req(client, pid, statement, epic="Orders", section="Capture", status="approved"):
    return client.post("/api/projects/%s/requirements" % pid, json={"statement": statement, "epic": epic, "section": section, "status": status}).json()


def _run(client, pid, stories_for="uncovered", scope="all"):
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "stories", "scope": scope, "stories_for": stories_for})
    if r.status_code != 202:
        return r
    for _ in range(100):
        runs = client.get("/api/projects/%s/runs?limit=1" % pid).json()
        if runs[0]["status"] not in ("running", "queued"):
            return runs[0]
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _stories(client, pid):
    return client.get("/api/projects/%s/stories" % pid).json()


def _project(client):
    return client.post("/api/projects", json={"name": "Stories", "platform": "generic"}).json()["id"]


def test_new_requirements_get_stories_without_touching_the_reviewed_ones(client):
    pid = _project(client)
    _req(client, pid, "The system shall let a buyer create an order from the account page.")
    _req(client, pid, "The system shall email an order confirmation to the buyer.", section="Notify")
    assert _run(client, pid, "all")["status"] == "done"
    first = _stories(client, pid)
    assert len(first) == 2
    client.patch("/api/projects/%s/stories/%s" % (pid, first[0]["id"]), json={"status": "approved", "notes": "Signed off by the product owner."})

    _req(client, pid, "The system shall let an approver reject an order with a reason.", section="Approve")
    done = _run(client, pid)
    assert done["status"] == "done" and done["item_count"] == 1, "one story, for the one uncovered requirement: %s" % done
    after = _stories(client, pid)
    assert [s["key"] for s in after] == ["US-001", "US-002", "US-003"], "numbering continues"
    kept = {s["id"]: s for s in after}[first[0]["id"]]
    assert kept["status"] == "approved" and kept["notes"] == "Signed off by the product owner.", "a reviewed story is untouched"
    assert "REQ-003" in after[2]["requirement_ids"]
    assert client.get("/api/projects/%s" % pid).json()["coverage"] == 100


def test_nothing_uncovered_is_refused_clearly_and_all_still_starts_over(client):
    pid = _project(client)
    _req(client, pid, "The system shall let a buyer create an order from the account page.")
    assert _run(client, pid, "all")["status"] == "done"
    nothing = _run(client, pid)
    assert nothing.status_code == 400 and "without a story" in nothing.json()["detail"], nothing.text
    story = _stories(client, pid)[0]
    client.patch("/api/projects/%s/stories/%s" % (pid, story["id"]), json={"notes": "to be replaced"})
    assert _run(client, pid, "all")["status"] == "done"
    assert _stories(client, pid)[0]["notes"] != "to be replaced", "'all' replaces the stories, as it always did"


def test_a_large_backlog_gets_its_stories_in_batches_that_fit_the_limit(client):
    pid = _project(client)
    for n in range(4):  # four feature areas = four groups = four AI requests
        _req(client, pid, "The system shall support the capability number %d for the buyer in area %d." % (n, n), section="Area %d" % n)
    client.put("/api/admin/settings", json={"max_chunks_per_run": 2})
    est = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "stories", "stories_for": "uncovered"}).json()
    assert est["requests"] == 2 and est["batch_requirements"] == 2 and est["remaining_requirements"] == 2 and est["over_limit"] is False, est
    counts = []
    while True:
        r = _run(client, pid)
        if hasattr(r, "status_code"):
            break
        assert r["status"] == "done"
        counts.append(r["item_count"])
    assert len(counts) == 2 and sum(counts) == 4, "four requirements in two runs of two groups each: %s" % counts
    assert len(_stories(client, pid)) == 4 and client.get("/api/projects/%s" % pid).json()["coverage"] == 100


def test_a_new_story_never_takes_the_title_of_an_existing_one():
    reqs = [{"key": "REQ-9", "statement": "The system shall manage users for the finance team.", "type": "functional"}]
    raw = [{"title": "Manage users", "narrative": "As an admin, I want to manage users, so that access is controlled.", "acceptance_criteria": ["Given a, when b, then c"],
            "requirement_ids": ["REQ-9"], "epic": "Admin", "feature": "Finance"}]
    taken = {(pipeline._norm_key("Manage users"), pipeline._norm_key("Finance"))}  # an existing story with the same title and feature
    out = pipeline._finalize(raw, reqs, taken=taken)
    assert len(out) == 1 and out[0]["title"] != "Manage users" and out[0]["title"].startswith("Manage users"), out[0]["title"]
    assert pipeline._finalize(raw, reqs)[0]["title"] == "Manage users", "without existing stories the title is kept"
