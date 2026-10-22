"""SUPPORT_CONTACT: where people are told to turn when something goes wrong (a helpdesk address, a chat channel), shown in the footer and in error references."""
from fastapi.testclient import TestClient

from app import config


def test_nothing_changes_without_a_contact(client):
    assert client.get("/api/meta").json()["support_contact"] == ""


def test_the_contact_is_in_the_meta_and_in_the_error_reference(client, monkeypatch):
    from app import main
    monkeypatch.setattr(config, "SUPPORT_CONTACT", "helpdesk@example.com or #fabulist-help")
    assert client.get("/api/meta").json()["support_contact"] == "helpdesk@example.com or #fabulist-help"

    def boom():
        raise RuntimeError("internal detail")
    main.app.add_api_route("/api/_boom2", boom)
    try:
        with TestClient(main.app, raise_server_exceptions=False, headers={"X-Requested-With": "fabulist"}) as c:
            r = c.get("/api/_boom2")
    finally:
        main.app.router.routes[:] = [x for x in main.app.router.routes if getattr(x, "path", "") != "/api/_boom2"]
    assert r.status_code == 500
    assert "helpdesk@example.com or #fabulist-help" in r.json()["detail"] and r.json()["request_id"] in r.json()["detail"]
    assert "internal detail" not in r.text
