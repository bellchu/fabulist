"""Extract only the BRDs that have not been read yet, in batches that fit the per-run limit, adding to the requirements that exist
(and leaving every reviewed or edited one alone). The offline provider keeps these tests off the network."""
import time

from app import db


def _doc(client, pid, name, sentences):
    text = "\n\n".join("# %s part %d\nThe system shall %s." % (name, i, s) for i, s in enumerate(sentences))
    return client.post("/api/projects/%s/documents/text" % pid, json={"name": name, "text": text}).json()


def _run(client, pid, documents="new"):
    r = client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements", "documents": documents})
    if r.status_code != 202:
        return r
    for _ in range(100):
        runs = client.get("/api/projects/%s/runs?limit=1" % pid).json()
        if runs[0]["status"] not in ("running", "queued"):
            return runs[0]
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _reqs(client, pid):
    return client.get("/api/projects/%s/requirements" % pid).json()


def _project(client, name="Incremental"):
    return client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]


def test_new_documents_are_extracted_and_added_without_touching_what_was_reviewed(client):
    pid = _project(client)
    _doc(client, pid, "first", ["export invoices to CSV on request", "email a receipt after payment is taken"])
    run = _run(client, pid)
    assert run["status"] == "done" and run["item_count"] == 2
    first = _reqs(client, pid)
    assert [r["key"] for r in first] == ["REQ-001", "REQ-002"]
    client.patch("/api/projects/%s/requirements/%s" % (pid, first[0]["id"]), json={"status": "approved", "notes": "Reviewed by finance."})

    _doc(client, pid, "second", ["lock an account after five failed sign-ins"])
    run = _run(client, pid)
    assert run["status"] == "done" and run["item_count"] == 1, "only the new BRD was read and only its requirement was added"
    after = _reqs(client, pid)
    assert [r["key"] for r in after] == ["REQ-001", "REQ-002", "REQ-003"], "numbering continues"
    kept = {r["id"]: r for r in after}[first[0]["id"]]
    assert kept["status"] == "approved" and kept["notes"] == "Reviewed by finance.", "a reviewed requirement is untouched"
    assert "lock an account" in after[2]["statement"] and after[2]["source_ref"].startswith("second")


def test_nothing_new_is_refused_clearly_and_all_still_starts_over(client):
    pid = _project(client)
    _doc(client, pid, "only", ["export invoices to CSV on request"])
    assert _run(client, pid)["status"] == "done"
    nothing = _run(client, pid)
    assert nothing.status_code == 400 and "No new BRDs" in nothing.json()["detail"], nothing.text
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall keep a manual requirement the analyst typed in."})
    again = _run(client, pid, documents="all")
    assert again["status"] == "done"
    assert [r["statement"] for r in _reqs(client, pid)] == ["The system shall export invoices to CSV on request."], "'all' replaces everything, as it always did"
    assert _run(client, pid).status_code == 400, "and it marks every BRD as read"


def test_a_requirement_repeated_in_a_later_brd_gains_a_source_instead_of_a_duplicate(client):
    pid = _project(client)
    _doc(client, pid, "first", ["export invoices to CSV on request"])
    _run(client, pid)
    _doc(client, pid, "second", ["export invoices to CSV on request", "archive closed orders after seven years"])
    assert _run(client, pid)["item_count"] == 1, "only the genuinely new requirement is added"
    reqs = _reqs(client, pid)
    assert ["export" in reqs[0]["statement"], "archive" in reqs[1]["statement"], len(reqs)] == [True, True, 2]
    assert "also second" in reqs[0]["source_ref"], reqs[0]["source_ref"]


def test_a_large_intake_is_read_in_batches_that_fit_the_section_limit(client):
    pid = _project(client)
    for n in range(5):
        _doc(client, pid, "brd%d" % n, ["handle thing %d for the buyer" % n, "record thing %d with the user and time" % n])  # 2 sections each
    client.put("/api/admin/settings", json={"max_chunks_per_run": 5})
    est = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "requirements", "documents": "new"}).json()
    assert est["requests"] == 4 and est["batch_documents"] == 2 and est["remaining_documents"] == 3 and est["over_limit"] is False, est
    done = []
    while True:
        r = _run(client, pid)
        if hasattr(r, "status_code"):
            break
        assert r["status"] == "done"
        done.append(r["item_count"])
    assert done == [4, 4, 2], "five BRDs in three runs, each within the limit of 5 sections: %s" % done
    assert len(_reqs(client, pid)) == 10
    assert client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "requirements", "documents": "new"}).json()["remaining_documents"] == 0


def test_starting_over_works_in_batches_too_and_only_replaces_when_its_first_batch_is_saved(client, monkeypatch):
    from app import pipeline
    pid = _project(client)
    for n in range(5):
        _doc(client, pid, "brd%d" % n, ["handle thing %d for the buyer" % n, "record thing %d with the user and time" % n])
    client.put("/api/admin/settings", json={"max_chunks_per_run": 5})
    while not hasattr(_run(client, pid), "status_code"):
        pass  # read everything in batches
    client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall keep a manual requirement the analyst typed in."})
    story = client.post("/api/projects/%s/stories" % pid, json={"title": "A story that goes with the old requirements"}).json()
    assert len(_reqs(client, pid)) == 11
    est = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "requirements", "documents": "reset"}).json()
    assert est["batch_documents"] == 2 and est["remaining_documents"] == 3 and est["requests"] == 4, est

    # a failure leaves everything as it was
    boom = pipeline.extract_requirements
    monkeypatch.setattr(pipeline, "extract_requirements", lambda *a, **k: (_ for _ in ()).throw(ValueError("provider down")))
    failed = _run(client, pid, documents="reset")
    assert failed["status"] == "failed" and len(_reqs(client, pid)) == 11, "a failed start-over replaces nothing"
    assert client.get("/api/projects/%s/documents" % pid).json()[0]["extracted"] is not None, "and the BRDs are still marked as read"
    monkeypatch.setattr(pipeline, "extract_requirements", boom)

    first = _run(client, pid, documents="reset")
    assert first["status"] == "done" and first["item_count"] == 4
    assert [r["key"] for r in _reqs(client, pid)] == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"], "replaced by the first batch, numbering from the start"
    assert client.get("/api/projects/%s/stories" % pid).json() == [] and story["id"], "the old backlog went with them"
    left = client.get("/api/projects/%s/runs/estimate" % pid, params={"stage": "requirements", "documents": "new"}).json()
    assert left["new_documents"] == 3, "the rest are New again"
    while not hasattr(_run(client, pid), "status_code"):
        pass
    assert len(_reqs(client, pid)) == 10


def test_one_brd_larger_than_the_limit_is_refused_with_a_way_forward(client):
    pid = _project(client)
    _doc(client, pid, "huge", ["do thing %d" % i for i in range(6)])  # 6 sections
    client.put("/api/admin/settings", json={"max_chunks_per_run": 3})
    r = _run(client, pid)
    assert r.status_code == 400 and "huge" in r.json()["detail"] and "split" in r.json()["detail"].lower(), r.text


def test_documents_say_whether_they_have_been_read(client):
    pid = _project(client)
    _doc(client, pid, "a", ["export invoices to CSV on request"])
    _run(client, pid)
    _doc(client, pid, "b", ["archive closed orders after seven years"])
    assert {d["filename"]: d["extracted"] is not None for d in client.get("/api/projects/%s/documents" % pid).json()} == {"a": True, "b": False}


def test_the_upgrade_marks_documents_of_projects_that_already_have_requirements_as_read(client):
    pid = db.new_id()
    db.execute("INSERT INTO projects (id, name, platform, description, created) VALUES (%s,'Old','generic','',%s)", (pid, db.now()))
    empty = db.new_id()
    db.execute("INSERT INTO projects (id, name, platform, description, created) VALUES (%s,'Empty','generic','',%s)", (empty, db.now()))
    for project, name in ((pid, "old.md"), (empty, "unread.md")):
        db.execute("INSERT INTO documents (id, project_id, filename, text, created) VALUES (%s,%s,%s,'text',%s)", (db.new_id(), project, name, db.now()))
    db.execute("INSERT INTO requirements (id, project_id, seq, data) VALUES (%s,%s,0,'{}')", (db.new_id(), pid))
    db.execute("ALTER TABLE documents DROP COLUMN extracted")  # the state of an installation that has not upgraded yet
    db.init()
    got = {r["filename"]: r["extracted"] is not None for r in db.rows("SELECT filename, extracted FROM documents WHERE filename IN ('old.md', 'unread.md')")}
    assert got == {"old.md": True, "unread.md": False}, "documents already behind existing requirements count as read; the rest are new"
    db.execute("DELETE FROM projects WHERE id IN (%s, %s)", (pid, empty))
