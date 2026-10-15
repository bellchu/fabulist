import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import admin, config, db, embeddings, exporters, history, integrations, parsing, pipeline, quality, repos, settings, taxonomy, users
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
STATUSES = {"draft", "approved", "excluded"}
STORY_STATUSES = {"draft", "approved", "rejected"}
STAGES = {"requirements", "stories"}
CSRF_HEADER = "fabulist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    settings.seed()
    # A restart kills in-process runs; don't leave them "running" forever.
    db.execute("UPDATE runs SET status='failed', error='Interrupted by server restart' WHERE status='running'")
    yield
    db.close()


app = FastAPI(title="Fabulist", lifespan=lifespan)
app.include_router(admin.router)
app.include_router(repos.router)
app.include_router(integrations.router)
app.include_router(users.router)


@app.middleware("http")
async def auth_and_guards(request: Request, call_next):
    request.state.user, request.state.role = "anonymous", "admin"
    path = request.url.path
    if users.auth_enabled() and path != "/healthz":
        creds = users.parse_basic(request.headers.get("authorization", ""))
        role = await run_in_threadpool(users.authenticate, *creds) if creds else None
        if not role:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Fabulist"'})
        request.state.user, request.state.role = creds[0], role
        if path.startswith("/api/") and not users.allowed(role, request.method, path):
            return JSONResponse({"detail": "Your role (%s) does not allow this action" % role}, status_code=403)
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
            and request.headers.get("x-requested-with") != CSRF_HEADER):
        # Browsers resend Basic-auth credentials cross-site; a custom header can't be sent
        # cross-origin without a CORS preflight (which we never allow), so require it.
        return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    response = await call_next(request)
    if path.startswith("/static/"):
        # Revalidate via ETag so a deploy is picked up without a hard refresh.
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/me")
def me(request: Request) -> Dict[str, str]:
    return users.current(request)


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
        "platforms": {k: v["label"] for k, v in PLATFORMS.items()},
        "formats": [{"id": k, "ext": v[1], "label": v[2], "description": v[3]} for k, v in exporters.EXPORTERS.items()],
        "requirement_types": pipeline.REQ_TYPES,
        "priorities": pipeline.PRIORITIES,
        "points": pipeline.POINTS,
        "product_classes": [c["name"] for c in taxonomy.PRODUCT_CLASSES],
        "compliance_tags": list(taxonomy.COMPLIANCE_KEYWORDS),
        "providers": [{"id": p["id"], "name": p["name"], "kind": p["kind"], "model": p["model"], "is_default": p["is_default"]}
                      for p in settings.list_providers()],
    }


# ---- models --------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: str = "generic"
    description: str = Field(default="", max_length=2000)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    platform: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=2000)


class TextDocIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class RunIn(BaseModel):
    stage: str
    provider_id: Optional[str] = None
    scope: str = "all"  # stories stage: "approved" or "all" (non-excluded)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=6, ge=1, le=20)


class BulkIn(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    status: str


# ---- projects ------------------------------------------------------------

def _project(pid: str) -> Dict[str, Any]:
    p = db.one("SELECT * FROM projects WHERE id = %s", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    return p


PROJECT_COUNTS = """
SELECT p.*,
  (SELECT count(*) FROM documents d WHERE d.project_id = p.id) AS documents,
  (SELECT count(*) FROM requirements r WHERE r.project_id = p.id AND r.status <> 'excluded') AS requirements,
  (SELECT count(*) FROM requirements r WHERE r.project_id = p.id AND r.status = 'approved') AS requirements_approved,
  (SELECT count(*) FROM stories s WHERE s.project_id = p.id AND s.status <> 'rejected') AS stories,
  (SELECT count(*) FROM stories s WHERE s.project_id = p.id AND s.status = 'approved') AS stories_approved
FROM projects p
"""


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    if body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    pid = db.new_id()
    db.execute("INSERT INTO projects (id, name, platform, description, created) VALUES (%s,%s,%s,%s,%s)",
               (pid, body.name.strip(), body.platform, body.description.strip(), db.now()))
    return _project(pid)


@app.get("/api/projects")
def list_projects():
    return db.rows(PROJECT_COUNTS + " ORDER BY p.created DESC")


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = db.one(PROJECT_COUNTS + " WHERE p.id = %s", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    reqs = _requirements(pid)
    stories = _stories(pid, reqs)
    active = [r for r in reqs if r["status"] != "excluded"]
    live = [s for s in stories if s["status"] != "rejected"]
    covered = {k for s in live for k in s["requirement_ids"]}
    p["coverage"] = round(100 * sum(1 for r in active if r["key"] in covered) / len(active)) if active else 0
    p["requirement_quality"] = round(sum(r["quality_score"] for r in active) / len(active)) if active else 0
    p["story_quality"] = round(sum(s["quality_score"] for s in live) / len(live)) if live else 0
    p["stories_ready"] = sum(1 for s in live if s["ready"])
    p["requirements_flagged"] = sum(1 for r in active if any(i["severity"] != "info" for i in r["issues"]))
    p["requirement_types"] = {t: sum(1 for r in active if r["type"] == t) for t in pipeline.REQ_TYPES}
    p["runs"] = {st: db.one("SELECT * FROM runs WHERE project_id=%s AND stage=%s ORDER BY created DESC LIMIT 1", (pid, st))
                 for st in STAGES}
    return p


@app.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectPatch):
    p = _project(pid)
    if body.platform is not None and body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    db.execute("UPDATE projects SET name=%s, platform=%s, description=%s WHERE id=%s",
               ((body.name or p["name"]).strip(), body.platform or p["platform"],
                (p["description"] if body.description is None else body.description).strip(), pid))
    return _project(pid)


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str):
    _project(pid)
    db.execute("DELETE FROM projects WHERE id = %s", (pid,))


# ---- documents -----------------------------------------------------------

def _add_document(pid: str, filename: str, text: str) -> Dict[str, Any]:
    did = db.new_id()
    db.execute("INSERT INTO documents (id, project_id, filename, text, created) VALUES (%s,%s,%s,%s,%s)",
               (did, pid, filename, text, db.now()))
    indexed = 0
    try:
        indexed = embeddings.index_document(pid, did, text)
    except Exception:  # retrieval degrades; generation still works without it
        log.exception("indexing failed for document %s", did)
    return {"id": did, "filename": filename, "chars": len(text), "indexed_chunks": indexed}


@app.post("/api/projects/{pid}/documents", status_code=201)
async def upload_document(pid: str, file: UploadFile = File(...)):
    _project(pid)
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "document.txt")[:200]
    try:
        text = parsing.extract_text(name, data)
    except parsing.ParseError as exc:
        raise HTTPException(400, str(exc))
    return _add_document(pid, name, text)


@app.post("/api/projects/{pid}/documents/text", status_code=201)
def add_text_document(pid: str, body: TextDocIn):
    _project(pid)
    return _add_document(pid, body.name.strip(), body.text[: config.MAX_DOC_CHARS])


@app.get("/api/projects/{pid}/documents")
def list_documents(pid: str):
    _project(pid)
    return db.rows("SELECT d.id, d.filename, length(d.text) AS chars, d.created, d.source, d.path, d.repo_id, "
                   "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks "
                   "FROM documents d WHERE d.project_id = %s ORDER BY d.created", (pid,))


@app.get("/api/projects/{pid}/documents/{did}")
def get_document(pid: str, did: str):
    d = db.one("SELECT id, filename, text, created FROM documents WHERE id = %s AND project_id = %s", (did, pid))
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@app.delete("/api/projects/{pid}/documents/{did}", status_code=204)
def delete_document(pid: str, did: str):
    _project(pid)
    db.execute("DELETE FROM documents WHERE id = %s AND project_id = %s", (did, pid))


@app.post("/api/projects/{pid}/search")
def search_documents(pid: str, body: SearchIn):
    _project(pid)
    return embeddings.search(pid, body.query, body.k)


# ---- runs ----------------------------------------------------------------

class RunCancelled(Exception):
    pass


def _execute_run(run_id: str, pid: str, stage: str, provider_id: str, scope: str) -> None:
    started = time.monotonic()
    usage = {"in": 0, "out": 0}

    def progress(msg: str) -> None:
        # Called between AI requests, so it doubles as the cancellation checkpoint.
        with db.conn() as c:
            row = c.execute("UPDATE runs SET progress=%s, tokens_in=%s, tokens_out=%s WHERE id=%s RETURNING cancel_requested",
                            (msg[:200], usage["in"], usage["out"], run_id)).fetchone()
        if row and row["cancel_requested"]:
            raise RunCancelled()

    try:
        project = _project(pid)
        profile = settings.get_provider(provider_id)
        opts = settings.get_settings()
        if stage == "requirements":
            docs = db.rows("SELECT id, filename, text FROM documents WHERE project_id=%s ORDER BY created", (pid,))
            items = pipeline.extract_requirements(docs, project["platform"], profile, opts, progress, usage)
        else:
            reqs = [r for r in _requirements(pid)
                    if r["status"] == "approved" or (scope == "all" and r["status"] == "draft")]
            if not reqs:
                raise ValueError("No requirements in scope. Extract and review requirements first.")
            items = pipeline.generate_stories(reqs, pid, project["platform"], profile, opts, progress, usage)
        progress("Saving results")
        if stage == "requirements":
            sql = "INSERT INTO requirements (id, project_id, seq, data) VALUES (%s,%s,%s,%s)"
            params = [(db.new_id(), pid, i, json.dumps(x)) for i, x in enumerate(items)]
        else:
            sql = "INSERT INTO stories (id, project_id, run_id, seq, data) VALUES (%s,%s,%s,%s,%s)"
            params = [(db.new_id(), pid, run_id, i, json.dumps(x)) for i, x in enumerate(items)]
        with db.conn() as c:
            # Stories link to requirement keys, so re-extracting requirements resets the backlog too.
            c.execute("DELETE FROM stories WHERE project_id = %s", (pid,))
            if stage == "requirements":
                c.execute("DELETE FROM requirements WHERE project_id = %s", (pid,))
            with c.cursor() as cur:
                cur.executemany(sql, params)
            c.execute("UPDATE runs SET status='done', item_count=%s, story_count=%s, progress='', tokens_in=%s, "
                      "tokens_out=%s, duration_ms=%s WHERE id=%s",
                      (len(items), len(items), usage["in"], usage["out"], int((time.monotonic() - started) * 1000), run_id))
    except RunCancelled:
        db.execute("UPDATE runs SET status='cancelled', error='Cancelled; existing data was kept', progress='', "
                   "duration_ms=%s WHERE id=%s", (int((time.monotonic() - started) * 1000), run_id))
    except Exception as exc:
        log.exception("run %s failed", run_id)
        msg = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Internal error (%s)" % type(exc).__name__
        db.execute("UPDATE runs SET status='failed', error=%s, progress='', tokens_in=%s, tokens_out=%s, duration_ms=%s "
                   "WHERE id=%s", (msg[:500], usage["in"], usage["out"], int((time.monotonic() - started) * 1000), run_id))


@app.post("/api/projects/{pid}/runs", status_code=202)
def start_run(pid: str, body: RunIn, tasks: BackgroundTasks, request: Request):
    _project(pid)
    if body.stage not in STAGES:
        raise HTTPException(400, "Unknown stage")
    if body.scope not in ("approved", "all"):
        raise HTTPException(400, "Unknown scope")
    profile = settings.get_provider(body.provider_id) if body.provider_id else settings.default_provider()
    if body.stage == "requirements" and not db.one("SELECT 1 AS x FROM documents WHERE project_id=%s LIMIT 1", (pid,)):
        raise HTTPException(400, "Add at least one BRD document first")
    if db.one("SELECT 1 AS x FROM runs WHERE project_id=%s AND status='running'", (pid,)):
        raise HTTPException(409, "A run is already in progress for this project")
    rid = db.new_id()
    db.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created) "
               "VALUES (%s,%s,'running',%s,%s,%s,%s,%s)",
               (rid, pid, body.stage, profile["name"], profile["model"], history.actor(request), db.now()))
    tasks.add_task(_execute_run, rid, pid, body.stage, profile["id"], body.scope)
    return {"id": rid, "status": "running"}


@app.post("/api/projects/{pid}/runs/{rid}/cancel")
def cancel_run(pid: str, rid: str):
    with db.conn() as c:
        row = c.execute("UPDATE runs SET cancel_requested=TRUE, progress='Cancelling…' WHERE id=%s AND project_id=%s "
                        "AND status='running' RETURNING id", (rid, pid)).fetchone()
    if not row:
        raise HTTPException(409, "Run is not running")
    return {"status": "cancelling"}


@app.get("/api/projects/{pid}/runs")
def list_runs(pid: str, limit: int = 20):
    _project(pid)
    return db.rows("SELECT * FROM runs WHERE project_id=%s ORDER BY created DESC, id LIMIT %s", (pid, min(limit, 100)))


# ---- requirements --------------------------------------------------------

REQ_FIELDS = {"statement", "type", "actors", "source_id", "rationale", "epic", "section", "source_ref",
              "product_scope", "compliance_tags", "notes"}
STORY_FIELDS = {"epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority", "story_points",
                "labels", "requirement_ids", "product_scope", "variant_criteria", "compliance_tags", "open_questions", "notes"}


def _requirements(pid: str) -> List[Dict[str, Any]]:
    out = []
    for r in db.rows("SELECT * FROM requirements WHERE project_id=%s ORDER BY seq", (pid,)):
        item = db.item_row(r, "REQ")
        item["issues"] = quality.requirement_issues(item)
        item["quality_score"] = quality.score(item["issues"])
        out.append(item)
    return out


def _stories(pid: str, reqs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    reqs = _requirements(pid) if reqs is None else reqs
    valid = {r["key"] for r in reqs if r["status"] != "excluded"}
    links: Dict[str, List[Dict[str, str]]] = {}
    for link in db.rows("SELECT l.item_ref, l.external_key, l.external_url, i.kind FROM external_links l "
                        "JOIN integrations i ON i.id = l.integration_id WHERE l.project_id=%s AND l.item_type='story'", (pid,)):
        links.setdefault(link["item_ref"], []).append({"key": link["external_key"], "url": link["external_url"], "kind": link["kind"]})
    out = []
    for r in db.rows("SELECT * FROM stories WHERE project_id=%s ORDER BY seq", (pid,)):
        item = db.item_row(r, "US")
        item.setdefault("feature", "General")
        item.setdefault("requirement_ids", [])
        item.setdefault("story_points", None)
        item["issues"] = quality.story_issues(item, valid)
        item["quality_score"] = quality.score(item["issues"])
        item["ready"] = quality.is_ready(item["issues"])
        item["external"] = links.get(item["id"], [])
        out.append(item)
    return out


def _kind(table: str) -> str:
    return "requirement" if table == "requirements" else "story"


def _label(item: Dict[str, Any]) -> str:
    return str(item.get("statement") or item.get("title") or "")[:160]


def _save_item(table: str, pid: str, iid: Optional[str], body: Dict[str, Any], who: str) -> str:
    statuses = STATUSES if table == "requirements" else STORY_STATUSES
    fields = REQ_FIELDS if table == "requirements" else STORY_FIELDS
    current: Dict[str, Any] = {}
    old_status = status = "draft"
    seq = None
    if iid:
        row = db.one("SELECT * FROM %s WHERE id=%%s AND project_id=%%s" % table, (iid, pid))
        if not row:
            raise HTTPException(404, "Item not found")
        current = dict(row["data"])
        old_status = status = row["status"]
        seq = row["seq"]
    status = body.get("status", status)
    if status not in statuses:
        raise HTTPException(400, "Invalid status")
    merged = dict(current)
    merged.update({k: v for k, v in body.items() if k in fields})
    if table == "requirements":
        item = pipeline.normalize_requirement(merged, {"epic": merged.get("epic", ""), "heading": merged.get("section", "")},
                                              merged.get("source_ref", "Manual"))
        if item is None:
            raise HTTPException(400, "Statement is required (min 8 characters)")
    else:
        item = pipeline.normalize_story(merged)
        if item is None:
            raise HTTPException(400, "Title is required")
    kind = _kind(table)
    with db.conn() as c:
        if iid:
            changes = history.diff(dict(current, status=old_status), dict(item, status=status))
            c.execute("UPDATE %s SET data=%%s, status=%%s WHERE id=%%s" % table, (json.dumps(item), status, iid))
            if changes:
                action = "status changed" if set(changes) == {"status"} else "edited"
                history.record(c, pid, kind, iid, history.item_key(kind, seq), action, who, _label(item), changes)
            return iid
        new = db.new_id()
        seq = c.execute("SELECT coalesce(max(seq), -1) + 1 AS n FROM %s WHERE project_id=%%s" % table, (pid,)).fetchone()["n"]
        if table == "requirements":
            c.execute("INSERT INTO requirements (id, project_id, seq, status, data) VALUES (%s,%s,%s,%s,%s)",
                      (new, pid, seq, status, json.dumps(item)))
        else:
            c.execute("INSERT INTO stories (id, project_id, run_id, seq, status, data) VALUES (%s,%s,'manual',%s,%s,%s)",
                      (new, pid, seq, status, json.dumps(item)))
        history.record(c, pid, kind, new, history.item_key(kind, seq), "created", who, _label(item))
    return new


def _bulk(table: str, pid: str, body: BulkIn, who: str) -> Dict[str, int]:
    statuses = STATUSES if table == "requirements" else STORY_STATUSES
    if body.status not in statuses:
        raise HTTPException(400, "Invalid status")
    kind = _kind(table)
    with db.conn() as c:
        rows = c.execute("SELECT id, seq, status, data FROM %s WHERE project_id=%%s AND id = ANY(%%s) FOR UPDATE" % table,
                         (pid, body.ids)).fetchall()
        changed = [r for r in rows if r["status"] != body.status]
        c.execute("UPDATE %s SET status=%%s WHERE project_id=%%s AND id = ANY(%%s)" % table,
                  (body.status, pid, [r["id"] for r in changed]))
        for r in changed:
            history.record(c, pid, kind, r["id"], history.item_key(kind, r["seq"]), "status changed", who,
                           _label(r["data"]), {"status": {"from": r["status"], "to": body.status}})
    return {"updated": len(changed)}


def _delete_item(table: str, pid: str, iid: str, who: str) -> None:
    kind = _kind(table)
    with db.conn() as c:
        row = c.execute("DELETE FROM %s WHERE id=%%s AND project_id=%%s RETURNING seq, data" % table, (iid, pid)).fetchone()
        if row:
            history.record(c, pid, kind, iid, history.item_key(kind, row["seq"]), "deleted", who, _label(row["data"]))


@app.get("/api/projects/{pid}/requirements")
def list_requirements(pid: str):
    _project(pid)
    return _requirements(pid)


@app.post("/api/projects/{pid}/requirements", status_code=201)
def create_requirement(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _project(pid)
    rid = _save_item("requirements", pid, None, body, history.actor(request))
    return next(r for r in _requirements(pid) if r["id"] == rid)


@app.patch("/api/projects/{pid}/requirements/{rid}")
def update_requirement(pid: str, rid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _save_item("requirements", pid, rid, body, history.actor(request))
    return next(r for r in _requirements(pid) if r["id"] == rid)


@app.delete("/api/projects/{pid}/requirements/{rid}", status_code=204)
def delete_requirement(pid: str, rid: str, request: Request):
    _delete_item("requirements", pid, rid, history.actor(request))


@app.post("/api/projects/{pid}/requirements/bulk")
def bulk_requirements(pid: str, body: BulkIn, request: Request):
    return _bulk("requirements", pid, body, history.actor(request))


# ---- stories -------------------------------------------------------------

@app.get("/api/projects/{pid}/stories")
def list_stories(pid: str):
    _project(pid)
    return _stories(pid)


@app.post("/api/projects/{pid}/stories", status_code=201)
def create_story(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _project(pid)
    sid = _save_item("stories", pid, None, body, history.actor(request))
    return next(s for s in _stories(pid) if s["id"] == sid)


@app.patch("/api/projects/{pid}/stories/{sid}")
def update_story(pid: str, sid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _save_item("stories", pid, sid, body, history.actor(request))
    return next(s for s in _stories(pid) if s["id"] == sid)


class RefineIn(BaseModel):
    provider_id: Optional[str] = None


@app.post("/api/projects/{pid}/stories/{sid}/refine")
def refine_story(pid: str, sid: str, body: RefineIn):
    """Propose an improved version of one story. Nothing is saved; the client applies it via PATCH."""
    project = _project(pid)
    reqs = _requirements(pid)
    story = next((s for s in _stories(pid, reqs) if s["id"] == sid), None)
    if story is None:
        raise HTTPException(404, "Story not found")
    profile = settings.get_provider(body.provider_id) if body.provider_id else settings.default_provider()
    linked = [r for r in reqs if r["key"] in story["requirement_ids"]]
    usage = {"in": 0, "out": 0}
    try:
        proposal = pipeline.refine_story(story, linked, story["issues"], project["platform"], profile, settings.get_settings(), usage)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, "Refinement failed: %s" % exc)
    valid = {r["key"] for r in reqs if r["status"] != "excluded"}
    issues = quality.story_issues(proposal, valid)
    return {"story": proposal, "issues": issues, "quality_score": quality.score(issues), "ready": quality.is_ready(issues),
            "before_score": story["quality_score"], "provider": profile["name"], "usage": usage}


@app.delete("/api/projects/{pid}/stories/{sid}", status_code=204)
def delete_story(pid: str, sid: str, request: Request):
    _delete_item("stories", pid, sid, history.actor(request))


@app.post("/api/projects/{pid}/stories/bulk")
def bulk_stories(pid: str, body: BulkIn, request: Request):
    return _bulk("stories", pid, body, history.actor(request))


@app.get("/api/projects/{pid}/history")
def project_history(pid: str, item_id: str = "", limit: int = 100):
    _project(pid)
    return history.entries(pid, item_id, max(1, min(limit, 500)))


# ---- traceability & export -----------------------------------------------

@app.get("/api/projects/{pid}/traceability")
def traceability(pid: str):
    _project(pid)
    reqs = [r for r in _requirements(pid) if r["status"] != "excluded"]
    stories = [s for s in _stories(pid, reqs) if s["status"] != "rejected"]
    by_req: Dict[str, List[Dict[str, Any]]] = {}
    for s in stories:
        for k in s["requirement_ids"]:
            by_req.setdefault(k, []).append({"key": s["key"], "id": s["id"], "title": s["title"], "status": s["status"]})
    rows = [{"key": r["key"], "id": r["id"], "type": r["type"], "statement": r["statement"], "section": r["section"],
             "status": r["status"], "stories": by_req.get(r["key"], [])} for r in reqs]
    orphans = [{"key": s["key"], "id": s["id"], "title": s["title"]} for s in stories if not s["requirement_ids"]]
    covered = sum(1 for r in rows if r["stories"])
    return {"rows": rows, "orphans": orphans, "covered": covered, "total": len(rows),
            "coverage": round(100 * covered / len(rows)) if rows else 0}


class PushIn(BaseModel):
    integration_id: str
    target: str = Field(default="", max_length=120)
    scope: str = "approved"


@app.post("/api/projects/{pid}/push")
def push_to_tracker(pid: str, body: PushIn, request: Request):
    _project(pid)
    conn = integrations.get(body.integration_id)
    target = (body.target or conn["target"]).strip()
    if not target:
        raise HTTPException(400, "Choose the target project (Jira project key or Azure DevOps project)")
    stories = [s for s in _stories(pid) if s["status"] == "approved" or (body.scope == "all" and s["status"] == "draft")]
    if not stories:
        raise HTTPException(400, "No stories in scope. Approve stories first or push everything in review.")
    if len(stories) > 500:
        raise HTTPException(400, "Push at most 500 stories at a time")
    try:
        result = integrations.push(pid, conn, target, stories)
    except (integrations.TrackerError, settings.SettingsError) as exc:
        raise HTTPException(400, str(exc))
    with db.conn() as c:
        history.record(c, pid, "push", conn["id"], target, "pushed to " + conn["kind"], history.actor(request),
                       "%s %s: %d created, %d updated, %d failed" % (conn["name"], target, result["created"], result["updated"], len(result["failed"])))
    return result


@app.get("/api/projects/{pid}/export")
def export(pid: str, format: str = "xlsx", scope: str = "approved-or-all"):
    project = _project(pid)
    entry = exporters.EXPORTERS.get(format)
    if not entry:
        raise HTTPException(400, "Unknown format")
    reqs = [r for r in _requirements(pid) if r["status"] != "excluded"]
    stories = [s for s in _stories(pid, reqs) if s["status"] != "rejected"]
    if scope == "approved":
        stories = [s for s in stories if s["status"] == "approved"]
        reqs = [r for r in reqs if r["status"] == "approved"]
    if not stories and not reqs:
        raise HTTPException(404, "Nothing to export yet")
    for item in stories + reqs:
        item.pop("issues", None)
    payload, mime = entry[0](stories, reqs)
    slug = re.sub(r"[^a-z0-9]+", "-", project["name"].lower()).strip("-") or "backlog"
    return Response(payload, media_type=mime,
                    headers={"Content-Disposition": 'attachment; filename="%s-%s.%s"' % (slug, format, entry[1])})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("app/static/index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory="app/static"), name="static")
