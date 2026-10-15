import base64
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, embeddings, exporters, parsing, pipeline, taxonomy
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
STORY_STATUSES = {"draft", "approved", "rejected"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Fabulist", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if config.BASIC_USER and config.BASIC_PASSWORD and request.url.path != "/healthz":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(user, config.BASIC_USER) and secrets.compare_digest(pw, config.BASIC_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Fabulist"'})
    return await call_next(request)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    db.one("SELECT 1 AS ok")
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return {
        "providers": config.ALLOWED_PROVIDERS,
        "default_provider": config.LLM_PROVIDER,
        "platforms": {k: v["label"] for k, v in PLATFORMS.items()},
        "formats": list(exporters.EXPORTERS),
        "product_classes": taxonomy.PRODUCT_CLASSES,
    }


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: str = "generic"


class TextDocIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class RunIn(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)


def _project(pid: str) -> Dict[str, Any]:
    p = db.one("SELECT * FROM projects WHERE id = %s", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    if body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    pid = db.new_id()
    db.execute("INSERT INTO projects (id, name, platform, created) VALUES (%s,%s,%s,%s)",
               (pid, body.name.strip(), body.platform, db.now()))
    return _project(pid)


@app.get("/api/projects")
def list_projects():
    return db.rows("SELECT * FROM projects ORDER BY created DESC")


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str):
    _project(pid)
    db.execute("DELETE FROM projects WHERE id = %s", (pid,))


def _add_document(pid: str, filename: str, text: str) -> Dict[str, Any]:
    did = db.new_id()
    db.execute("INSERT INTO documents (id, project_id, filename, text, created) VALUES (%s,%s,%s,%s,%s)",
               (did, pid, filename, text, db.now()))
    indexed = 0
    try:
        indexed = embeddings.index_document(pid, did, text)
    except Exception:  # RAG degrades; generation still works without retrieval context
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
    return db.rows("SELECT id, filename, length(text) AS chars, created FROM documents "
                   "WHERE project_id = %s ORDER BY created", (pid,))


@app.delete("/api/projects/{pid}/documents/{did}", status_code=204)
def delete_document(pid: str, did: str):
    _project(pid)
    db.execute("DELETE FROM documents WHERE id = %s AND project_id = %s", (did, pid))


@app.post("/api/projects/{pid}/search")
def search_documents(pid: str, body: SearchIn):
    _project(pid)
    return embeddings.search(pid, body.query, body.k)


def _run_generation(run_id: str, pid: str, platform: str, provider: str, model: str) -> None:
    def progress(msg: str) -> None:
        db.execute("UPDATE runs SET progress = %s WHERE id = %s", (msg, run_id))

    try:
        docs = db.rows("SELECT id, filename, text FROM documents WHERE project_id = %s ORDER BY created", (pid,))
        stories = pipeline.generate(docs, pid, platform, provider, model, progress)
        with db.conn() as c:
            c.execute("DELETE FROM stories WHERE project_id = %s", (pid,))
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO stories (id, project_id, run_id, seq, data) VALUES (%s,%s,%s,%s,%s)",
                    [(db.new_id(), pid, run_id, i, json.dumps(s)) for i, s in enumerate(stories)])
            c.execute("UPDATE runs SET status='done', story_count=%s, progress='' WHERE id=%s",
                      (len(stories), run_id))
    except Exception as exc:
        log.exception("run %s failed", run_id)
        msg = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        db.execute("UPDATE runs SET status='failed', error=%s, progress='' WHERE id=%s", (msg[:500], run_id))


@app.post("/api/projects/{pid}/runs", status_code=202)
def start_run(pid: str, tasks: BackgroundTasks, body: RunIn = Body(default_factory=RunIn)):
    project = _project(pid)
    provider = (body.provider or config.LLM_PROVIDER).lower()
    if provider not in config.ALLOWED_PROVIDERS:
        raise HTTPException(400, "Provider not allowed")
    if not db.one("SELECT 1 AS x FROM documents WHERE project_id = %s LIMIT 1", (pid,)):
        raise HTTPException(400, "Add at least one BRD document first")
    if db.one("SELECT 1 AS x FROM runs WHERE project_id = %s AND status = 'running'", (pid,)):
        raise HTTPException(409, "A run is already in progress")
    model = (body.model or config.LLM_MODEL or "").strip()[:100]
    rid = db.new_id()
    db.execute("INSERT INTO runs (id, project_id, status, provider, model, created) VALUES (%s,%s,'running',%s,%s,%s)",
               (rid, pid, provider, model, db.now()))
    tasks.add_task(_run_generation, rid, pid, project["platform"], provider, model)
    return {"id": rid, "status": "running"}


@app.get("/api/projects/{pid}/runs/latest")
def latest_run(pid: str):
    _project(pid)
    return db.one("SELECT * FROM runs WHERE project_id = %s ORDER BY created DESC, id LIMIT 1", (pid,)) or {}


def _stories(pid: str, include_rejected: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM stories WHERE project_id = %s" + ("" if include_rejected else " AND status <> 'rejected'")
    return [db.story_row(r) for r in db.rows(sql + " ORDER BY seq", (pid,))]


@app.get("/api/projects/{pid}/stories")
def list_stories(pid: str):
    _project(pid)
    return _stories(pid)


@app.patch("/api/projects/{pid}/stories/{sid}")
def update_story(pid: str, sid: str, body: Dict[str, Any] = Body(...)):
    row = db.one("SELECT * FROM stories WHERE id = %s AND project_id = %s", (sid, pid))
    if not row:
        raise HTTPException(404, "Story not found")
    current = db.story_row(row)
    status = body.get("status", current["status"])
    if status not in STORY_STATUSES:
        raise HTTPException(400, "Invalid status")
    merged = {k: v for k, v in current.items() if k not in ("id", "project_id", "status", "seq")}
    merged.update({k: v for k, v in body.items() if k in merged})
    story = pipeline.normalize_story(merged)
    if not story:
        raise HTTPException(400, "Title is required")
    db.execute("UPDATE stories SET data = %s, status = %s WHERE id = %s", (json.dumps(story), status, sid))
    story.update({"id": sid, "project_id": pid, "status": status, "seq": current["seq"]})
    return story


@app.get("/api/projects/{pid}/export")
def export(pid: str, format: str = "csv", include_rejected: bool = False):
    project = _project(pid)
    entry = exporters.EXPORTERS.get(format)
    if not entry:
        raise HTTPException(400, "Unknown format")
    fn, ext = entry
    stories = _stories(pid, include_rejected)
    if not stories:
        raise HTTPException(404, "No stories to export")
    payload, mime = fn(stories)
    slug = re.sub(r"[^a-z0-9]+", "-", project["name"].lower()).strip("-") or "stories"
    return Response(payload, media_type=mime,
                    headers={"Content-Disposition": 'attachment; filename="%s-%s.%s"' % (slug, format, ext)})


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
