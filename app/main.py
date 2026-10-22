import json
import logging
import secrets
from urllib.parse import quote
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

import psycopg
import psycopg_pool
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import admin, config, db, exporters, integrations, llm, metrics, pipeline, portability, repos, settings, sso, taxonomy, users, workspace
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
CSRF_HEADER = "fabulist"
SECURITY_HEADERS = {
    # All scripts and styles are served from /static; inline style properties are set via CSSOM only.
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def recover_runs_after_restart() -> None:
    """A restart kills the runs this server was executing, which cannot be resumed (they would start over and spend the AI cost again), and
    empties its in-memory queue, which can: runs that were only waiting for a slot are started again, in order."""
    db.execute("UPDATE runs SET status='failed', error='Interrupted by server restart' WHERE status='running'")
    workspace.resume_waiting_runs()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    settings.seed()
    taxonomy.seed()
    recover_runs_after_restart()
    yield
    workspace.shutdown_runs()
    db.close()


# Read-only POSTs and the archive toggles stay available on an archived project.
_ARCHIVE_EXEMPT = ("/search", "/ask", "/archive", "/restore")


def archived_is_read_only(request: Request) -> None:
    pid = request.path_params.get("pid")
    if not pid or request.method in ("GET", "HEAD", "OPTIONS") or request.url.path.endswith(_ARCHIVE_EXEMPT):
        return
    if request.method == "DELETE" and request.url.path == "/api/projects/" + pid:
        return  # permanent deletion has its own rules
    row = db.one("SELECT archived FROM projects WHERE id=%s", (pid,))
    if row and row["archived"]:
        raise HTTPException(409, "This project is archived and read-only. Restore it to make changes.")


app = FastAPI(title="Fabulist", lifespan=lifespan, dependencies=[Depends(archived_is_read_only)])
app.include_router(admin.router)
app.include_router(repos.router)
app.include_router(integrations.router)
app.include_router(users.router)
app.include_router(users.me_router)
app.include_router(users.admin_tokens_router)
app.include_router(portability.router)
app.include_router(workspace.router)
app.include_router(sso.router)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname.lower(),
                 "logger": record.name, "msg": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.getLogger("fabulist").handlers[:] = [_handler]
logging.getLogger("fabulist").setLevel(logging.INFO)
logging.getLogger("fabulist").propagate = False
access_log = logging.getLogger("fabulist.access")


@app.middleware("http")
async def auth_and_guards(request: Request, call_next):
    request.state.user, request.state.role = "anonymous", "admin"
    path = request.url.path
    if path.startswith("/api/"):
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if declared > config.max_body_bytes(path):
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    if path == "/metrics":
        # Scraped by Prometheus with a bearer token; never behind user Basic auth.
        token = request.headers.get("authorization", "")
        if not config.METRICS_TOKEN or not secrets.compare_digest(token, "Bearer " + config.METRICS_TOKEN):
            return Response(status_code=404)
        return await call_next(request)
    session = None
    if sso.enabled() and not path.startswith("/auth/"):
        session = await run_in_threadpool(sso.session_user, request)
        if session:
            request.state.user, request.state.role = session
    if session:
        if path.startswith("/api/") and not users.allowed(session[1], request.method, path):
            return JSONResponse({"detail": "Your role (%s) does not allow this action" % session[1]}, status_code=403)
    elif sso.enabled() and not path.startswith(("/auth/", "/static/")) and path != "/healthz" and "authorization" not in request.headers:
        # Browsers without a session go to the identity provider; API clients get a plain 401.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return RedirectResponse("/auth/login?next=" + quote(path), status_code=302)
    elif ((users.auth_enabled() or sso.enabled()) and path != "/healthz" and not path.startswith("/auth/")
          and not (sso.enabled() and path.startswith("/static/"))):
        token = users.bearer_token(request.headers.get("authorization", ""))
        creds = None if token else users.parse_basic(request.headers.get("authorization", ""))
        # Throttle per username and per client address (Cloudflare/Traefik forward the real IP).
        client_ip = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                     or (request.client.host if request.client else ""))
        keys = ["ip:" + client_ip] + (["user:" + creds[0]] if creds else [])
        if any(users.throttled(k) for k in keys):
            metrics.inc("fabulist_auth_failures_total", kind="throttled")
            return JSONResponse({"detail": "Too many failed sign-in attempts; try again in a few minutes"}, status_code=429,
                                headers={"Retry-After": str(int(users.FAIL_WINDOW))})
        if token:
            who = await run_in_threadpool(users.authenticate_token, token)
            role = who[1] if who else None
        else:
            role = await run_in_threadpool(users.authenticate, *creds) if creds else None
        if not role:
            if creds or token:
                metrics.inc("fabulist_auth_failures_total", kind="token" if token else "basic")
                for k in keys:
                    users.record_failure(k)
                log.warning("failed sign-in", extra={"fields": {"user": creds[0][:80] if creds else "(token)", "ip": client_ip}})
            return Response(status_code=401, headers={"WWW-Authenticate": 'Bearer realm="Fabulist"' if token else 'Basic realm="Fabulist"'})
        if creds:
            users.clear_failures("user:" + creds[0])
        request.state.user, request.state.role, request.state.via_token = (who[0] if token else creds[0]), role, bool(token)
        if path.startswith("/api/") and not users.allowed(role, request.method, path):
            return JSONResponse({"detail": "Your role (%s) does not allow this action" % role}, status_code=403)
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
            and not getattr(request.state, "via_token", False)  # a bearer token is never sent by a browser on its own
            and request.headers.get("x-requested-with") != CSRF_HEADER):
        # Browsers resend Basic-auth credentials cross-site; a custom header can't be sent
        # cross-origin without a CORS preflight (which we never allow), so require it.
        return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    response = await call_next(request)
    sso.refresh_cookie(request, response)
    if path.startswith("/static/"):
        # Revalidate via ETag so a deploy is picked up without a hard refresh.
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")  # backlog data and exports must not sit in shared caches
    if request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)  # a route may set a stricter/different policy (e.g. the HTML report)
    return response


# Registered last so it wraps auth: rejected requests are logged and counted too.
@app.middleware("http")
async def observe(request: Request, call_next):
    """JSON access log line and Prometheus metrics per request (route templates, not raw paths)."""
    started = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", None) or ("/static" if request.url.path.startswith("/static/") else "other")
        elapsed = time.monotonic() - started
        if request.url.path not in ("/healthz", "/metrics"):
            metrics.inc("fabulist_http_requests_total", method=request.method, route=route, status=str(status))
            metrics.observe_request(route, elapsed)
            access_log.info("request", extra={"fields": {"method": request.method, "route": route, "path": request.url.path,
                                                         "status": status, "ms": int(elapsed * 1000),
                                                         "user": getattr(request.state, "user", "")}})


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/api/me")
def me(request: Request) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(users.current(request))
    out["sso"] = bool(sso.enabled() and request.cookies.get(sso.SESSION_COOKIE))
    return out


@app.exception_handler(psycopg_pool.PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(_request: Request, exc: Exception):
    # Postgres down, restarting, failing over or saturated: a short, retryable answer instead of a stack trace.
    log.warning("database unavailable", extra={"fields": {"error": type(exc).__name__}})
    metrics.inc("fabulist_database_unavailable_total")
    return JSONResponse({"detail": "The database is temporarily unavailable. Please try again in a moment."},
                        status_code=503, headers={"Retry-After": "5"})


@app.exception_handler(llm.AIBusy)
async def ai_busy(_request: Request, _exc: Exception):
    metrics.inc("fabulist_ai_busy_total")
    return JSONResponse({"detail": "The AI service is busy with other requests. Try again in a moment."}, status_code=429,
                        headers={"Retry-After": "5"})


@app.exception_handler(settings.SettingsError)
async def settings_error(_request: Request, exc: settings.SettingsError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    db.one("SELECT 1 AS ok")
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    return {
        "version": config.VERSION,
        "platforms": {k: v["label"] for k, v in PLATFORMS.items()},
        "formats": [{"id": k, "ext": v[1], "label": v[2], "description": v[3]} for k, v in exporters.EXPORTERS.items()],
        "requirement_types": pipeline.REQ_TYPES,
        "priorities": pipeline.PRIORITIES,
        "points": pipeline.POINTS,
        "product_classes": [c["name"] for c in taxonomy.classes()],
        "compliance_tags": list(taxonomy.COMPLIANCE_KEYWORDS),
        "providers": [{"id": p["id"], "name": p["name"], "kind": p["kind"], "model": p["model"], "is_default": p["is_default"]}
                      for p in settings.list_providers()],
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("app/static/index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory="app/static"), name="static")
