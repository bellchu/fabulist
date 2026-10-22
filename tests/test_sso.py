"""OIDC single sign-on against a fake identity provider."""
import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import sso, users

ISSUER = "https://idp.example.com"


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return "%s.%s.sig" % (enc({"alg": "RS256"}), enc(claims))


@pytest.fixture()
def idp(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "fabulist")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(config, "OIDC_REDIRECT_URL", "http://testserver/auth/callback")
    monkeypatch.setattr(config, "OIDC_AUTO_PROVISION_ROLE", "")
    sso._discovery.update(at=0.0, doc=None)
    users._roles.clear()
    state = {"claims": {}, "token_requests": []}

    def fake_get(url, timeout=None):
        assert url == ISSUER + "/.well-known/openid-configuration"
        return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                         "token_endpoint": ISSUER + "/token"}, request=httpx.Request("GET", url))

    def fake_post(url, data=None, timeout=None):
        state["token_requests"].append(data)
        return httpx.Response(200, json={"id_token": _jwt(state["claims"]), "access_token": "x"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(sso.httpx, "get", fake_get)
    monkeypatch.setattr(sso.httpx, "post", fake_post)
    return state


def _sign_in(client, idp, email, **overrides):
    r = client.get("/auth/login?next=/%23/admin", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["fabulist"]
    idp["claims"] = dict({"iss": ISSUER, "aud": "fabulist", "exp": time.time() + 300, "nonce": q["nonce"][0],
                          "email": email, "email_verified": True}, **overrides)
    return client.get("/auth/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)


def test_unauthenticated_requests_are_sent_to_idp(client, idp):
    assert client.get("/api/projects").status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/auth/login?next=/"
    assert client.get("/static/app.css").status_code == 200, "static assets stay public for the sign-in pages"
    # regression: empty Basic credentials must never authenticate
    assert client.get("/api/projects", auth=("", "")).status_code == 401


def test_sign_in_with_auto_provisioning_and_roles(client, idp, monkeypatch):
    from app import config
    r = _sign_in(client, idp, "ana@example.com")
    assert r.status_code == 403 and "has not been given access" in r.text
    monkeypatch.setattr(config, "OIDC_AUTO_PROVISION_ROLE", "viewer")
    r = _sign_in(client, idp, "Ana@Example.com")
    assert r.status_code == 302 and r.headers["location"] == "/#/admin"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    token_req = idp["token_requests"][-1]
    assert token_req["code_verifier"] and token_req["client_secret"] == "s3cret" and token_req["grant_type"] == "authorization_code"
    assert client.get("/api/me").json() == {"username": "ana@example.com", "role": "viewer", "sso": True, "named": True}
    assert client.post("/api/projects", json={"name": "x", "platform": "generic"}).status_code == 403
    # an admin promotes the user; the change applies to the live session
    from app import db
    db.execute("UPDATE users SET role='analyst' WHERE username='ana@example.com'")
    users.forget("ana@example.com")
    assert client.post("/api/projects", json={"name": "x", "platform": "generic"}).status_code == 201
    client.get("/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_rejects_tampered_or_invalid_tokens(client, idp, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OIDC_AUTO_PROVISION_ROLE", "analyst")
    assert "nonce mismatch" in _sign_in(client, idp, "bob@example.com", nonce="evil").text
    assert "audience mismatch" in _sign_in(client, idp, "bob@example.com", aud="someone-else").text
    assert "issuer mismatch" in _sign_in(client, idp, "bob@example.com", iss="https://evil.example").text
    assert "expired" in _sign_in(client, idp, "bob@example.com", exp=time.time() - 3600).text
    assert _sign_in(client, idp, "bob@example.com", email_verified=False).status_code == 403
    assert client.get("/auth/callback", params={"code": "abc", "state": "forged"}, follow_redirects=False).status_code == 400
    assert _sign_in(client, idp, "bob@example.com").status_code == 302
    good = client.cookies.get(sso.SESSION_COOKIE)
    assert client.get("/api/me").status_code == 200
    body, sig = good.split(".")
    forged = sso._b64(json.dumps({"u": "admin@example.com", "exp": time.time() + 999}).encode()) + "." + sig
    for value in (forged, good[:-2] + ("AA" if not good.endswith("AA") else "BB")):
        client.cookies.clear()
        client.cookies.set(sso.SESSION_COOKIE, value)
        assert client.get("/api/me").status_code == 401


def test_next_parameter_cannot_redirect_offsite(client, idp, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OIDC_AUTO_PROVISION_ROLE", "viewer")
    r = client.get("/auth/login?next=//evil.example/x", follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    idp["claims"] = {"iss": ISSUER, "aud": ["fabulist"], "exp": time.time() + 300, "nonce": q["nonce"][0], "email": "c@example.com"}
    r = client.get("/auth/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"] == "/"


def _issued(response):
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    return sso.verify(jar[sso.SESSION_COOKIE].value)


def _use(client, token):
    client.cookies.clear()  # a refreshed cookie from an earlier request must not shadow this one
    client.cookies.set(sso.SESSION_COOKIE, token)


def _session_cookie(monkeypatch, seen_ago, left=3600):
    from app import config, db
    monkeypatch.setattr(config, "OIDC_AUTO_PROVISION_ROLE", "analyst")
    db.execute("INSERT INTO users (id, username, role, password_hash, created) VALUES ('u1','ana@example.com','analyst','x','now') ON CONFLICT DO NOTHING")
    users._roles.clear()
    now = time.time()
    return sso.sign({"u": "ana@example.com", "exp": now + left, "seen": now - seen_ago})


def test_idle_sessions_expire_and_active_ones_slide(client, idp, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "SESSION_IDLE_MINUTES", 30)
    # idle for longer than the limit: signed out even though the absolute lifetime remains
    _use(client, _session_cookie(monkeypatch, seen_ago=31 * 60))
    assert client.get("/api/me").status_code == 401
    # recently active: accepted, and the cookie is re-issued with a fresh activity time
    _use(client, _session_cookie(monkeypatch, seen_ago=10 * 60))
    r = client.get("/api/me")
    assert r.status_code == 200 and sso.SESSION_COOKIE in r.headers["set-cookie"]
    refreshed = _issued(r)
    assert time.time() - refreshed["seen"] < 5
    # just refreshed: no new cookie on every request
    _use(client, _session_cookie(monkeypatch, seen_ago=5))
    assert "set-cookie" not in client.get("/api/me").headers
    # the absolute lifetime is never extended by activity
    _use(client, _session_cookie(monkeypatch, seen_ago=10 * 60, left=120))
    assert _issued(client.get("/api/me"))["exp"] < time.time() + 130


def test_idle_timeout_can_be_disabled_and_legacy_cookies_still_work(client, idp, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "SESSION_IDLE_MINUTES", 0)
    _use(client, _session_cookie(monkeypatch, seen_ago=5 * 3600))
    assert client.get("/api/me").status_code == 200
    monkeypatch.setattr(config, "SESSION_IDLE_MINUTES", 30)
    legacy = sso.sign({"u": "ana@example.com", "exp": time.time() + 3600})  # issued before idle tracking existed
    _use(client, legacy)
    assert client.get("/api/me").status_code == 200
