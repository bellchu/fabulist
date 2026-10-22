"""One runaway client is slowed down; everybody else, probes and the page itself are not."""
from app import config, ratelimit


def test_bucket_allows_a_burst_then_refuses_and_refills(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60)  # one per second, burst of 60
    ratelimit.reset()
    assert all(ratelimit.take("a", now=100.0) == 0 for _ in range(60))
    wait = ratelimit.take("a", now=100.0)
    assert 0.9 < wait <= 1.0
    assert ratelimit.take("a", now=100.5) > 0, "half a second is not enough"
    assert ratelimit.take("a", now=101.2) == 0, "a second later one more is allowed"
    assert ratelimit.take("b", now=100.0) == 0, "another person is unaffected"


def test_zero_switches_it_off(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    assert all(ratelimit.take("a") == 0 for _ in range(1000))


def test_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 50)
    ratelimit.reset()
    for i in range(200):
        ratelimit.take("idle-%d" % i, now=1.0)
    ratelimit.take("late", now=500.0)  # everyone idle has refilled by now, so they are dropped
    assert len(ratelimit._buckets) < 100


def test_api_answers_429_with_retry_after_for_a_runaway_client(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 5)
    ratelimit.reset()
    codes = [client.get("/api/projects").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429] * 3
    r = client.get("/api/projects")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "requests" in r.json()["detail"].lower()
    assert client.get("/healthz").status_code == 200 and client.get("/livez").status_code == 200, "probes are never limited"
    assert client.get("/static/js/core.js").status_code == 200, "the page's own files are not limited"
    assert client.get("/", follow_redirects=False).status_code == 200


def test_people_are_limited_separately(client, monkeypatch):
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    root = ("root", "root-password-123")
    assert client.post("/api/admin/users", json={"username": "other", "role": "analyst", "password": "other-password-1"}, auth=root).status_code == 201
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 3)
    ratelimit.reset()
    assert [client.get("/api/projects", auth=root).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.get("/api/projects", auth=("other", "other-password-1")).status_code == 200, "root's runaway loop does not cost other people their turn"
