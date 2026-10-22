import os
import time

import pytest

# Tests need a disposable Postgres database with pgvector, e.g. TEST_DATABASE_URL=.../fabulist_test.
TEST_DB = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def instant_retry_waits(monkeypatch):
    """Retries wait up to a minute between attempts (patient on purpose); a test of a failing provider must not."""
    from app import llm
    monkeypatch.setattr(llm, "_sleep", lambda seconds: llm._check_cancelled())  # still notices a cancel, but does not wait
    llm._cooldowns.clear()


@pytest.fixture()
def client(monkeypatch):
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from app import config, db
    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(config, "SETTINGS_KEY", "test-encryption-key")
    monkeypatch.setattr(config, "BASIC_USER", "")
    db.init()
    with db.conn() as c:
        c.execute("TRUNCATE projects, providers, settings, runs, integrations, users, product_classes, attribute_rules CASCADE")
    from fastapi.testclient import TestClient
    from app.main import app
    from app import taxonomy
    taxonomy.invalidate()
    with TestClient(app, headers={"X-Requested-With": "fabulist"}) as c:
        yield c
        # A background AI run must not outlive its test: on a slow machine it would run into the next one
        # (and touch shared state such as the taxonomy cache).
        for _ in range(150):
            if not db.one("SELECT 1 AS x FROM runs WHERE status IN ('running', 'queued')"):
                break
            time.sleep(0.2)
