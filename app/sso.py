"""OpenID Connect single sign-on: authorization code flow with PKCE and signed session cookies.

The ID token comes straight from the token endpoint over TLS, so per OIDC Core 3.1.3.7 the TLS
channel authenticates the issuer; we still validate iss, aud, exp and nonce.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import config, users

log = logging.getLogger("fabulist.sso")
router = APIRouter()
SESSION_COOKIE = "fabulist_session"
FLOW_COOKIE = "fabulist_oidc"
SESSION_TTL = 8 * 3600
FLOW_TTL = 600
_discovery: Dict[str, Any] = {"at": 0.0, "doc": None}


class SSOError(Exception):
    pass


def enabled() -> bool:
    return bool(config.OIDC_ISSUER and config.OIDC_CLIENT_ID and config.OIDC_REDIRECT_URL)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    secret = config.SETTINGS_KEY or config.BASIC_PASSWORD
    if not secret:
        raise SSOError("SETTINGS_ENCRYPTION_KEY is required to sign sessions")
    return hashlib.sha256(("fabulist-session:" + secret).encode()).digest()


def sign(payload: Dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def verify(token: str) -> Optional[Dict[str, Any]]:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()), sig):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, SSOError):
        return None
    return payload if payload.get("exp", 0) > time.time() else None


def discover() -> Dict[str, Any]:
    if _discovery["doc"] and time.monotonic() - _discovery["at"] < 3600:
        return _discovery["doc"]
    try:
        resp = httpx.get(config.OIDC_ISSUER + "/.well-known/openid-configuration", timeout=10)
        resp.raise_for_status()
        doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SSOError("Identity provider discovery failed: %s" % type(exc).__name__)
    if doc.get("issuer", "").rstrip("/") != config.OIDC_ISSUER:
        raise SSOError("Identity provider issuer mismatch")
    _discovery.update(at=time.monotonic(), doc=doc)
    return doc


def _secure() -> bool:
    return config.OIDC_REDIRECT_URL.startswith("https://")


def _safe_next(target: str) -> str:
    return target if re.match(r"^/(?!/)[^\s]*$", target or "") else "/"


def _page(title: str, message: str, status: int = 200) -> HTMLResponse:
    from html import escape
    body = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>%s</title>"
            "<link rel='stylesheet' href='/static/app.css'></head><body><div class='content'><div class='card card-body'>"
            "<h1>%s</h1><p class='muted'>%s</p><p><a class='btn btn-primary' href='/auth/login'>Sign in</a></p>"
            "</div></div></body></html>") % (escape(title), escape(title), escape(message))
    return HTMLResponse(body, status_code=status)


@router.get("/auth/login", include_in_schema=False)
def login(next: str = "/") -> Response:
    if not enabled():
        return Response(status_code=404)
    try:
        doc = discover()
        verifier = secrets.token_urlsafe(48)
        flow = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": verifier,
                "next": _safe_next(next), "exp": time.time() + FLOW_TTL}
        cookie = sign(flow)
    except SSOError as exc:
        return _page("Sign-in unavailable", str(exc), 503)
    query = urlencode({"response_type": "code", "client_id": config.OIDC_CLIENT_ID, "redirect_uri": config.OIDC_REDIRECT_URL,
                       "scope": config.OIDC_SCOPES, "state": flow["state"], "nonce": flow["nonce"],
                       "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"})
    resp = RedirectResponse(doc["authorization_endpoint"] + ("&" if "?" in doc["authorization_endpoint"] else "?") + query, status_code=302)
    resp.set_cookie(FLOW_COOKIE, cookie, max_age=FLOW_TTL, httponly=True, secure=_secure(), samesite="lax", path="/auth")
    return resp


def _claims(code: str, flow: Dict[str, Any]) -> Dict[str, Any]:
    doc = discover()
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": config.OIDC_REDIRECT_URL,
            "client_id": config.OIDC_CLIENT_ID, "code_verifier": flow["verifier"]}
    if config.OIDC_CLIENT_SECRET:
        data["client_secret"] = config.OIDC_CLIENT_SECRET
    try:
        resp = httpx.post(doc["token_endpoint"], data=data, timeout=15)
        resp.raise_for_status()
        id_token = resp.json()["id_token"]
        claims = json.loads(_unb64(id_token.split(".")[1]))
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise SSOError("Token exchange failed: %s" % type(exc).__name__)
    aud = claims.get("aud")
    if str(claims.get("iss", "")).rstrip("/") != config.OIDC_ISSUER:
        raise SSOError("ID token issuer mismatch")
    if config.OIDC_CLIENT_ID not in (aud if isinstance(aud, list) else [aud]):
        raise SSOError("ID token audience mismatch")
    if claims.get("exp", 0) < time.time() - 60:
        raise SSOError("ID token expired")
    if not hmac.compare_digest(str(claims.get("nonce", "")), flow["nonce"]):
        raise SSOError("ID token nonce mismatch")
    return claims


@router.get("/auth/callback", include_in_schema=False)
def callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    if not enabled():
        return Response(status_code=404)
    flow = verify(request.cookies.get(FLOW_COOKIE, ""))
    if error:
        return _page("Sign-in was not completed", "The identity provider returned: %s" % error[:100], 400)
    if not flow or not code or not hmac.compare_digest(state, flow.get("state", "")):
        return _page("Sign-in expired", "Please start the sign-in again.", 400)
    try:
        claims = _claims(code, flow)
    except SSOError as exc:
        log.warning("sso failure", extra={"fields": {"error": str(exc)}})
        return _page("Sign-in failed", str(exc), 400)
    username = str(claims.get(config.OIDC_USERNAME_CLAIM) or claims.get("preferred_username") or "").strip().lower()[:64]
    if claims.get("email_verified") is False and config.OIDC_USERNAME_CLAIM == "email":
        return _page("Email not verified", "Verify your email with the identity provider, then sign in again.", 403)
    role = users.provision_sso_user(username, config.OIDC_AUTO_PROVISION_ROLE) if username else None
    if not role:
        log.warning("sso user not allowed", extra={"fields": {"user": username}})
        return _page("Access not granted", "Your account (%s) has not been given access to Fabulist. Ask an administrator." % username, 403)
    log.info("sso sign-in", extra={"fields": {"user": username, "role": role}})
    resp = RedirectResponse(flow.get("next") or "/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, _session_cookie(username, time.time() + SESSION_TTL, time.time()), max_age=SESSION_TTL,
                    httponly=True, secure=_secure(), samesite="lax", path="/")
    resp.delete_cookie(FLOW_COOKIE, path="/auth")
    return resp


@router.get("/auth/logout", include_in_schema=False)
def logout() -> Response:
    resp = _page("Signed out", "You have been signed out of Fabulist.")
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


_TOUCH_AFTER = 60  # re-issue the cookie at most once a minute


def _session_cookie(username: str, exp: float, seen: float) -> str:
    return sign({"u": username, "exp": exp, "seen": seen})


def session_user(request: Request) -> Optional[Tuple[str, str]]:
    payload = verify(request.cookies.get(SESSION_COOKIE, ""))
    if not payload:
        return None
    now = time.time()
    seen = payload.get("seen")  # cookies issued before idle tracking have none: count them as active now
    if config.SESSION_IDLE_MINUTES > 0 and seen is not None and now - seen > config.SESSION_IDLE_MINUTES * 60:
        return None
    role = users.role_of(payload.get("u", ""))
    if not role:
        return None
    if seen is None or now - seen > _TOUCH_AFTER:
        request.state.session_refresh = payload
    return payload["u"], role


def refresh_cookie(request: Request, response: Response) -> None:
    """Slide the idle window: same absolute expiry, new activity time (see main's middleware)."""
    payload = getattr(request.state, "session_refresh", None)
    if not payload or response.status_code >= 500:
        return
    exp = payload["exp"]
    response.set_cookie(SESSION_COOKIE, _session_cookie(payload["u"], exp, time.time()), max_age=max(1, int(exp - time.time())),
                        httponly=True, secure=_secure(), samesite="lax", path="/")
