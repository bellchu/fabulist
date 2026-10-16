"""Project workspace API: projects, sources, AI runs, requirements, stories, traceability, export, push."""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import config, db, embeddings, exporters, history, integrations, metrics, parsing, pipeline, quality, settings
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
router = APIRouter()
STATUSES = {"draft", "approved", "excluded"}
STORY_STATUSES = {"draft", "approved", "rejected"}
STAGES = {"requirements", "stories"}


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


class RefineIn(BaseModel):
    provider_id: Optional[str] = None


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


@router.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    if body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    pid = db.new_id()
    db.execute("INSERT INTO projects (id, name, platform, description, created) VALUES (%s,%s,%s,%s,%s)",
               (pid, body.name.strip(), body.platform, body.description.strip(), db.now()))
    return _project(pid)


@router.get("/api/projects")
def list_projects():
    return db.rows(PROJECT_COUNTS + " ORDER BY p.created DESC")


@router.get("/api/projects/{pid}")
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


@router.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectPatch):
    p = _project(pid)
    if body.platform is not None and body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    db.execute("UPDATE projects SET name=%s, platform=%s, description=%s WHERE id=%s",
               ((body.name or p["name"]).strip(), body.platform or p["platform"],
                (p["description"] if body.description is None else body.description).strip(), pid))
    return _project(pid)


@router.delete("/api/projects/{pid}", status_code=204)
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


@router.post("/api/projects/{pid}/documents", status_code=201)
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


@router.post("/api/projects/{pid}/documents/text", status_code=201)
def add_text_document(pid: str, body: TextDocIn):
    _project(pid)
    return _add_document(pid, body.name.strip(), body.text[: config.MAX_DOC_CHARS])


@router.get("/api/projects/{pid}/documents")
def list_documents(pid: str):
    _project(pid)
    return db.rows("SELECT d.id, d.filename, length(d.text) AS chars, d.created, d.source, d.path, d.repo_id, "
                   "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks "
                   "FROM documents d WHERE d.project_id = %s ORDER BY d.created", (pid,))


@router.get("/api/projects/{pid}/documents/{did}")
def get_document(pid: str, did: str):
    d = db.one("SELECT id, filename, text, created FROM documents WHERE id = %s AND project_id = %s", (did, pid))
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.delete("/api/projects/{pid}/documents/{did}", status_code=204)
def delete_document(pid: str, did: str):
    _project(pid)
    db.execute("DELETE FROM documents WHERE id = %s AND project_id = %s", (did, pid))


@router.post("/api/projects/{pid}/search")
def search_documents(pid: str, body: SearchIn):
    _project(pid)
    return embeddings.search(pid, body.query, body.k)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    provider_id: Optional[str] = None


@router.post("/api/projects/{pid}/ask")
def ask_brds(pid: str, body: AskIn):
    """Answer a question from the project's BRDs using retrieved passages, with citations."""
    _project(pid)
    passages = embeddings.search(pid, body.question, 6)
    profile = settings.get_provider(body.provider_id) if body.provider_id else settings.default_provider()
    usage = {"in": 0, "out": 0}
    try:
        answer = pipeline.answer_question(body.question, passages, profile, settings.get_settings(), usage)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, "Could not answer: %s" % exc)
    finally:
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")
    return {"answer": answer, "provider": profile["name"],
            "citations": [{"n": n, "filename": p["filename"], "text": p["text"][:600], "score": round(float(p["score"]), 3)}
                          for n, p in enumerate(passages, 1)]}


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
        _run_stage(run_id, pid, stage, provider_id, scope, started, usage, progress)
    finally:
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")


def _run_stage(run_id: str, pid: str, stage: str, provider_id: str, scope: str, started: float,
               usage: Dict[str, int], progress: Any) -> None:
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
        metrics.inc("fabulist_runs_total", stage=stage, status="done")
    except RunCancelled:
        metrics.inc("fabulist_runs_total", stage=stage, status="cancelled")
        db.execute("UPDATE runs SET status='cancelled', error='Cancelled; existing data was kept', progress='', "
                   "duration_ms=%s WHERE id=%s", (int((time.monotonic() - started) * 1000), run_id))
    except Exception as exc:
        metrics.inc("fabulist_runs_total", stage=stage, status="failed")
        log.exception("run %s failed", run_id)
        msg = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Internal error (%s)" % type(exc).__name__
        db.execute("UPDATE runs SET status='failed', error=%s, progress='', tokens_in=%s, tokens_out=%s, duration_ms=%s "
                   "WHERE id=%s", (msg[:500], usage["in"], usage["out"], int((time.monotonic() - started) * 1000), run_id))


@router.post("/api/projects/{pid}/runs", status_code=202)
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


@router.post("/api/projects/{pid}/runs/{rid}/cancel")
def cancel_run(pid: str, rid: str):
    with db.conn() as c:
        row = c.execute("UPDATE runs SET cancel_requested=TRUE, progress='Cancelling…' WHERE id=%s AND project_id=%s "
                        "AND status='running' RETURNING id", (rid, pid)).fetchone()
    if not row:
        raise HTTPException(409, "Run is not running")
    return {"status": "cancelling"}


@router.get("/api/projects/{pid}/runs")
def list_runs(pid: str, limit: int = 20):
    _project(pid)
    return db.rows("SELECT * FROM runs WHERE project_id=%s ORDER BY created DESC, id LIMIT %s", (pid, min(limit, 100)))


# ---- requirements --------------------------------------------------------

REQ_FIELDS = {"statement", "type", "actors", "source_id", "rationale", "epic", "section", "source_ref",
              "product_scope", "compliance_tags", "notes"}
STORY_FIELDS = {"epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority", "story_points",
                "labels", "requirement_ids", "product_scope", "variant_criteria", "compliance_tags", "open_questions", "notes"}


def _requirements(pid: str) -> List[Dict[str, Any]]:
    out = [db.item_row(r, "REQ") for r in db.rows("SELECT * FROM requirements WHERE project_id=%s ORDER BY seq", (pid,))]
    dupes = quality.duplicate_pairs([r for r in out if r["status"] != "excluded"])
    for item in out:
        item["issues"] = quality.requirement_issues(item)
        if item["key"] in dupes:
            item["issues"].append({"code": "duplicate", "severity": "warning",
                                   "message": "Possible duplicate of %s; merge or exclude one." % ", ".join(dupes[item["key"]])})
        item["quality_score"] = quality.score(item["issues"])
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


@router.get("/api/projects/{pid}/requirements")
def list_requirements(pid: str):
    _project(pid)
    return _requirements(pid)


@router.post("/api/projects/{pid}/requirements", status_code=201)
def create_requirement(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _project(pid)
    rid = _save_item("requirements", pid, None, body, history.actor(request))
    return next(r for r in _requirements(pid) if r["id"] == rid)


@router.patch("/api/projects/{pid}/requirements/{rid}")
def update_requirement(pid: str, rid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _save_item("requirements", pid, rid, body, history.actor(request))
    return next(r for r in _requirements(pid) if r["id"] == rid)


@router.post("/api/projects/{pid}/requirements/{rid}/refine")
def refine_requirement(pid: str, rid: str, body: RefineIn):
    """Propose a rewrite (possibly split into several requirements). Nothing is saved."""
    project = _project(pid)
    req = next((r for r in _requirements(pid) if r["id"] == rid), None)
    if req is None:
        raise HTTPException(404, "Requirement not found")
    profile = settings.get_provider(body.provider_id) if body.provider_id else settings.default_provider()
    usage = {"in": 0, "out": 0}
    try:
        proposals = pipeline.refine_requirement(req, req["issues"], project["platform"], profile, settings.get_settings(), usage)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, "Refinement failed: %s" % exc)
    finally:
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")
    out = []
    for p in proposals:
        issues = quality.requirement_issues(p)
        out.append({"requirement": p, "issues": issues, "quality_score": quality.score(issues)})
    return {"proposals": out, "before_score": req["quality_score"], "provider": profile["name"], "usage": usage}


@router.delete("/api/projects/{pid}/requirements/{rid}", status_code=204)
def delete_requirement(pid: str, rid: str, request: Request):
    _delete_item("requirements", pid, rid, history.actor(request))


@router.post("/api/projects/{pid}/requirements/bulk")
def bulk_requirements(pid: str, body: BulkIn, request: Request):
    return _bulk("requirements", pid, body, history.actor(request))


# ---- stories -------------------------------------------------------------

@router.get("/api/projects/{pid}/stories")
def list_stories(pid: str):
    _project(pid)
    return _stories(pid)


@router.post("/api/projects/{pid}/stories", status_code=201)
def create_story(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _project(pid)
    sid = _save_item("stories", pid, None, body, history.actor(request))
    return next(s for s in _stories(pid) if s["id"] == sid)


@router.patch("/api/projects/{pid}/stories/{sid}")
def update_story(pid: str, sid: str, request: Request, body: Dict[str, Any] = Body(...)):
    _save_item("stories", pid, sid, body, history.actor(request))
    return next(s for s in _stories(pid) if s["id"] == sid)


@router.post("/api/projects/{pid}/stories/{sid}/refine")
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
    finally:
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")
    valid = {r["key"] for r in reqs if r["status"] != "excluded"}
    issues = quality.story_issues(proposal, valid)
    return {"story": proposal, "issues": issues, "quality_score": quality.score(issues), "ready": quality.is_ready(issues),
            "before_score": story["quality_score"], "provider": profile["name"], "usage": usage}


class EstimateIn(BaseModel):
    scope: str = "unestimated"


@router.post("/api/projects/{pid}/stories/estimate")
def estimate_stories(pid: str, body: EstimateIn):
    """Propose story points; nothing is saved until the client applies them."""
    _project(pid)
    stories = [s for s in _stories(pid) if s["status"] != "rejected" and (body.scope == "all" or not s.get("story_points"))]
    if not stories:
        return {"proposals": []}
    profile = settings.default_provider()
    usage = {"in": 0, "out": 0}
    try:
        proposals = pipeline.estimate_stories(stories, profile, settings.get_settings(), usage)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, "Estimation failed: %s" % exc)
    finally:
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")
    by_key = {s["key"]: s for s in stories}
    return {"proposals": [dict(p, id=by_key[p["key"]]["id"], title=by_key[p["key"]]["title"], current=by_key[p["key"]].get("story_points"))
                          for p in proposals]}


class PointsIn(BaseModel):
    points: Dict[str, int]


@router.post("/api/projects/{pid}/stories/points")
def apply_points(pid: str, body: PointsIn, request: Request):
    """Set story points for several stories at once (audited)."""
    _project(pid)
    if any(p not in pipeline.POINTS for p in body.points.values()):
        raise HTTPException(400, "Points must be one of %s" % pipeline.POINTS)
    who = history.actor(request)
    updated = 0
    with db.conn() as c:
        rows = c.execute("SELECT id, seq, data FROM stories WHERE project_id=%s AND id = ANY(%s) FOR UPDATE",
                         (pid, list(body.points))).fetchall()
        for r in rows:
            data = dict(r["data"])
            new = body.points[r["id"]]
            if data.get("story_points") == new:
                continue
            old = data.get("story_points")
            data["story_points"] = new
            c.execute("UPDATE stories SET data=%s WHERE id=%s", (json.dumps(data), r["id"]))
            history.record(c, pid, "story", r["id"], history.item_key("story", r["seq"]), "estimated", who, data.get("title", ""),
                           {"story_points": {"from": old, "to": new}})
            updated += 1
    return {"updated": updated}


@router.delete("/api/projects/{pid}/stories/{sid}", status_code=204)
def delete_story(pid: str, sid: str, request: Request):
    _delete_item("stories", pid, sid, history.actor(request))


@router.post("/api/projects/{pid}/stories/bulk")
def bulk_stories(pid: str, body: BulkIn, request: Request):
    return _bulk("stories", pid, body, history.actor(request))


@router.get("/api/projects/{pid}/history")
def project_history(pid: str, item_id: str = "", limit: int = 100):
    _project(pid)
    return history.entries(pid, item_id, max(1, min(limit, 500)))


# ---- traceability & export -----------------------------------------------

@router.get("/api/projects/{pid}/traceability")
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


@router.post("/api/projects/{pid}/push")
def push_to_tracker(pid: str, body: PushIn, request: Request):
    _project(pid)
    conn = integrations.get(body.integration_id)
    target = (body.target or conn["target"]).strip() or ("default" if conn["kind"] == "webhook" else "")
    if not target:
        raise HTTPException(400, "Choose the target project (Jira project key or Azure DevOps project)")
    stories = [s for s in _stories(pid) if s["status"] == "approved" or (body.scope == "all" and s["status"] == "draft")]
    if not stories:
        raise HTTPException(400, "No stories in scope. Approve stories first or push everything in review.")
    if len(stories) > 500:
        raise HTTPException(400, "Push at most 500 stories at a time")
    try:
        if conn["kind"] == "confluence":
            project = _project(pid)
            reqs = [r for r in _requirements(pid) if r["status"] == "approved" or (body.scope == "all" and r["status"] == "draft")]
            result = integrations.publish_page(pid, conn, target, project["name"] + " backlog",
                                               exporters.confluence_storage(stories, reqs, project["name"]))
        else:
            result = integrations.push(pid, conn, target, stories)
    except (integrations.TrackerError, settings.SettingsError) as exc:
        raise HTTPException(400, str(exc))
    for outcome in ("created", "updated"):
        metrics.inc("fabulist_tracker_items_total", result[outcome], tracker=conn["kind"], result=outcome)
    metrics.inc("fabulist_tracker_items_total", len(result["failed"]), tracker=conn["kind"], result="failed")
    with db.conn() as c:
        history.record(c, pid, "push", conn["id"], target, "pushed to " + conn["kind"], history.actor(request),
                       "%s %s: %d created, %d updated, %d failed" % (conn["name"], target, result["created"], result["updated"], len(result["failed"])))
    return result


@router.get("/api/projects/{pid}/export")
def export(pid: str, format: str = "xlsx", scope: str = "approved-or-all", inline: bool = False):
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
    payload, mime = (exporters.html_report(stories, reqs, project["name"] + " backlog") if format == "html"
                     else entry[0](stories, reqs))
    slug = re.sub(r"[^a-z0-9]+", "-", project["name"].lower()).strip("-") or "backlog"
    headers = {"Content-Disposition": '%s; filename="%s-%s.%s"' % ("inline" if inline and format == "html" else "attachment",
                                                                     slug, format, entry[1])}
    if format == "html":
        # Static document: inline styles only, no scripts or remote content.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return Response(payload, media_type=mime, headers=headers)



# ---- baselines (frozen, versioned snapshots of the backlog) ---------------

class BaselineIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    scope: str = "approved"


SNAPSHOT_STORY_FIELDS = ("title", "narrative", "acceptance_criteria", "priority", "story_points", "status", "epic", "feature",
                         "requirement_ids", "variant_criteria", "open_questions")
SNAPSHOT_REQ_FIELDS = ("statement", "type", "status", "section")


def _snapshot(pid: str, scope: str) -> Dict[str, Any]:
    reqs = [r for r in _requirements(pid) if r["status"] == "approved" or (scope == "all" and r["status"] == "draft")]
    stories = [s for s in _stories(pid) if s["status"] == "approved" or (scope == "all" and s["status"] == "draft")]
    for item in reqs + stories:
        for k in ("issues", "external", "project_id"):
            item.pop(k, None)
    return {"requirements": reqs, "stories": stories}


def _baseline(pid: str, bid: str) -> Dict[str, Any]:
    b = db.one("SELECT * FROM baselines WHERE id=%s AND project_id=%s", (bid, pid))
    if not b:
        raise HTTPException(404, "Baseline not found")
    return b


def _diff(old: List[Dict[str, Any]], new: List[Dict[str, Any]], fields: Any) -> Dict[str, Any]:
    before, after = {x["key"]: x for x in old}, {x["key"]: x for x in new}
    changed = []
    for key in sorted(set(before) & set(after)):
        fields_changed = [f for f in fields if before[key].get(f) != after[key].get(f)]
        if fields_changed:
            changed.append({"key": key, "title": after[key].get("title") or after[key].get("statement", ""), "fields": fields_changed})
    label = lambda x: x.get("title") or x.get("statement", "")  # noqa: E731
    return {"added": [{"key": k, "title": label(after[k])} for k in sorted(set(after) - set(before))],
            "removed": [{"key": k, "title": label(before[k])} for k in sorted(set(before) - set(after))],
            "changed": changed}


@router.post("/api/projects/{pid}/baselines", status_code=201)
def create_baseline(pid: str, body: BaselineIn, request: Request):
    _project(pid)
    if body.scope not in ("approved", "all"):
        raise HTTPException(400, "scope must be approved or all")
    snap = _snapshot(pid, body.scope)
    if not snap["stories"] and not snap["requirements"]:
        raise HTTPException(400, "Nothing to baseline. Approve requirements or stories first, or include drafts.")
    who = history.actor(request)
    with db.conn() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (pid,))  # serialize version numbering per project
        version = c.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM baselines WHERE project_id=%s", (pid,)).fetchone()["v"]
        bid = db.new_id()
        c.execute("INSERT INTO baselines (id, project_id, version, name, note, scope, actor, snapshot, created) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (bid, pid, version, body.name.strip(), body.note.strip(), body.scope, who, json.dumps(snap), db.now()))
        history.record(c, pid, "baseline", bid, "v%d" % version, "baselined", who,
                       "%s: %d requirements, %d stories" % (body.name.strip(), len(snap["requirements"]), len(snap["stories"])))
    return {"id": bid, "version": version}


@router.get("/api/projects/{pid}/baselines")
def list_baselines(pid: str):
    _project(pid)
    return db.rows("SELECT id, version, name, note, scope, actor, created, jsonb_array_length(snapshot->'requirements') AS requirements, "
                   "jsonb_array_length(snapshot->'stories') AS stories FROM baselines WHERE project_id=%s ORDER BY version DESC", (pid,))


@router.get("/api/projects/{pid}/baselines/{bid}/compare")
def compare_baseline(pid: str, bid: str, to: str = "current"):
    """Differences from a baseline to another baseline (id) or to the current backlog."""
    base = _baseline(pid, bid)
    if to == "current":
        target, label = _snapshot(pid, "all"), "current backlog"
    else:
        other = _baseline(pid, to)
        target, label = other["snapshot"], "v%d" % other["version"]
    return {"from": "v%d" % base["version"], "to": label,
            "stories": _diff(base["snapshot"]["stories"], target["stories"], SNAPSHOT_STORY_FIELDS),
            "requirements": _diff(base["snapshot"]["requirements"], target["requirements"], SNAPSHOT_REQ_FIELDS)}


@router.get("/api/projects/{pid}/baselines/{bid}/export")
def export_baseline(pid: str, bid: str, format: str = "xlsx"):
    project = _project(pid)
    b = _baseline(pid, bid)
    entry = exporters.EXPORTERS.get(format)
    if not entry:
        raise HTTPException(400, "Unknown format")
    snap = b["snapshot"]
    title = "%s backlog v%d (%s)" % (project["name"], b["version"], b["name"])
    payload, mime = (exporters.html_report(snap["stories"], snap["requirements"], title) if format == "html"
                     else entry[0](snap["stories"], snap["requirements"]))
    slug = re.sub(r"[^a-z0-9]+", "-", project["name"].lower()).strip("-") or "backlog"
    headers = {"Content-Disposition": 'attachment; filename="%s-v%d-%s.%s"' % (slug, b["version"], format, entry[1])}
    if format == "html":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return Response(payload, media_type=mime, headers=headers)



# ---- review discussions -----------------------------------------------------

class CommentIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=4000)


def _item_kind(pid: str, item_id: str) -> str:
    if db.one("SELECT 1 AS x FROM requirements WHERE id=%s AND project_id=%s", (item_id, pid)):
        return "requirement"
    if db.one("SELECT 1 AS x FROM stories WHERE id=%s AND project_id=%s", (item_id, pid)):
        return "story"
    raise HTTPException(404, "Item not found")


@router.get("/api/projects/{pid}/comments")
def list_comments(pid: str, item_id: str = ""):
    _project(pid)
    if item_id:
        return db.rows("SELECT * FROM comments WHERE project_id=%s AND item_id=%s ORDER BY created", (pid, item_id))
    # Open-discussion counts per item, for badges in lists and the story map.
    return {r["item_id"]: r["n"] for r in db.rows(
        "SELECT item_id, count(*) AS n FROM comments WHERE project_id=%s AND NOT resolved GROUP BY item_id", (pid,))}


@router.post("/api/projects/{pid}/comments", status_code=201)
def add_comment(pid: str, body: CommentIn, request: Request):
    _project(pid)
    kind = _item_kind(pid, body.item_id)
    who = history.actor(request)
    cid = db.new_id()
    with db.conn() as c:
        c.execute("INSERT INTO comments (id, project_id, item_type, item_id, author, body, created) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (cid, pid, kind, body.item_id, who, body.body.strip(), db.now()))
        seq = c.execute("SELECT seq FROM %s WHERE id=%%s" % ("requirements" if kind == "requirement" else "stories"),
                        (body.item_id,)).fetchone()["seq"]
        history.record(c, pid, kind, body.item_id, history.item_key(kind, seq), "commented", who, body.body.strip()[:160])
    return db.one("SELECT * FROM comments WHERE id=%s", (cid,))


@router.post("/api/projects/{pid}/comments/{cid}/resolve")
def resolve_comment(pid: str, cid: str, request: Request):
    row = db.one("SELECT * FROM comments WHERE id=%s AND project_id=%s", (cid, pid))
    if not row:
        raise HTTPException(404, "Comment not found")
    resolved = not row["resolved"]
    db.execute("UPDATE comments SET resolved=%s, resolved_by=%s WHERE id=%s", (resolved, history.actor(request) if resolved else "", cid))
    return db.one("SELECT * FROM comments WHERE id=%s", (cid,))


@router.delete("/api/projects/{pid}/comments/{cid}", status_code=204)
def delete_comment(pid: str, cid: str, request: Request):
    row = db.one("SELECT author FROM comments WHERE id=%s AND project_id=%s", (cid, pid))
    if not row:
        raise HTTPException(404, "Comment not found")
    if row["author"] != history.actor(request) and getattr(request.state, "role", "admin") != "admin":
        raise HTTPException(403, "Only the author or an administrator can delete a comment")
    db.execute("DELETE FROM comments WHERE id=%s", (cid,))
