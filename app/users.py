"""Named user accounts with roles, authenticated over HTTP Basic.

Roles: admin (everything), analyst (projects, no /api/admin), viewer (read-only).
The APP_BASIC_USER/APP_BASIC_PASSWORD account from the environment is a break-glass admin.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import APIRouter, Body, HTTPException, Request

from . import audit, config, db

_COMMENT_PATH = re.compile(r"^/api/projects/[^/]+/comments(/[^/]+/resolve)?$")
_OWN_TOKEN_PATH = re.compile(r"^/api/me/(tokens(/[^/]+)?|password)$")  # every role manages its own tokens and password
_SEARCH_PATH = re.compile(r"^/api/projects/[^/]+/search$")  # a read that happens to be a POST (the query is in the body)
ROLES = {"admin": "Administrator", "analyst": "Business analyst", "viewer": "Viewer (read-only)"}
_N, _R, _P = 2 ** 14, 8, 1
_CACHE_TTL = 300
_cache: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()
router = APIRouter(prefix="/api/admin/users")
me_router = APIRouter(prefix="/api/me/tokens")
admin_tokens_router = APIRouter(prefix="/api/admin/tokens")
account_router = APIRouter(prefix="/api/me")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode())


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = _scrypt(password, salt, _N, _R, _P)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        calc = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def auth_enabled() -> bool:
    return bool(config.BASIC_USER and config.BASIC_PASSWORD)


def authenticate(username: str, password: str) -> Optional[str]:
    """Return the user's role, or None. Successful and failed checks are cached briefly
    because scrypt is deliberately slow and Basic auth sends credentials on every request."""
    if (auth_enabled() and secrets.compare_digest(username, config.BASIC_USER)
            and secrets.compare_digest(password, config.BASIC_PASSWORD)):
        return "admin"
    if not username or not password:
        return None
    key = (username, hashlib.sha256(password.encode()).hexdigest())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    row = db.one("SELECT password_hash, role, disabled FROM users WHERE username = %s", (username,))
    role = row["role"] if row and not row["disabled"] and verify_password(password, row["password_hash"]) else None
    with _cache_lock:
        if len(_cache) > 1000:
            _cache.clear()
        _cache[key] = (now, role)
    return role


# ---- brute-force throttling ------------------------------------------------

FAIL_WINDOW = 300.0
FAIL_LIMIT = 10
_failures: Dict[str, List[float]] = {}


def throttled(key: str) -> bool:
    now = time.monotonic()
    with _cache_lock:
        recent = [t for t in _failures.get(key, []) if now - t < FAIL_WINDOW]
        if recent:
            _failures[key] = recent
        else:
            _failures.pop(key, None)
        return len(recent) >= FAIL_LIMIT


def record_failure(key: str) -> None:
    with _cache_lock:
        if len(_failures) > 10000:
            _failures.clear()
        _failures.setdefault(key, []).append(time.monotonic())


def clear_failures(key: str) -> None:
    with _cache_lock:
        _failures.pop(key, None)


_roles: Dict[str, Tuple[float, Optional[str]]] = {}


def role_of(username: str) -> Optional[str]:
    """Current role of an active named user (cached briefly; invalidated by forget())."""
    now = time.monotonic()
    with _cache_lock:
        hit = _roles.get(username)
        if hit and now - hit[0] < 60:
            return hit[1]
    row = db.one("SELECT role, disabled FROM users WHERE username = %s", (username,))
    role = row["role"] if row and not row["disabled"] else None
    with _cache_lock:
        _roles[username] = (now, role)
    return role


def provision_sso_user(username: str, auto_role: str) -> Optional[str]:
    """Role for an SSO sign-in: existing active user, or a new account when auto-provisioning is on."""
    row = db.one("SELECT role, disabled FROM users WHERE username = %s", (username,))
    if row:
        return None if row["disabled"] else row["role"]
    if auto_role not in ROLES:
        return None
    # SSO-only account: a random password nobody knows (Basic sign-in stays possible after a reset).
    db.execute("INSERT INTO users (id, username, password_hash, role, disabled, created) VALUES (%s,%s,%s,%s,FALSE,%s) "
               "ON CONFLICT (username) DO NOTHING",
               (db.new_id(), username, hash_password(secrets.token_urlsafe(32)), auto_role, db.now()))
    forget(username)
    return role_of(username)


def forget(username: str) -> None:
    with _cache_lock:
        _roles.pop(username, None)
        for key in [k for k in _cache if k[0] == username]:
            del _cache[key]


def allowed(role: str, method: str, path: str) -> bool:
    if role == "admin":
        return True
    if _OWN_TOKEN_PATH.match(path):
        return True
    if path.startswith("/api/admin/"):
        return False
    if role == "viewer":
        # Reviewers may read everything and take part in discussions, but not change the backlog.
        return method in ("GET", "HEAD") or (method == "POST" and bool(_COMMENT_PATH.match(path) or _SEARCH_PATH.match(path)))
    return role == "analyst"


def parse_basic(header: str) -> Optional[Tuple[str, str]]:
    if not header.lower().startswith("basic "):
        return None
    try:
        user, sep, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return None
    return (user, pw) if sep else None


# ---- admin API -----------------------------------------------------------

_touched: Dict[str, float] = {}  # username -> when this instance last wrote its last_seen
TOUCH_EVERY = 900.0  # seconds: "last active" to the quarter hour, without turning every request into a write


def touch_due(username: str) -> bool:
    with _cache_lock:
        return time.monotonic() - _touched.get(username, -TOUCH_EVERY * 2) >= TOUCH_EVERY


def touch(username: str) -> None:
    """Record that this account just used the API (a named account only: the bootstrap login has no row)."""
    with _cache_lock:
        _touched[username] = time.monotonic()
        if len(_touched) > 5000:
            _touched.clear()
    db.execute("UPDATE users SET last_seen=%s WHERE username=%s", (db.now(), username))


def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "username": r["username"], "role": r["role"], "role_label": ROLES.get(r["role"], r["role"]),
            "disabled": r["disabled"], "created": r["created"], "last_seen": r.get("last_seen")}


def _validate_password(pw: str) -> None:
    if len(pw) < 12:
        raise HTTPException(400, "Password must be at least 12 characters")


@router.get("")
def list_users():
    return {"users": [_public(r) for r in db.rows("SELECT * FROM users ORDER BY username")], "roles": ROLES,
            "bootstrap_user": config.BASIC_USER}


@router.post("", status_code=201)
def create_user(request: Request, body: Dict[str, Any] = Body(...)):
    username = str(body.get("username") or "").strip()
    role = str(body.get("role") or "analyst")
    password = str(body.get("password") or "")
    if not re.match(r"^[A-Za-z0-9._@-]{3,64}$", username):
        raise HTTPException(400, "Username: 3-64 characters (letters, digits, . _ @ -)")
    if username == config.BASIC_USER:
        raise HTTPException(400, "That name is reserved for the bootstrap admin")
    if role not in ROLES:
        raise HTTPException(400, "Unknown role")
    _validate_password(password)
    if db.one("SELECT 1 AS x FROM users WHERE username=%s", (username,)):
        raise HTTPException(409, "Username already exists")
    uid = db.new_id()
    db.execute("INSERT INTO users (id, username, password_hash, role, disabled, created) VALUES (%s,%s,%s,%s,FALSE,%s)",
               (uid, username, hash_password(password), role, db.now()))
    audit.record(request, "user created", username, "role " + role)
    return _public(db.one("SELECT * FROM users WHERE id=%s", (uid,)))


@router.patch("/{uid}")
def update_user(uid: str, request: Request, body: Dict[str, Any] = Body(...)):
    row = db.one("SELECT * FROM users WHERE id=%s", (uid,))
    if not row:
        raise HTTPException(404, "User not found")
    role = str(body.get("role", row["role"]))
    if role not in ROLES:
        raise HTTPException(400, "Unknown role")
    disabled = bool(body.get("disabled", row["disabled"]))
    pw_hash = row["password_hash"]
    if body.get("password"):
        _validate_password(str(body["password"]))
        pw_hash = hash_password(str(body["password"]))
    db.execute("UPDATE users SET role=%s, disabled=%s, password_hash=%s WHERE id=%s", (role, disabled, pw_hash, uid))
    forget(row["username"])
    if role != row["role"]:
        audit.record(request, "role changed", row["username"], "%s to %s" % (row["role"], role), {"role": {"from": row["role"], "to": role}})
    if disabled != row["disabled"]:
        audit.record(request, "user disabled" if disabled else "user enabled", row["username"])
    if body.get("password"):
        audit.record(request, "password reset", row["username"])
    return _public(db.one("SELECT * FROM users WHERE id=%s", (uid,)))


@router.delete("/{uid}", status_code=204)
def delete_user(uid: str, request: Request):
    row = db.one("SELECT username FROM users WHERE id=%s", (uid,))
    if not row:
        raise HTTPException(404, "User not found")
    db.execute("DELETE FROM users WHERE id=%s", (uid,))
    forget(row["username"])
    audit.record(request, "user deleted", row["username"])


def current(request: Request) -> Dict[str, str]:
    return {"username": getattr(request.state, "user", "anonymous"), "role": getattr(request.state, "role", "admin")}


# ---- personal API tokens ---------------------------------------------------
# For automation (CI, scripts, integrations) so nobody shares a password: `Authorization: Bearer fab_...`.
# The secret is shown once; only its SHA-256 is stored (the token is 256 random bits, so a fast hash is safe).
# A token carries its owner's current role, expires, and dies with the account.

TOKEN_PREFIX = "fab_"
MAX_TOKENS_PER_USER = 20
DEFAULT_TOKEN_DAYS, MAX_TOKEN_DAYS = 90, 365


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(header: str) -> Optional[str]:
    return header[7:].strip() if header[:7].lower() == "bearer " and header[7:].strip().startswith(TOKEN_PREFIX) else None


def authenticate_token(token: str) -> Optional[Tuple[str, str]]:
    """(username, role) for a valid, unexpired token of an active user, else None."""
    row = db.one("SELECT t.id, t.expires, t.last_used, u.username, u.role, u.disabled FROM api_tokens t "
                 "JOIN users u ON u.id = t.user_id WHERE t.token_hash = %s", (hash_token(token),))
    now = db.now()
    if not row or row["disabled"] or row["expires"] <= now:
        return None
    # Recording every call would turn each API request into a write; a minute of resolution is enough.
    if not row["last_used"] or (datetime.fromisoformat(now) - datetime.fromisoformat(row["last_used"])).total_seconds() > 60:
        db.execute("UPDATE api_tokens SET last_used=%s WHERE id=%s", (now, row["id"]))
    return row["username"], row["role"]


def _token_public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {k: r[k] for k in ("id", "name", "prefix", "created", "expires", "last_used") if k in r} | (
        {"owner": r["owner"]} if "owner" in r else {})


@me_router.get("")
def list_my_tokens(request: Request):
    me = _named_user(request)
    return [_token_public(r) for r in db.rows("SELECT * FROM api_tokens WHERE user_id=%s ORDER BY created DESC", (me["id"],))]


@me_router.post("", status_code=201)
def create_token(request: Request, body: Dict[str, Any] = Body(...)):
    me = _named_user(request)
    name = str(body.get("name") or "").strip()
    days = body.get("days", DEFAULT_TOKEN_DAYS)
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "Give the token a name (1-80 characters), e.g. the system that will use it")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_TOKEN_DAYS:
        raise HTTPException(400, "Lifetime must be 1-%d days" % MAX_TOKEN_DAYS)
    if db.one("SELECT count(*) AS n FROM api_tokens WHERE user_id=%s", (me["id"],))["n"] >= MAX_TOKENS_PER_USER:
        raise HTTPException(400, "Limit of %d tokens reached; revoke one you no longer use" % MAX_TOKENS_PER_USER)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    tid, now = db.new_id(), datetime.now(timezone.utc)
    expires = (now + timedelta(days=days)).isoformat(timespec="milliseconds")
    db.execute("INSERT INTO api_tokens (id, user_id, name, prefix, token_hash, created, expires) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               (tid, me["id"], name, token[:8], hash_token(token), db.now(), expires))
    audit.record(request, "token created", name, "%s, expires in %d days" % (current(request)["username"], days))
    return dict(_token_public(db.one("SELECT * FROM api_tokens WHERE id=%s", (tid,))), token=token)


@me_router.delete("/{tid}", status_code=204)
def revoke_my_token(tid: str, request: Request):
    me = _named_user(request)
    row = db.one("SELECT name FROM api_tokens WHERE id=%s AND user_id=%s", (tid, me["id"]))
    if not row:
        raise HTTPException(404, "Token not found")
    db.execute("DELETE FROM api_tokens WHERE id=%s", (tid,))
    audit.record(request, "token revoked", row["name"], current(request)["username"])


@account_router.post("/password")
def change_own_password(request: Request, body: Dict[str, Any] = Body(...)):
    """A named account changes its own password, proving it knows the current one. (The caller is already signed in with that password, so the
    sign-in throttle covers guessing it.) Not for the bootstrap login (set in the deployment) and not for a bearer token: a leaked token must not
    be able to take the account over."""
    if getattr(request.state, "via_token", False):
        raise HTTPException(403, "Change your password while signed in with your password, not with a token")
    username = current(request)["username"]
    row = db.one("SELECT id, password_hash FROM users WHERE username=%s", (username,))
    if not row:
        raise HTTPException(400, "Only named accounts have a password to change here; the bootstrap login is set in the deployment")
    old, new = str(body.get("current_password") or ""), str(body.get("new_password") or "")
    if not verify_password(old, row["password_hash"]):
        raise HTTPException(403, "The current password is wrong")
    _validate_password(new)
    if new == old:
        raise HTTPException(400, "Choose a password different from the current one")
    db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(new), row["id"]))
    forget(username)
    audit.record(request, "password changed", username, "by the account holder")
    return {"ok": True}


@admin_tokens_router.get("")
def list_all_tokens():
    return [_token_public(r) for r in db.rows(
        "SELECT t.*, u.username AS owner FROM api_tokens t JOIN users u ON u.id = t.user_id ORDER BY t.created DESC")]


@admin_tokens_router.delete("/{tid}", status_code=204)
def revoke_any_token(tid: str, request: Request):
    row = db.one("SELECT t.name, u.username AS owner FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.id=%s", (tid,))
    if not row:
        raise HTTPException(404, "Token not found")
    db.execute("DELETE FROM api_tokens WHERE id=%s", (tid,))
    audit.record(request, "token revoked by an administrator", row["name"], "owner " + row["owner"])


def _named_user(request: Request) -> Dict[str, Any]:
    row = db.one("SELECT id FROM users WHERE username=%s", (current(request)["username"],))
    if not row:
        raise HTTPException(400, "API tokens belong to a named account; the bootstrap and anonymous identities cannot own one")
    return row
