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
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import APIRouter, Body, HTTPException, Request

from . import config, db

_COMMENT_PATH = re.compile(r"^/api/projects/[^/]+/comments(/[^/]+/resolve)?$")
ROLES = {"admin": "Administrator", "analyst": "Business analyst", "viewer": "Viewer (read-only)"}
_N, _R, _P = 2 ** 14, 8, 1
_CACHE_TTL = 300
_cache: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()
router = APIRouter(prefix="/api/admin/users")


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
    if path.startswith("/api/admin/"):
        return False
    if role == "viewer":
        # Reviewers may read everything and take part in discussions, but not change the backlog.
        return method in ("GET", "HEAD") or (method == "POST" and bool(_COMMENT_PATH.match(path)))
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

def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "username": r["username"], "role": r["role"], "role_label": ROLES.get(r["role"], r["role"]),
            "disabled": r["disabled"], "created": r["created"]}


def _validate_password(pw: str) -> None:
    if len(pw) < 12:
        raise HTTPException(400, "Password must be at least 12 characters")


@router.get("")
def list_users():
    return {"users": [_public(r) for r in db.rows("SELECT * FROM users ORDER BY username")], "roles": ROLES,
            "bootstrap_user": config.BASIC_USER}


@router.post("", status_code=201)
def create_user(body: Dict[str, Any] = Body(...)):
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
    return _public(db.one("SELECT * FROM users WHERE id=%s", (uid,)))


@router.patch("/{uid}")
def update_user(uid: str, body: Dict[str, Any] = Body(...)):
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
    return _public(db.one("SELECT * FROM users WHERE id=%s", (uid,)))


@router.delete("/{uid}", status_code=204)
def delete_user(uid: str):
    row = db.one("SELECT username FROM users WHERE id=%s", (uid,))
    if not row:
        raise HTTPException(404, "User not found")
    db.execute("DELETE FROM users WHERE id=%s", (uid,))
    forget(row["username"])


def current(request: Request) -> Dict[str, str]:
    return {"username": getattr(request.state, "user", "anonymous"), "role": getattr(request.state, "role", "admin")}
