"""Every response carries a request id, the same id is in the logs, and an unexpected error answers with it: the one thing a person can quote to support."""
import logging
import re

from fastapi.testclient import TestClient


def test_every_response_has_a_request_id(client):
    r = client.get("/api/meta")
    assert re.fullmatch(r"[0-9a-f]{12}", r.headers["x-request-id"])
    assert client.get("/api/meta").headers["x-request-id"] != r.headers["x-request-id"]
    for path in ("/healthz", "/livez", "/", "/static/app.css", "/api/projects/nope", "/nothing-here"):
        assert "x-request-id" in client.get(path).headers, path


def test_a_sensible_id_from_the_caller_is_kept_and_a_hostile_one_is_replaced(client):
    assert client.get("/api/meta", headers={"X-Request-ID": "build-1234.trace_ab"}).headers["x-request-id"] == "build-1234.trace_ab"
    for hostile in ("short", "x" * 100, "bad id with spaces", "semi;colon;12345", "<script>alert(1)</script>"):
        got = client.get("/api/meta", headers={"X-Request-ID": hostile}).headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{12}", got), hostile


def test_the_access_log_line_has_the_same_id(client, caplog):
    with caplog.at_level(logging.INFO):
        rid = client.get("/api/meta", headers={"X-Request-ID": "trace-abcdef12"}).headers["x-request-id"]
    lines = [r for r in caplog.records if getattr(r, "fields", {}).get("path") == "/api/meta"]
    assert lines and lines[-1].fields["request_id"] == rid == "trace-abcdef12"


def test_an_unexpected_error_answers_with_its_id_and_logs_the_stack(client, monkeypatch, caplog):
    from app import main

    def boom():
        raise RuntimeError("secret internal detail")
    main.app.add_api_route("/api/_boom", boom)
    try:
        with TestClient(main.app, raise_server_exceptions=False, headers={"X-Requested-With": "fabulist"}) as c, caplog.at_level(logging.ERROR):
            r = c.get("/api/_boom", headers={"X-Request-ID": "trace-boom0001"})
    finally:
        main.app.router.routes[:] = [x for x in main.app.router.routes if getattr(x, "path", "") != "/api/_boom"]
    assert r.status_code == 500 and r.headers["x-request-id"] == "trace-boom0001"
    body = r.json()
    assert body["request_id"] == "trace-boom0001" and "trace-boom0001" in body["detail"]
    assert "secret internal detail" not in r.text, "no internals in the answer"
    logged = [x for x in caplog.records if getattr(x, "fields", {}).get("request_id") == "trace-boom0001" and x.exc_info]
    assert logged, "the stack trace is logged under the same id"
