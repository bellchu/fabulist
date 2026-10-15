import os

import pytest

# Tests need a disposable Postgres database with pgvector, e.g. TEST_DATABASE_URL=.../fabulist_test.
TEST_DB = os.environ.get("TEST_DATABASE_URL", "")


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
        c.execute("TRUNCATE projects, providers, settings, runs CASCADE")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={"X-Requested-With": "fabulist"}) as c:
        yield c
