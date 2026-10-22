"""Interactive AI calls (Ask, Refine, Estimate) share a small pool; one person must not be able to take all of it and lock everyone else out."""
import threading

import pytest

from app import config, llm, pipeline

ROOT = ("root", "root-password-123")
ALICE, BOB = ("alice", "alice-password-1"), ("bob", "bob-password-123")


@pytest.fixture()
def two_users(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", ROOT[1])
    for name, who in (("alice", ALICE), ("bob", BOB)):
        assert client.post("/api/admin/users", json={"username": name, "role": "analyst", "password": who[1]}, auth=ROOT).status_code == 201
    pid = client.post("/api/projects", json={"name": "Shared", "platform": "generic"}, auth=ROOT).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV."}, auth=ROOT)
    llm._reset_interactive_slots()
    return client, pid


def test_one_person_cannot_hold_more_than_their_share_of_the_pool(two_users, monkeypatch):
    client, pid = two_users
    monkeypatch.setattr(config, "MAX_INTERACTIVE_PER_USER", 2)
    release, started = threading.Event(), threading.Semaphore(0)

    def slow_answer(question, passages, profile, opts, usage):
        started.release()
        release.wait(20)
        return "An answer."
    monkeypatch.setattr(pipeline, "answer_question", slow_answer)
    ask = lambda who: client.post("/api/projects/%s/ask" % pid, json={"question": "What is exported?"}, auth=who)  # noqa: E731
    results = []
    threads = [threading.Thread(target=lambda: results.append(ask(ALICE).status_code)) for _ in range(2)]
    [t.start() for t in threads]
    try:
        assert started.acquire(timeout=10) and started.acquire(timeout=10), "alice's two calls are both running"
        third = ask(ALICE)
        assert third.status_code == 429 and third.headers["retry-after"] and "already have 2" in third.json()["detail"], third.text
        bob = []
        t = threading.Thread(target=lambda: bob.append(ask(BOB).status_code))
        t.start()
        assert started.acquire(timeout=10), "bob is not held up by alice's calls"
    finally:
        release.set()
    [t.join(20) for t in threads]
    t.join(20)
    assert sorted(results) == [200, 200] and bob == [200]
    assert ask(ALICE).status_code == 200, "alice can ask again once hers have finished"


def test_the_share_is_released_when_a_call_fails(two_users, monkeypatch):
    client, pid = two_users
    monkeypatch.setattr(config, "MAX_INTERACTIVE_PER_USER", 1)
    monkeypatch.setattr(pipeline, "answer_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the provider refused")))
    for _ in range(3):  # each failing call must give its place back, or the third would be a 429
        r = client.post("/api/projects/%s/ask" % pid, json={"question": "What is exported?"}, auth=ALICE)
        assert r.status_code == 502, r.text


def test_every_interactive_ai_route_is_covered():
    from app import main
    routes = []
    for r in main.app.routes:  # included routers are wrapped; their own routes (with their dependencies) are on original_router
        routes += list(r.original_router.routes) if hasattr(r, "original_router") else [r]
    covered = {r.path for r in routes if any(getattr(d, "dependency", None) is not None and d.dependency.__name__ == "ai_fair_use" for d in getattr(r, "dependencies", []))}
    assert covered >= {"/api/projects/{pid}/ask", "/api/projects/{pid}/requirements/{rid}/refine", "/api/projects/{pid}/stories/{sid}/refine",
                       "/api/projects/{pid}/stories/estimate", "/api/admin/providers/{pid}/test"}, covered
