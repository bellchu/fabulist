import asyncio
import json
import logging
import re
import secrets
from urllib.parse import quote
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import psycopg
import psycopg_pool
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from . import admin, config, db, exporters, integrations, llm, metrics, netaddr, pipeline, portability, ratelimit, repos, settings, sso, taxonomy, users, workspace
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
CSRF_HEADER = "fabulist"
SECURITY_HEADERS = {
    # All scripts and styles are served from /static; inline style properties are set via CSSOM only.
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",    # a window opened from another site gets no handle on this one
    "Cross-Origin-Resource-Policy": "same-origin",  # other sites cannot embed its data or files
}


def recover_runs_after_restart() -> None:
    """A restart kills the runs this server was executing, which cannot be resumed (they would start over and spend the AI cost again), and
    empties its in-memory queue, which can: runs that were only waiting for a slot are started again, in order."""
    workspace.heartbeat()
    # Only runs nobody owns (rows from before owners were recorded): those of another live server are left alone, and those of one that stopped
    # are found by the sweeper within about a minute.
    db.execute("UPDATE runs SET status='failed', error='Interrupted by server restart' WHERE status='running' AND owner IS NULL")
    workspace.resume_waiting_runs()


SWEEP_SECONDS = 60


async def beat_forever() -> None:
    """Tell the other instances this one is alive, every few seconds (see run ownership in the architecture notes)."""
    while True:
        await asyncio.sleep(10)
        try:
            await run_in_threadpool(workspace.heartbeat)
        except Exception:  # noqa: BLE001 - a database blip must not end the heartbeat
            log.warning("heartbeat failed", exc_info=True)


async def sweep_stuck_runs() -> None:
    """A hung AI call is otherwise only noticed when someone starts another run in that project: look every minute, so the run is failed,
    its slot is freed and the people who asked to be told are told."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await run_in_threadpool(workspace.recover_all_stuck_runs)
        except Exception:  # noqa: BLE001 - a database blip must not end the sweeper
            log.warning("stuck-run sweep failed", exc_info=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    settings.seed()
    taxonomy.seed()
    recover_runs_after_restart()
    sweeper, beat = asyncio.create_task(sweep_stuck_runs()), asyncio.create_task(beat_forever())
    yield
    sweeper.cancel()
    beat.cancel()
    workspace.shutdown_runs()
    db.close()


# Read-only POSTs and the archive toggles stay available on an archived project.
_ARCHIVE_EXEMPT = ("/search", "/ask", "/archive", "/restore")


def project_guards(request: Request) -> None:
    """One guard on every route that names a project (so a route added later cannot forget it): who may see it, then whether it may change."""
    pid = request.path_params.get("pid")
    if not pid:
        return
    row = db.one("SELECT archived, visibility FROM projects WHERE id=%s", (pid,))
    if not row:
        return  # the route answers 404 itself
    if row["visibility"] == "members" and getattr(request.state, "role", "admin") != "admin":
        user = getattr(request.state, "user", "")
        if not db.one("SELECT 1 AS x FROM project_members WHERE project_id=%s AND username=%s", (pid, user)):
            raise HTTPException(404, "Project not found")  # not 403: a project one may not see does not announce that it exists
    if request.method in ("GET", "HEAD", "OPTIONS") or request.url.path.endswith(_ARCHIVE_EXEMPT):
        return
    if request.method == "DELETE" and request.url.path == "/api/projects/" + pid:
        return  # permanent deletion has its own rules
    if row["archived"]:
        raise HTTPException(409, "This project is archived and read-only. Restore it to make changes.")


API_INTRO = """The Fabulist API turns BRDs into reviewed requirements and a traceable backlog. Everything the web app does goes through it.

**Authentication.** Send a personal API token as `Authorization: Bearer fab_...` (create one on the Account page; it acts with your role and can be revoked), or HTTP Basic credentials, or a single-sign-on session. Tokens cannot be sent by a browser on its own, so they need no extra header. Browser sessions and Basic credentials must add `X-Requested-With: fabulist` to every request that changes data (POST, PUT, PATCH, DELETE).

**Errors** are JSON: `{"detail": "what went wrong"}`. 401 means sign in, 403 means your role does not allow it, 404 includes projects you may not see, 409 means the project is archived or the item changed since you read it (send the item's `rev` back when you save), 429 and 503 carry `Retry-After`.

**Long work** (extracting requirements, writing stories) is a *run*: start it with `POST /api/projects/{pid}/runs`, which answers 202 immediately, then poll `GET /api/projects/{pid}/runs`. A run waits in a queue when every slot is busy. `GET /api/projects/{pid}/runs/estimate` says what a run would ask of the AI provider first.

Example: `curl -H "Authorization: Bearer $TOKEN" https://your-host/api/projects`"""

_TAGS = (("Requirements", "/requirements"), ("Stories", "/stories"), ("Documents", ("/documents", "/search", "/ask", "/repos")), ("Runs", "/runs"),
         ("Baselines", "/baselines"), ("Comments", "/comments"), ("Audit trail", "/history"), ("Export and push", ("/export", "/push")))
TAG_DESCRIPTIONS = {
    "Projects": "Create, open, archive, import and share projects.", "Documents": "BRD sources: uploads, pasted text, Git repositories, passage search, questions.",
    "Requirements": "The reviewed requirement set, with quality checks.", "Stories": "User stories linked to requirements, with traceability.",
    "Runs": "AI extraction and story generation: queue, estimate, progress.", "Baselines": "Versioned sign-off snapshots and comparison.",
    "Comments": "Discussion on requirements and stories.", "Audit trail": "Who changed what and when.", "Export and push": "Reports, spreadsheets, tracker imports and pushes.",
    "Integrations": "Issue trackers and webhooks (configured by administrators).", "Administration": "AI providers, settings, users, taxonomy, usage (administrators).",
    "Account": "Your identity and personal API tokens.", "General": "Application metadata and people picker."}


def _tag_for(path: str) -> str:
    if path.startswith("/api/admin/"):
        return "Administration"
    if path.startswith(("/api/me", "/api/auth")):
        return "Account"
    if path.startswith("/api/integrations"):
        return "Integrations"
    if path.startswith("/api/projects/{pid}/"):
        rest = path[len("/api/projects/{pid}"):]
        for tag, needle in _TAGS:
            if any(rest.startswith(n) for n in ((needle,) if isinstance(needle, str) else needle)):
                return tag
        return "Projects"  # the project itself: archive, restore, access, archive.json
    if path.startswith("/api/projects"):
        return "Projects"
    return "General"


def custom_openapi() -> Dict[str, Any]:
    """The generated document, with every operation in a group (the URL structure decides which) and an introduction that says how to authenticate."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    spec = get_openapi(title="Fabulist API", version=config.VERSION, description=API_INTRO, routes=app.routes)
    used = set()
    for path, item in spec["paths"].items():
        for op in item.values():
            op["tags"] = [_tag_for(path)]
            used.add(op["tags"][0])
            summary = op.get("summary", "")
            op["summary"] = summary[:1] + summary[1:].lower()  # "Start Run" (from the function name) reads better as a sentence
    spec["tags"] = [{"name": t, "description": TAG_DESCRIPTIONS.get(t, "")} for t in TAG_DESCRIPTIONS if t in used]
    app.openapi_schema = spec
    return spec


REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")  # what a caller may send as X-Request-ID
PROBES = ("/healthz", "/livez")  # open to the platform: no sign-in, not counted in request metrics

app = FastAPI(title="Fabulist", lifespan=lifespan, dependencies=[Depends(project_guards)], docs_url=None, redoc_url=None)
app.openapi = custom_openapi  # type: ignore[assignment]
# An ingress in front may not compress; a 10,000-requirement list is megabytes of repetitive JSON.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(admin.router)
app.include_router(repos.router)
app.include_router(integrations.router)
app.include_router(users.router)
app.include_router(users.me_router)
app.include_router(users.admin_tokens_router)
app.include_router(users.account_router)
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
    if config.ORIGIN_SECRET and path not in (*PROBES, "/metrics") and not secrets.compare_digest(request.headers.get("x-origin-secret", "").encode(), config.ORIGIN_SECRET.encode()):
        metrics.inc("fabulist_origin_refused_total")
        return JSONResponse({"detail": "Forbidden"}, status_code=403)  # no hint of why: the proxy knows
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
    elif sso.enabled() and not path.startswith(("/auth/", "/static/")) and path not in PROBES and "authorization" not in request.headers:
        # Browsers without a session go to the identity provider; API clients get a plain 401.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return RedirectResponse("/auth/login?next=" + quote(path), status_code=302)
    elif ((users.auth_enabled() or sso.enabled()) and path not in PROBES and not path.startswith("/auth/")
          and not (sso.enabled() and path.startswith("/static/"))):
        token = users.bearer_token(request.headers.get("authorization", ""))
        creds = None if token else users.parse_basic(request.headers.get("authorization", ""))
        # Throttle per username and per client address (Cloudflare/Traefik forward the real IP).
        client_ip = netaddr.client_ip(request)
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
    if path.startswith("/api/") and path not in PROBES:
        who = getattr(request.state, "user", "anonymous")
        wait = ratelimit.take(who if who != "anonymous" else "ip:" + netaddr.client_ip(request))
        if wait:
            metrics.inc("fabulist_rate_limited_total")
            return JSONResponse({"detail": "Too many requests: slow down and try again shortly"}, status_code=429, headers={"Retry-After": str(max(1, int(wait + 0.999)))})
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
    incoming = request.headers.get("x-request-id", "")
    request.state.request_id = incoming if REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex[:12]  # a caller's own trace id is kept; anything odd is replaced
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", None) or ("/static" if request.url.path.startswith("/static/") else "other")
        elapsed = time.monotonic() - started
        if request.url.path not in (*PROBES, "/metrics"):
            metrics.inc("fabulist_http_requests_total", method=request.method, route=route, status=str(status))
            metrics.observe_request(route, elapsed)
            access_log.info("request", extra={"fields": {"method": request.method, "route": route, "path": request.url.path,
                                                         "status": status, "ms": int(elapsed * 1000),
                                                         "user": getattr(request.state, "user", ""), "request_id": request.state.request_id}})


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/api/me")
def me(request: Request) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(users.current(request))
    out["sso"] = bool(sso.enabled() and request.cookies.get(sso.SESSION_COOKIE))
    out["named"] = bool(db.one("SELECT 1 AS x FROM users WHERE username=%s", (out["username"],)))  # has an account row: can own API tokens
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
async def ai_busy(_request: Request, exc: Exception):
    metrics.inc("fabulist_ai_busy_total")
    reason = str(exc).rstrip(".") or "The AI service is busy with other requests"  # the pool is full, or this person has used their share of it
    return JSONResponse({"detail": reason + ". Try again in a moment."}, status_code=429, headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Anything not handled above: no internals in the answer, but an id the person can quote and the log can be searched for."""
    rid = getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12]
    log.error("unhandled error", exc_info=exc, extra={"fields": {"request_id": rid, "method": request.method, "path": request.url.path}})
    return JSONResponse({"detail": "Something went wrong on our side. If it keeps happening, tell " + (config.SUPPORT_CONTACT or "your administrator") + " this reference: " + rid, "request_id": rid},
                        status_code=500, headers={"X-Request-ID": rid})


@app.exception_handler(settings.SettingsError)
async def settings_error(_request: Request, exc: settings.SettingsError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    """Readiness: can this instance serve (the database answers)? Failing it only takes the pod out of rotation."""
    db.one("SELECT 1 AS ok")
    return {"status": "ok"}


@app.get("/livez")
def livez() -> Dict[str, str]:
    """Liveness: is the process itself running? No database here, so a database outage never restarts the app (and kills its runs)."""
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    return {
        "version": config.VERSION,
        "support_contact": config.SUPPORT_CONTACT,
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


@app.get("/docs", include_in_schema=False)
def api_reference() -> FileResponse:
    """The API reference, rendered by the app itself: FastAPI's stock page loads its scripts from a public CDN (blocked by our policy)."""
    return FileResponse("app/static/docs.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory="app/static"), name="static")
