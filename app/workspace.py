"""Project workspace API: projects, sources, AI runs, requirements, stories, traceability, export, push."""
import csv
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import audit, config, db, embeddings, exporters, history, integrations, llm, metrics, parsing, pipeline, quality, settings, users
from .fairuse import ai_fair_use
from .profiles import PLATFORMS

log = logging.getLogger("fabulist")
router = APIRouter()
STATUSES = {"draft", "approved", "excluded"}
STORY_STATUSES = {"draft", "approved", "rejected"}
STAGES = {"requirements", "stories"}


# ---- models --------------------------------------------------------------

def _choice(default: str, values: List[str], description: str) -> Any:
    """A string field validated in the route (so the answer stays a readable 400), but documented in the OpenAPI schema with its allowed values."""
    return Field(default=default, description=description, json_schema_extra={"enum": values})


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: str = "generic"
    description: str = Field(default="", max_length=2000)
    visibility: str = _choice("everyone", ["everyone", "members"], "`members`: only the people chosen under the project's access, and administrators, can see it.")


class AccessIn(BaseModel):
    visibility: str = Field(description="Who can see the project.", json_schema_extra={"enum": ["everyone", "members"]})
    members: List[str] = Field(default_factory=list, max_length=500, description="Usernames (named accounts) chosen when visibility is `members`; the caller always stays in.")


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    platform: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=2000)


class TextDocIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class RunIn(BaseModel):
    stage: str = Field(description="What to run.", json_schema_extra={"enum": ["requirements", "stories"]})
    provider_id: Optional[str] = Field(default=None, description="AI provider to use; the default provider when omitted.")
    scope: str = _choice("all", ["all", "approved"], "Stories stage: which requirements are in scope: approved only, or approved and draft.")
    documents: str = _choice("all", ["all", "new", "reset"], "Requirements stage. `all` reads every BRD in one run and replaces the requirements; `new` reads only the BRDs not read yet "
                             "(as many as fit 'Max sections per run') and adds to what exists; `reset` starts over in batches: the first batch replaces everything when it is "
                             "saved, the rest then show as new.")
    stories_for: str = _choice("all", ["all", "uncovered", "reset"], "Stories stage. `all` writes stories for every requirement in scope and replaces the stories; `uncovered` writes "
                               "stories only for requirements no story covers and adds them; `reset` starts over in batches, as for requirements.")


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=6, ge=1, le=20)


class RefineIn(BaseModel):
    provider_id: Optional[str] = None


class AssignIn(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    assignee: str = Field(default="", max_length=64)


class MergeIn(BaseModel):
    keep: str = Field(max_length=40)
    merge: List[str] = Field(min_length=1, max_length=200)


class SetFieldIn(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    field: str = Field(max_length=40)
    value: str = Field(max_length=200)


class SplitIn(BaseModel):
    statements: List[str] = Field(min_length=2, max_length=10)


class TagIn(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=2000)
    tag: str = Field(max_length=80)
    remove: bool = False


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
  (SELECT count(*) FROM stories s WHERE s.project_id = p.id AND s.status = 'approved') AS stories_approved,
  coalesce((SELECT max(h.created) FROM history h WHERE h.project_id = p.id), p.created) AS last_activity
FROM projects p
"""


@router.post("/api/projects", status_code=201)
def create_project(body: ProjectIn, request: Request):
    if body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    if body.visibility not in ("everyone", "members"):
        raise HTTPException(400, "Unknown visibility")
    pid = db.new_id()
    who = history.actor(request)
    with db.conn() as c:
        c.execute("INSERT INTO projects (id, name, platform, description, created, visibility) VALUES (%s,%s,%s,%s,%s,%s)",
                  (pid, body.name.strip(), body.platform, body.description.strip(), db.now(), body.visibility))
        if body.visibility == "members" and who != "anonymous":  # whoever makes a private project is its first member
            c.execute("INSERT INTO project_members (project_id, username, added_by, created) VALUES (%s,%s,%s,%s)", (pid, who, who, db.now()))
        history.record(c, pid, "project", pid, "", "created", who, "%s (%s%s)" % (body.name.strip(), body.platform, ", private" if body.visibility == "members" else ""))
    return _project(pid)


@router.get("/api/projects")
def list_projects(request: Request, archived: bool = False):
    sql = PROJECT_COUNTS + " WHERE p.archived IS %s NULL" % ("NOT" if archived else "")
    args: List[Any] = []
    if getattr(request.state, "role", "admin") != "admin":  # administrators see everything; everyone else the open projects and their own
        sql += " AND (p.visibility = 'everyone' OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.username = %s))"
        args.append(history.actor(request))
    return db.rows(sql + " ORDER BY p.created DESC", args)


def _like(q: str) -> str:
    """The query as a case-insensitive 'contains' pattern in which % and _ are ordinary characters."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/api/search")
def search_everywhere(request: Request, q: str = "", limit: int = 30):
    """Projects by name, requirements by statement and stories by title or narrative, across the projects this person may see (archived ones
    are left out). The same visibility rule as the project list: private projects exist only for their members and administrators."""
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Type at least 2 characters")
    limit = max(1, min(limit, 50))
    pattern = _like(q)
    visible, args = "p.archived IS NULL", []  # type: str, List[Any]
    if getattr(request.state, "role", "admin") != "admin":
        visible += " AND (p.visibility = 'everyone' OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.username = %s))"
        args.append(history.actor(request))
    projects = db.rows("SELECT p.id, p.name, p.platform FROM projects p WHERE %s AND p.name ILIKE %%s ORDER BY p.name LIMIT %%s" % visible, (*args, pattern, limit))
    reqs = db.rows("SELECT r.project_id, p.name AS project_name, r.seq, r.status, r.data->>'statement' AS statement FROM requirements r JOIN projects p ON p.id = r.project_id "
                   "WHERE %s AND r.data->>'statement' ILIKE %%s ORDER BY p.name, r.seq LIMIT %%s" % visible, (*args, pattern, limit))
    stories = db.rows("SELECT s.project_id, p.name AS project_name, s.seq, s.status, s.data->>'title' AS title FROM stories s JOIN projects p ON p.id = s.project_id "
                      "WHERE %s AND (s.data->>'title' ILIKE %%s OR s.data->>'narrative' ILIKE %%s) ORDER BY p.name, s.seq LIMIT %%s" % visible, (*args, pattern, pattern, limit))
    return {"projects": projects,
            "requirements": [dict(r, key=history.item_key("requirement", r.pop("seq"))) for r in reqs],
            "stories": [dict(s, key=history.item_key("story", s.pop("seq"))) for s in stories]}


@router.get("/api/me/assigned")
def assigned_to_me(request: Request):
    """What is waiting for this person: requirements and stories assigned to them that are still drafts, by project (the projects they may see,
    not archived), the project with the most waiting first."""
    me = history.actor(request)
    if me == "anonymous":
        return {"requirements": 0, "stories": 0, "projects": []}
    visible, args = "p.archived IS NULL", []  # type: str, List[Any]
    if getattr(request.state, "role", "admin") != "admin":
        visible += " AND (p.visibility = 'everyone' OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.username = %s))"
        args.append(me)
    rows = db.rows("SELECT p.id AS project_id, p.name AS project_name, "
                   "(SELECT count(*) FROM requirements r WHERE r.project_id = p.id AND r.status = 'draft' AND r.data->>'assignee' = %%s) AS requirements, "
                   "(SELECT count(*) FROM stories s WHERE s.project_id = p.id AND s.status = 'draft' AND s.data->>'assignee' = %%s) AS stories "
                   "FROM projects p WHERE %s" % visible, (me, me, *args))
    rows = sorted([r for r in rows if r["requirements"] or r["stories"]], key=lambda r: (-(r["requirements"] + r["stories"]), r["project_name"]))
    return {"requirements": sum(r["requirements"] for r in rows), "stories": sum(r["stories"] for r in rows), "projects": rows}


@router.get("/api/people")
def people() -> List[Dict[str, str]]:
    """Named accounts, for choosing who can see a project (names and roles only)."""
    return db.rows("SELECT username, role FROM users WHERE disabled IS NOT TRUE ORDER BY username")


SAMPLE_BRD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "order-to-cash-brd.md")


@router.post("/api/projects/sample", status_code=201)
def create_sample_project(request: Request):
    """A new project holding a small order-to-cash BRD, to try the workflow with (extraction works offline, with no AI provider)."""
    with open(SAMPLE_BRD, encoding="utf-8") as f:
        text = f.read()
    names = {p["name"] for p in db.rows("SELECT name FROM projects")}
    name = "Sample: order to cash"
    n = 1
    while name in names:
        n += 1
        name = "Sample: order to cash (%d)" % n
    project = create_project(ProjectIn(name=name, platform="generic", description="A small BRD to try the workflow with: extract requirements, review them, generate stories."), request)
    _document_added(project["id"], _add_document(project["id"], "order-to-cash-brd.md", text), request)
    return project


@router.get("/api/projects/{pid}/access")
def get_access(pid: str):
    project = _project(pid)
    return {"visibility": project["visibility"], "members": [r["username"] for r in db.rows("SELECT username FROM project_members WHERE project_id=%s ORDER BY username", (pid,))]}


@router.put("/api/projects/{pid}/access")
def set_access(pid: str, body: AccessIn, request: Request):
    _project(pid)
    if body.visibility not in ("everyone", "members"):
        raise HTTPException(400, "Unknown visibility")
    who = history.actor(request)
    members = sorted({m.strip() for m in body.members if m.strip()} | ({who} if body.visibility == "members" and who != "anonymous" else set()))
    unknown = [m for m in members if m != who and not db.one("SELECT 1 AS x FROM users WHERE username=%s", (m,))]
    if unknown:
        raise HTTPException(400, "No such account: %s" % ", ".join(unknown[:5]))
    with db.conn() as c:
        c.execute("UPDATE projects SET visibility=%s WHERE id=%s", (body.visibility, pid))
        c.execute("DELETE FROM project_members WHERE project_id=%s", (pid,))
        for m in members:
            c.execute("INSERT INTO project_members (project_id, username, added_by, created) VALUES (%s,%s,%s,%s)", (pid, m, who, db.now()))
        history.record(c, pid, "project", pid, "", "access changed", who,
                       "Visible to everyone" if body.visibility == "everyone" else "Private to %s" % ", ".join(members), {"visibility": body.visibility, "members": members})
    return get_access(pid)


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
    p["documents_new"] = db.one("SELECT count(*) AS n FROM documents WHERE project_id=%s AND extracted IS NULL", (pid,))["n"]
    p["requirements_uncovered"] = sum(1 for r in active if r["key"] not in covered)
    p["coverage"] = round(100 * sum(1 for r in active if r["key"] in covered) / len(active)) if active else 0
    p["requirement_quality"] = round(sum(r["quality_score"] for r in active) / len(active)) if active else 0
    p["story_quality"] = round(sum(s["quality_score"] for s in live) / len(live)) if live else 0
    p["stories_ready"] = sum(1 for s in live if s["ready"])
    p["requirements_flagged"] = sum(1 for r in active if any(i["severity"] != "info" for i in r["issues"]))
    p["requirement_types"] = {t: sum(1 for r in active if r["type"] == t) for t in pipeline.REQ_TYPES}
    p["runs"] = {st: db.one("SELECT * FROM runs WHERE project_id=%s AND stage=%s ORDER BY created DESC LIMIT 1", (pid, st))
                 for st in STAGES}
    for run in p["runs"].values():
        if run and run["status"] == "queued":  # where it stands in the line, oldest first
            run["queue_position"] = db.one("SELECT count(*) AS n FROM runs WHERE status='queued' AND (created, id) <= (%s, %s)", (run["created"], run["id"]))["n"]
            run["queue_length"] = db.one("SELECT count(*) AS n FROM runs WHERE status='queued'")["n"]
    return p


@router.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectPatch, request: Request):
    p = _project(pid)
    if body.platform is not None and body.platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    new = {"name": (body.name or p["name"]).strip(), "platform": body.platform or p["platform"],
           "description": (p["description"] if body.description is None else body.description).strip()}
    with db.conn() as c:
        c.execute("UPDATE projects SET name=%s, platform=%s, description=%s WHERE id=%s", (new["name"], new["platform"], new["description"], pid))
        changes = history.diff({k: p[k] for k in new}, new)
        if changes:
            history.record(c, pid, "project", pid, "", "edited", history.actor(request), new["name"], changes)
    return _project(pid)


def _set_archived(pid: str, request: Request, archive: bool) -> Dict[str, Any]:
    project = _project(pid)
    if bool(project["archived"]) == archive:
        raise HTTPException(409, "Project is already %s" % ("archived" if archive else "active"))
    with db.conn() as c:
        c.execute("UPDATE projects SET archived=%s WHERE id=%s", (db.now() if archive else None, pid))
        history.record(c, pid, "project", pid, "", "archived" if archive else "restored", history.actor(request), project["name"])
    return {"id": pid, "archived": archive}


@router.post("/api/projects/{pid}/archive")
def archive_project(pid: str, request: Request):
    return _set_archived(pid, request, True)


@router.post("/api/projects/{pid}/restore")
def restore_project(pid: str, request: Request):
    return _set_archived(pid, request, False)


@router.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str, request: Request):
    # Deleting cascades to the project's audit trail, so it is a deliberate two-step, admin-only act.
    project = _project(pid)
    if getattr(request.state, "role", "admin") != "admin":
        raise HTTPException(403, "Only administrators can permanently delete a project")
    if not project["archived"]:
        raise HTTPException(409, "Archive the project before deleting it permanently")
    held = db.one("SELECT (SELECT count(*) FROM documents WHERE project_id=%s) AS documents, (SELECT count(*) FROM requirements WHERE project_id=%s) AS requirements, "
                  "(SELECT count(*) FROM stories WHERE project_id=%s) AS stories", (pid, pid, pid))
    db.execute("DELETE FROM projects WHERE id = %s", (pid,))
    # The project's own trail goes with it, so the deletion is the one thing that must outlive it.
    audit.record(request, "project deleted", project["name"], "%d BRDs, %d requirements, %d stories" % (held["documents"], held["requirements"], held["stories"]),
                 {"project_id": pid})


# ---- documents -----------------------------------------------------------

_parse_lock = threading.Lock()
_parse_state: Optional[Tuple[int, threading.BoundedSemaphore]] = None


def _parse_slots() -> threading.BoundedSemaphore:
    global _parse_state
    size = max(1, config.MAX_CONCURRENT_PARSES)
    with _parse_lock:
        if _parse_state is None or _parse_state[0] != size:
            _parse_state = (size, threading.BoundedSemaphore(size))
        return _parse_state[1]


def _reset_parse_slots() -> None:
    global _parse_state
    with _parse_lock:
        _parse_state = None


@contextmanager
def _document_slot() -> Iterator[None]:
    """Reading and indexing a large document takes tens of MB and seconds of CPU; twenty at once exhausted the pod's memory. A few at a
    time, the rest wait their turn, and are told to retry if the wait is long instead of tying up a worker thread indefinitely."""
    slots = _parse_slots()
    if not slots.acquire(timeout=config.PARSE_WAIT_SECONDS):
        raise HTTPException(503, "The server is busy reading other documents. Try this upload again in a moment.", headers={"Retry-After": "15"})
    try:
        yield
    finally:
        slots.release()
        _give_memory_back()


def _give_memory_back() -> None:
    """Ask glibc to return the memory a large parse freed to the operating system (it otherwise keeps it for reuse, and a run of big
    documents ends with a process that is mostly cached garbage). Harmless where there is no glibc."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _add_document(pid: str, filename: str, text: str) -> Dict[str, Any]:
    did = db.new_id()
    db.execute("INSERT INTO documents (id, project_id, filename, text, created) VALUES (%s,%s,%s,%s,%s)",
               (did, pid, filename, text, db.now()))
    indexed, index_error = 0, ""
    try:
        indexed = embeddings.index_document(pid, did, text)
    except Exception as exc:  # retrieval degrades; generation still works without it
        log.exception("indexing failed for document %s", did)
        index_error = str(exc)[:200] or type(exc).__name__
    return {"id": did, "filename": filename, "chars": len(text), "indexed_chunks": indexed, "index_error": index_error}


def _document_added(pid: str, doc: Dict[str, Any], request: Request) -> Dict[str, Any]:
    with db.conn() as c:
        history.record(c, pid, "document", doc["id"], "", "added", history.actor(request), doc["filename"])
    return doc


@router.post("/api/projects/{pid}/documents", status_code=201)
def upload_document(pid: str, request: Request, file: UploadFile = File(...)):
    # A plain `def`, not `async def`: parsing, embedding and the inserts are blocking work, and FastAPI runs a plain def in its thread
    # pool. As an async handler this held the whole event loop (health probes included) for the length of every upload.
    _project(pid)
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "document.txt")[:200]
    with _document_slot():
        try:
            text = parsing.extract_text(name, data)
        except parsing.ParseError as exc:
            raise HTTPException(400, str(exc))
        return _document_added(pid, _add_document(pid, name, text), request)


@router.post("/api/projects/{pid}/documents/text", status_code=201)
def add_text_document(pid: str, body: TextDocIn, request: Request):
    _project(pid)
    with _document_slot():
        return _document_added(pid, _add_document(pid, body.name.strip(), parsing.clip(body.text)), request)


@router.get("/api/projects/{pid}/documents")
def list_documents(pid: str):
    _project(pid)
    # One pass over the project's chunks, not a count per document: the correlated subquery was quadratic (5 s for 1,100 BRDs).
    return db.rows("SELECT d.id, d.filename, length(d.text) AS chars, d.created, d.source, d.path, d.repo_id, d.extracted, coalesce(c.n, 0) AS chunks "
                   "FROM documents d LEFT JOIN (SELECT document_id, count(*) AS n FROM chunks WHERE project_id = %s GROUP BY document_id) c "
                   "ON c.document_id = d.id WHERE d.project_id = %s ORDER BY d.created", (pid, pid))


@router.get("/api/projects/{pid}/documents/{did}")
def get_document(pid: str, did: str):
    d = db.one("SELECT id, filename, text, created FROM documents WHERE id = %s AND project_id = %s", (did, pid))
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.delete("/api/projects/{pid}/documents/{did}", status_code=204)
def delete_document(pid: str, did: str, request: Request):
    _project(pid)
    with db.conn() as c:
        row = c.execute("DELETE FROM documents WHERE id = %s AND project_id = %s RETURNING filename", (did, pid)).fetchone()
        if row:
            history.record(c, pid, "document", did, "", "removed", history.actor(request), row["filename"])


@router.post("/api/projects/{pid}/search")
def search_documents(pid: str, body: SearchIn):
    _project(pid)
    return embeddings.search(pid, body.query, body.k)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    provider_id: Optional[str] = None


@router.post("/api/projects/{pid}/ask", dependencies=[Depends(ai_fair_use)])
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


def _execute_run(run_id: str, pid: str, stage: str, provider_id: str, scope: str, mode: str = "all") -> None:
    # A queued run waits for a pool worker; claim it now. Exactly one server can: if it was cancelled, failed, or already taken by another
    # server (a rolling deploy briefly runs two), there is nothing to do.
    if not db.one("UPDATE runs SET status='running', claimed=TRUE, owner=%s, updated=%s, started=%s WHERE id=%s AND claimed = FALSE AND status IN ('queued', 'running') RETURNING id",
                  (INSTANCE, db.now(), db.now(), run_id)):
        return
    started = time.monotonic()
    usage = {"in": 0, "out": 0}

    def progress(msg: str) -> None:
        # Called between AI requests, so it doubles as the cancellation checkpoint; also the run's heartbeat.
        with db.conn() as c:
            row = c.execute("UPDATE runs SET progress=%s, tokens_in=%s, tokens_out=%s, updated=%s WHERE id=%s RETURNING cancel_requested",
                            (msg[:200], usage["in"], usage["out"], db.now(), run_id)).fetchone()
        if row and row["cancel_requested"]:
            raise RunCancelled()

    def cancelled() -> None:
        row = db.one("SELECT cancel_requested FROM runs WHERE id=%s", (run_id,))
        if row and row["cancel_requested"]:
            raise RunCancelled()

    llm.set_cancel_check(cancelled)  # also checked inside the provider's retries and backoff
    try:
        _run_stage(run_id, pid, stage, provider_id, scope, started, usage, progress, mode)
    finally:
        llm.set_cancel_check(None)
        metrics.inc("fabulist_llm_tokens_total", usage["in"], direction="in")
        metrics.inc("fabulist_llm_tokens_total", usage["out"], direction="out")
    _record_run_in_history(run_id, pid)


def _record_run_in_history(run_id: str, pid: str) -> None:
    """Who ran what, and how it ended, goes in the project's audit trail (the run list says it too, but the trail is what gets exported as evidence)."""
    try:
        run = db.one("SELECT stage, status, actor, item_count, error, provider, model, duration_ms FROM runs WHERE id=%s", (run_id,))
        if not run or run["status"] not in ("done", "failed", "cancelled"):
            return
        what = ("%d item%s" % (run["item_count"], "" if run["item_count"] == 1 else "s")) if run["status"] == "done" else (run["error"] or run["status"])[:150]
        engine = " · %s%s" % (run["provider"], " " + run["model"] if run["model"] else "") if run["provider"] else ""
        with db.conn() as c:
            history.record(c, pid, "run", run_id, "", "ai run " + run["status"], run["actor"] or "anonymous",
                           "%s · %s%s · %.1f s" % (run["stage"], what, engine, (run["duration_ms"] or 0) / 1000))
    except Exception:  # noqa: BLE001 - the trail is not allowed to break a run (the project may be gone, for instance)
        log.exception("could not record run %s in the audit trail", run_id)


def _run_stage(run_id: str, pid: str, stage: str, provider_id: str, scope: str, started: float,
               usage: Dict[str, int], progress: Any, mode: str = "all") -> None:
    try:
        project = _project(pid)
        profile = settings.get_provider(provider_id)
        opts = settings.get_settings()
        # `mode` is `documents` (all / new / reset) for the requirements stage and `stories_for` (all / uncovered) for the stories stage
        incremental = stage == "requirements" and mode in ("new", "reset")
        reset = incremental and mode == "reset"
        adding_stories = stage == "stories" and mode in ("uncovered", "reset")
        reset_stories = adding_stories and mode == "reset"
        existing: List[Dict[str, Any]] = []
        before: Dict[str, str] = {}
        if incremental:
            # Only the BRDs not yet read, as many as fit the limit; the requirements that exist stay exactly as they are.
            try:
                batch, _, _ = _next_batch(pid, opts, include_read=reset)
            except _TooBig as exc:
                raise ValueError(str(exc))
            if not batch:
                raise ValueError("No new BRDs to extract.")
            docs = db.rows("SELECT id, filename, text FROM documents WHERE id = ANY(%s) ORDER BY created, id", ([d["id"] for d in batch],))
            existing = [] if reset else [dict(r["data"], _id=r["id"]) for r in db.rows("SELECT id, data FROM requirements WHERE project_id=%s ORDER BY seq", (pid,))]
            before = {e["_id"]: e.get("source_ref", "") for e in existing}
            items = pipeline.extract_requirements(docs, project["platform"], profile, opts, progress, usage, existing=existing)
        elif stage == "requirements":
            docs = db.rows("SELECT id, filename, text FROM documents WHERE project_id=%s ORDER BY created", (pid,))
            items = pipeline.extract_requirements(docs, project["platform"], profile, opts, progress, usage)
        elif adding_stories:
            # Only the requirements no story covers, as many feature-area groups as fit the limit; existing stories stay exactly as they are.
            reqs, _, _ = _uncovered_batch(pid, scope, opts, ignore_stories=reset_stories)
            if not reqs:
                raise ValueError("No requirements without a story.")
            taken = set() if reset_stories else {(pipeline._norm_key(r["data"].get("title", "")), pipeline._norm_key(r["data"].get("feature") or ""))
                                                 for r in db.rows("SELECT data FROM stories WHERE project_id=%s", (pid,))}
            items = pipeline.generate_stories(reqs, pid, project["platform"], profile, opts, progress, usage, taken=taken)
        else:
            reqs = [r for r in _requirements(pid)
                    if r["status"] == "approved" or (scope == "all" and r["status"] == "draft")]
            if not reqs:
                raise ValueError("No requirements in scope. Extract and review requirements first.")
            items = pipeline.generate_stories(reqs, pid, project["platform"], profile, opts, progress, usage)
        progress("Saving results")
        if stage == "requirements":
            sql = "INSERT INTO requirements (id, project_id, seq, data) VALUES (%s,%s,%s,%s)"
            first_seq = db.one("SELECT coalesce(max(seq) + 1, 0) AS n FROM requirements WHERE project_id=%s", (pid,))["n"] if incremental and not reset else 0
            params = [(db.new_id(), pid, first_seq + i, json.dumps(x)) for i, x in enumerate(items)]
        else:
            sql = "INSERT INTO stories (id, project_id, run_id, seq, data) VALUES (%s,%s,%s,%s,%s)"
            first_story = db.one("SELECT coalesce(max(seq) + 1, 0) AS n FROM stories WHERE project_id=%s", (pid,))["n"] if adding_stories and not reset_stories else 0
            params = [(db.new_id(), pid, run_id, first_story + i, json.dumps(x)) for i, x in enumerate(items)]
        with db.conn() as c:
            # A run that was cancelled or marked stuck while its AI call hung must not replace results when it finally returns.
            current = c.execute("SELECT status FROM runs WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
            if not current or current["status"] != "running":
                raise RunCancelled()
            if (not incremental and not adding_stories) or reset or reset_stories:
                # Stories link to requirement keys, so re-extracting requirements resets the backlog too.
                c.execute("DELETE FROM stories WHERE project_id = %s", (pid,))
                if stage == "requirements":
                    c.execute("DELETE FROM requirements WHERE project_id = %s", (pid,))
            if reset:
                c.execute("UPDATE documents SET extracted = NULL WHERE project_id = %s", (pid,))  # everything is unread again, bar this batch (marked below)
            with c.cursor() as cur:
                cur.executemany(sql, params)
            for e in existing:  # a requirement repeated in the new BRDs keeps one row but learns where else it was stated
                if e.get("source_ref", "") != before[e["_id"]]:
                    c.execute("UPDATE requirements SET data = jsonb_set(data, '{source_ref}', to_jsonb(%s::text)), rev = rev + 1 WHERE id = %s", (e["source_ref"], e["_id"]))
            if stage == "requirements":
                c.execute("UPDATE documents SET extracted = %s WHERE id = ANY(%s)", (db.now(), [d["id"] for d in docs]))
            c.execute("UPDATE runs SET status='done', item_count=%s, story_count=%s, progress='', tokens_in=%s, "
                      "tokens_out=%s, duration_ms=%s, requests=%s WHERE id=%s",
                      (len(items), len(items), usage["in"], usage["out"], int((time.monotonic() - started) * 1000), usage.get("requests", 0), run_id))
        metrics.inc("fabulist_runs_total", stage=stage, status="done")
    except RunCancelled:
        metrics.inc("fabulist_runs_total", stage=stage, status="cancelled")
        db.execute("UPDATE runs SET status='cancelled', error='Cancelled; existing data was kept', progress='', "
                   "duration_ms=%s WHERE id=%s AND status='running'", (int((time.monotonic() - started) * 1000), run_id))
    except Exception as exc:
        metrics.inc("fabulist_runs_total", stage=stage, status="failed")
        log.exception("run %s failed", run_id)
        msg = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Internal error (%s)" % type(exc).__name__
        db.execute("UPDATE runs SET status='failed', error=%s, progress='', tokens_in=%s, tokens_out=%s, duration_ms=%s "
                   "WHERE id=%s AND status='running'", (msg[:500], usage["in"], usage["out"], int((time.monotonic() - started) * 1000), run_id))
    finally:
        integrations.announce_run(run_id)


_ADMISSION_LOCK = 7_240_001  # arbitrary application key for pg_advisory_xact_lock: serialises run admission across requests
_pool_lock = threading.Lock()
_pool: Optional[Tuple[int, ThreadPoolExecutor]] = None


def _run_pool() -> ThreadPoolExecutor:
    """AI runs last minutes. Run in the web server's shared worker pool they starve every other request: 40 slow runs
    (its size) made the whole app, /healthz included, stop answering. They get their own bounded pool instead."""
    global _pool
    size = max(1, config.MAX_CONCURRENT_RUNS)
    with _pool_lock:
        if _pool is None or _pool[0] != size:
            if _pool:
                _pool[1].shutdown(wait=False)  # runs already started keep going
            _pool = (size, ThreadPoolExecutor(max_workers=size, thread_name_prefix="run"))
        return _pool[1]


def shutdown_runs() -> None:
    global _pool
    with _pool_lock:
        if _pool:
            _pool[1].shutdown(wait=False, cancel_futures=True)
        _pool = None  # a later start (tests, reload) creates a fresh pool instead of using a dead one


def _stale_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=config.STALE_RUN_MINUTES)).isoformat(timespec="milliseconds")


class _TooBig(Exception):
    """A single BRD that alone has more sections than one run may read."""
    def __init__(self, filename: str, sections: int, limit: int):
        super().__init__("%s produces %d sections, above the limit of %d. Raise 'Max sections per run' in Admin > Generation settings or split the document."
                         % (filename, sections, limit))


def _next_batch(pid: str, opts: Dict[str, Any], include_read: bool = False) -> Tuple[List[Dict[str, Any]], int, int]:
    """The BRDs not yet read that the next run would take, oldest first, as many as fit within 'Max sections per run', with their
    section count, and how many are left for later runs. A project of a thousand BRDs is read in a few such runs, none of which touches
    what the earlier ones produced."""
    limit, chunk = int(opts["max_chunks_per_run"]), int(opts["chunk_chars"])
    pending = db.rows("SELECT id, filename FROM documents WHERE project_id=%s AND (%s OR extracted IS NULL) ORDER BY created, id", (pid, include_read))
    batch: List[Dict[str, Any]] = []
    sections = 0
    for d in pending:
        n = len(pipeline.chunk_sections(db.one("SELECT text FROM documents WHERE id=%s", (d["id"],))["text"], chunk))
        if sections + n > limit:
            if not batch:
                raise _TooBig(d["filename"], n, limit)
            break
        batch.append(d)
        sections += n
    return batch, sections, len(pending) - len(batch)


def _uncovered_batch(pid: str, scope: str, opts: Dict[str, Any], ignore_stories: bool = False) -> Tuple[List[Dict[str, Any]], int, int]:
    """The requirements in scope that no story covers yet (all of them when starting over), as many feature-area groups as fit within
    'Max sections per run', how many groups that is, and how many requirements are left for later runs."""
    reqs = _requirements(pid)
    covered = set() if ignore_stories else {k for s in _stories(pid, reqs) for k in s.get("requirement_ids", [])}
    todo = [r for r in reqs if r["key"] not in covered and (r["status"] == "approved" or (scope == "all" and r["status"] == "draft"))]
    batches = pipeline.story_batches(todo)[: int(opts["max_chunks_per_run"])]
    taken = [r for _epic, _section, items in batches for r in items]
    return taken, len(batches), len(todo) - len(taken)


def _requests_needed(pid: str, stage: str, scope: str, opts: Dict[str, Any]) -> Tuple[int, int]:
    """(AI requests a run would make, items it reads), from the same batching the run uses. Documents are read a few at a time:
    a project with a thousand BRDs must not be held in memory just to count its sections."""
    if stage == "requirements":
        ids = [r["id"] for r in db.rows("SELECT id FROM documents WHERE project_id=%s ORDER BY created", (pid,))]
        sections = 0
        for i in range(0, len(ids), 25):
            for d in db.rows("SELECT text FROM documents WHERE id = ANY(%s)", (ids[i:i + 25],)):
                sections += len(pipeline.chunk_sections(d["text"], int(opts["chunk_chars"])))
        return sections, len(ids)
    reqs = [r for r in _requirements(pid) if r["status"] == "approved" or (scope == "all" and r["status"] == "draft")]
    return len(pipeline.story_batches(reqs)), len(reqs)


@router.get("/api/projects/{pid}/runs/estimate")
def estimate_run(pid: str, stage: str, scope: str = "all", documents: str = "all", stories_for: str = "all"):
    """What a run would ask of the AI provider, before anyone clicks: the number of requests (exact, from the same batching the
    run uses), and a time and token guess calibrated from this installation's own finished runs of that stage."""
    _project(pid)
    if stage not in STAGES or scope not in ("approved", "all") or documents not in ("all", "new", "reset") or stories_for not in ("all", "uncovered", "reset"):
        raise HTTPException(400, "Unknown stage, scope, documents or stories_for")
    opts = settings.get_settings()
    extra: Dict[str, Any] = {}
    if stage == "requirements":
        extra["new_documents"] = db.one("SELECT count(*) AS n FROM documents WHERE project_id=%s AND extracted IS NULL", (pid,))["n"]
    if stage == "requirements" and documents in ("new", "reset"):
        try:
            batch, requests, remaining = _next_batch(pid, opts, include_read=documents == "reset")
            items = len(batch)
            extra.update(batch_documents=len(batch), remaining_documents=remaining)
        except _TooBig as exc:
            requests, items = int(opts["max_chunks_per_run"]) + 1, 0
            extra.update(batch_documents=0, remaining_documents=extra["new_documents"], too_big=str(exc))
    elif stage == "stories" and stories_for in ("uncovered", "reset"):
        batch, requests, remaining = _uncovered_batch(pid, scope, opts, ignore_stories=stories_for == "reset")
        items = len(batch)
        extra.update(batch_requirements=len(batch), remaining_requirements=remaining)
    else:
        requests, items = _requests_needed(pid, stage, scope, opts)
    past = db.rows("SELECT requests, duration_ms, tokens_in + tokens_out AS tokens FROM runs WHERE stage=%s AND status='done' AND requests > 0 "
                   "ORDER BY created DESC LIMIT 10", (stage,))
    seconds = tokens = None
    basis = "No finished AI runs yet to base a time or cost estimate on."
    if past:
        total = sum(r["requests"] for r in past)
        seconds = round(requests * sum(r["duration_ms"] for r in past) / 1000 / total)
        tokens = round(requests * sum(r["tokens"] for r in past) / total)
        basis = "Based on your last %d run%s (%d requests)." % (len(past), "" if len(past) == 1 else "s", total)
    return dict({"stage": stage, "scope": scope, "documents": documents, "items": items, "requests": requests, "limit": int(opts["max_chunks_per_run"]),
                 "over_limit": requests > int(opts["max_chunks_per_run"]), "seconds": seconds, "tokens": tokens, "basis": basis}, **extra)


INSTANCE = uuid.uuid4().hex[:10]  # this server process, for run ownership
INSTANCE_DEAD_SECONDS = 45  # an instance that has not beaten for this long has stopped


def heartbeat() -> None:
    """Say this server instance is alive (every few seconds), and forget instances that have been gone for a day."""
    now = db.now()
    with db.conn() as c:
        c.execute("INSERT INTO instances (id, started, seen) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET seen = EXCLUDED.seen", (INSTANCE, now, now))
        c.execute("DELETE FROM instances WHERE seen < %s", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="milliseconds"),))


def recover_all_stuck_runs() -> int:
    """The same recovery for every project (the background sweeper); returns how many runs were marked."""
    return _recover_stuck_runs(None)


def _recover_orphaned_runs(pid: Optional[str]) -> int:
    """Running runs whose server instance stopped (it has not beaten for INSTANCE_DEAD_SECONDS, or never registered): nothing is executing them."""
    alive_since = (datetime.now(timezone.utc) - timedelta(seconds=INSTANCE_DEAD_SECONDS)).isoformat(timespec="milliseconds")
    marked = db.rows("UPDATE runs SET status='failed', cancel_requested=TRUE, progress='', error='Interrupted: the server that was running it stopped. Start it again.' "
                     "WHERE (%s::text IS NULL OR project_id=%s) AND status='running' AND owner IS NOT NULL AND owner <> %s "
                     "AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id = runs.owner AND i.seen >= %s) RETURNING id, stage, project_id",
                     (pid, pid, INSTANCE, alive_since))
    for r in marked:
        metrics.inc("fabulist_runs_total", stage=r["stage"], status="failed")
        integrations.announce_run(r["id"])
        _record_run_in_history(r["id"], r["project_id"])
    return len(marked)


def _recover_stuck_runs(pid: Optional[str]) -> int:
    """A run whose AI provider stopped answering would block its project until a restart. One with no progress for
    STALE_RUN_MINUTES is marked failed (and asked to stop), so a new run can start; if its thread ever wakes up it will
    find it is no longer running and write nothing."""
    cutoff = _stale_cutoff()
    marked = db.rows("UPDATE runs SET status='failed', cancel_requested=TRUE, progress='', error=%s "
                     "WHERE (%s::text IS NULL OR project_id=%s) AND status='running' AND coalesce(updated, created) < %s RETURNING id, stage, project_id",
                     ("Marked as stuck: no progress for %d minutes. The AI provider may have stopped responding; start a new run." % config.STALE_RUN_MINUTES, pid, pid, cutoff))
    for r in marked:
        metrics.inc("fabulist_runs_total", stage=r["stage"], status="failed")
        integrations.announce_run(r["id"])
        _record_run_in_history(r["id"], r["project_id"])
    return len(marked) + _recover_orphaned_runs(pid)


@router.post("/api/projects/{pid}/runs", status_code=202)
def start_run(pid: str, body: RunIn, request: Request):
    _project(pid)
    if body.stage not in STAGES:
        raise HTTPException(400, "Unknown stage")
    if body.scope not in ("approved", "all"):
        raise HTTPException(400, "Unknown scope")
    profile = settings.get_provider(body.provider_id) if body.provider_id else settings.default_provider()
    if profile["kind"] == "anthropic" and not profile.get("api_key"):  # fail at the click, not after a run has started
        raise HTTPException(400, "The provider %s has no API key. Add one in Admin > AI providers." % profile["name"])
    if body.stage == "requirements" and not db.one("SELECT 1 AS x FROM documents WHERE project_id=%s LIMIT 1", (pid,)):
        raise HTTPException(400, "Add at least one BRD document first")
    if body.documents not in ("all", "new", "reset") or body.stories_for not in ("all", "uncovered", "reset"):
        raise HTTPException(400, "Unknown documents or stories_for choice")
    mode = body.documents if body.stage == "requirements" else body.stories_for  # what the run was asked to do beyond its stage
    # Refuse what can never succeed before creating a run (it used to start, then fail seconds later with the same message).
    limit = int(settings.get_settings()["max_chunks_per_run"])
    if body.stage == "requirements" and body.documents in ("new", "reset"):
        try:
            if not _next_batch(pid, settings.get_settings(), include_read=body.documents == "reset")[0]:
                raise HTTPException(400, "No new BRDs to extract. Add BRDs on the Sources tab, or choose all BRDs to start over.")
        except _TooBig as exc:
            raise HTTPException(400, str(exc))
        needed = 0
    elif body.stage == "stories" and body.stories_for in ("uncovered", "reset"):
        if not _uncovered_batch(pid, body.scope, settings.get_settings(), ignore_stories=body.stories_for == "reset")[0]:
            raise HTTPException(400, "No requirements without a story to write stories for. Add requirements, or choose all requirements to start over." if body.stories_for == "uncovered"
                                else "No requirements in scope. Extract and review requirements first.")
        needed = 0
    else:
        needed, _ = _requests_needed(pid, body.stage, body.scope, settings.get_settings())
    if needed > limit and (body.stage == "requirements" or profile["kind"] != "mock"):
        raise HTTPException(400, ("Documents produce %d sections, above the limit of %d. Raise 'Max sections per run' in Admin > Generation settings "
                                  "or split the documents." % (needed, limit)) if body.stage == "requirements" else
                            ("Too many requirement groups (%d) for one run (limit %d). Raise 'Max sections per run' in Admin > Generation settings, "
                             "or generate from approved requirements only." % (needed, limit)))
    _recover_stuck_runs(pid)
    rid = db.new_id()
    # Admission is one atomic step under a database lock: twenty people starting at the same instant must not all see "a free slot"
    # (each count would be taken before any of the others had inserted its row, labelling waiting runs "running").
    with db.conn() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (_ADMISSION_LOCK,))
        if c.execute("SELECT 1 AS x FROM runs WHERE project_id=%s AND status IN ('running', 'queued')", (pid,)).fetchone():
            raise HTTPException(409, "A run is already in progress for this project")
        busy = c.execute("SELECT count(*) AS n FROM runs WHERE status='running' AND coalesce(updated, created) >= %s", (_stale_cutoff(),)).fetchone()["n"]
        waiting = c.execute("SELECT count(*) AS n FROM runs WHERE status='queued'").fetchone()["n"]
        # Runs that stopped making progress do not count as busy (recovered above). A full pool queues the run instead of turning people
        # away (with a real AI provider a run takes minutes to hours, so "try again later" is no answer); the queue itself is bounded, and
        # first come, first served: nobody jumps ahead of a run that is already waiting.
        status = "running" if busy < config.MAX_CONCURRENT_RUNS and not waiting else "queued"
        if status == "queued" and waiting >= config.MAX_QUEUED_RUNS:
            metrics.inc("fabulist_runs_total", stage=body.stage, status="rejected")
            raise HTTPException(429, "%d AI runs are in progress and the queue is full (%d waiting). Try again in a few minutes." % (busy, waiting),
                                headers={"Retry-After": "60"})
        c.execute("INSERT INTO runs (id, project_id, status, stage, provider, model, actor, created, provider_id, run_scope, run_documents) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (rid, pid, status, body.stage, profile["name"], profile["model"], history.actor(request), db.now(), profile["id"], body.scope, mode))
    _run_pool().submit(_execute_run, rid, pid, body.stage, profile["id"], body.scope, mode)
    return {"id": rid, "status": status}


def resume_waiting_runs() -> int:
    """After a restart, start again the runs that were still waiting for a slot (oldest first), instead of failing them. A waiting run has
    done no work yet, so nothing is lost; one that cannot be resumed (it predates this) is failed with a message. Returns how many resumed."""
    resumed = 0
    for r in db.rows("SELECT id, project_id, stage, provider_id, run_scope, run_documents FROM runs WHERE status='queued' AND claimed = FALSE ORDER BY created, id"):
        if not r["provider_id"]:
            db.execute("UPDATE runs SET status='failed', progress='', error='The server restarted while this run was waiting; start it again.' WHERE id=%s", (r["id"],))
            continue
        _run_pool().submit(_execute_run, r["id"], r["project_id"], r["stage"], r["provider_id"], r["run_scope"] or "all", r["run_documents"] or "all")
        resumed += 1
    return resumed


@router.post("/api/projects/{pid}/runs/{rid}/cancel")
def cancel_run(pid: str, rid: str):
    with db.conn() as c:
        # Still waiting for a slot: just drop it; the worker skips it when its turn comes.
        dropped = c.execute("UPDATE runs SET status='cancelled', error='Cancelled before it started', progress='' WHERE id=%s AND project_id=%s "
                            "AND status='queued' RETURNING id", (rid, pid)).fetchone()
        if dropped:
            return {"status": "cancelled"}
        row = c.execute("UPDATE runs SET cancel_requested=TRUE, progress='Cancelling…' WHERE id=%s AND project_id=%s "
                        "AND status='running' RETURNING id", (rid, pid)).fetchone()
    if not row:
        raise HTTPException(409, "Run is not running")
    return {"status": "cancelling"}


@router.get("/api/projects/{pid}/runs")
def list_runs(pid: str, limit: int = 20):
    _project(pid)
    return db.rows("SELECT * FROM runs WHERE project_id=%s ORDER BY created DESC, id LIMIT %s", (pid, max(1, min(limit, 100))))


# ---- requirements --------------------------------------------------------

REQ_FIELDS = {"statement", "type", "actors", "source_id", "rationale", "epic", "section", "source_ref",
              "product_scope", "compliance_tags", "notes", "assignee"}
STORY_FIELDS = {"epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority", "story_points",
                "labels", "requirement_ids", "product_scope", "variant_criteria", "compliance_tags", "open_questions", "notes", "assignee"}


_dupe_cache: "OrderedDict[str, Tuple[Tuple[int, int, int, int], Dict[str, List[str]]]]" = OrderedDict()
_dupe_lock = threading.Lock()


def _duplicates(pid: str, out: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Near-duplicate pairs for the project's requirements, kept until one changes. Every summary, list and estimate request needs them,
    and on thousands of requirements working them out is the most expensive part of answering. The stamp changes with every edit,
    status change (each bumps `rev`), addition and removal."""
    stamp = db.one("SELECT count(*) AS n, coalesce(sum(rev), 0) AS r, coalesce(sum(hashtext(id)::bigint), 0) AS h, "
                   "count(*) FILTER (WHERE status = 'excluded') AS x FROM requirements WHERE project_id=%s", (pid,))
    key = (stamp["n"], int(stamp["r"]), int(stamp["h"]), stamp["x"])
    with _dupe_lock:
        hit = _dupe_cache.get(pid)
        if hit and hit[0] == key:
            _dupe_cache.move_to_end(pid)
            return hit[1]
    dupes = quality.duplicate_pairs([r for r in out if r["status"] != "excluded"])
    with _dupe_lock:
        _dupe_cache[pid] = (key, dupes)
        _dupe_cache.move_to_end(pid)
        while len(_dupe_cache) > 32:  # bounded: only recently used projects are kept
            _dupe_cache.popitem(last=False)
    return dupes


def _requirements(pid: str) -> List[Dict[str, Any]]:
    out = [db.item_row(r, "REQ") for r in db.rows("SELECT * FROM requirements WHERE project_id=%s ORDER BY seq", (pid,))]
    dupes = _duplicates(pid, out)
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


STALE = "This item was changed by someone else since you opened it. Close it and open it again to see their changes."


def _stale() -> HTTPException:
    metrics.inc("fabulist_edit_conflicts_total")
    return HTTPException(409, STALE)


def _is_person(name: str) -> bool:
    """With named accounts, work is assigned to a person who exists and can sign in; without them it is free text (a name or a team)."""
    return not users.auth_enabled() or bool(db.one("SELECT 1 AS x FROM users WHERE username=%s AND disabled IS NOT TRUE", (name,)))


def _check_assignee(name: str) -> None:
    if not _is_person(name):
        raise HTTPException(400, "Unknown person: %s. Choose someone from the list of accounts." % name)


def _save_item(table: str, pid: str, iid: Optional[str], body: Dict[str, Any], who: str) -> str:
    statuses = STATUSES if table == "requirements" else STORY_STATUSES
    fields = REQ_FIELDS if table == "requirements" else STORY_FIELDS
    current: Dict[str, Any] = {}
    old_status = status = "draft"
    seq = rev = None
    expected = body.get("rev")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise HTTPException(400, "rev must be the revision number the item had when it was opened")
    if iid:
        row = db.one("SELECT * FROM %s WHERE id=%%s AND project_id=%%s" % table, (iid, pid))
        if not row:
            raise HTTPException(404, "Item not found")
        current = dict(row["data"])
        old_status = status = row["status"]
        seq, rev = row["seq"], row["rev"]
        if expected is not None and expected != rev:
            raise _stale()
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
    if item.get("assignee") and item["assignee"] != current.get("assignee", ""):
        _check_assignee(item["assignee"])
    kind = _kind(table)
    with db.conn() as c:
        if iid:
            changes = history.diff(dict(current, status=old_status), dict(item, status=status))
            if c.execute("UPDATE %s SET data=%%s, status=%%s, rev=rev+1 WHERE id=%%s AND rev=%%s" % table,
                         (json.dumps(item), status, iid, rev)).rowcount != 1:
                raise _stale()  # someone saved between our read and this write
            if changes:
                action = "status changed" if set(changes) == {"status"} else "edited"
                history.record(c, pid, kind, iid, history.item_key(kind, seq), action, who, _label(item), changes)
            if item.get("assignee") and item.get("assignee") != current.get("assignee", ""):
                integrations.announce_assignment(pid, kind, item["assignee"], [{"key": history.item_key(kind, seq), "title": _label(item), "previous": current.get("assignee", "")}], who)
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
        c.execute("UPDATE %s SET status=%%s, rev=rev+1 WHERE project_id=%%s AND id = ANY(%%s)" % table,
                  (body.status, pid, [r["id"] for r in changed]))
        for r in changed:
            history.record(c, pid, kind, r["id"], history.item_key(kind, r["seq"]), "status changed", who,
                           _label(r["data"]), {"status": {"from": r["status"], "to": body.status}})
    return {"updated": len(changed)}


def _bulk_assign(table: str, pid: str, body: AssignIn, who: str) -> Dict[str, int]:
    name = body.assignee.strip()
    if name:
        _check_assignee(name)
    kind = _kind(table)
    with db.conn() as c:
        rows = c.execute("SELECT id, seq, data FROM %s WHERE project_id=%%s AND id = ANY(%%s) FOR UPDATE" % table, (pid, body.ids)).fetchall()
        changed = [r for r in rows if (r["data"].get("assignee") or "") != name]
        for r in changed:
            c.execute("UPDATE %s SET data = jsonb_set(data, '{assignee}', to_jsonb(%%s::text)), rev = rev + 1 WHERE id=%%s" % table, (name, r["id"]))
            history.record(c, pid, kind, r["id"], history.item_key(kind, r["seq"]), "edited", who, _label(r["data"]),
                           {"assignee": {"from": r["data"].get("assignee") or "", "to": name}})
    if changed and name:
        integrations.announce_assignment(pid, kind, name, [{"key": history.item_key(kind, r["seq"]), "title": _label(r["data"]), "previous": r["data"].get("assignee") or ""} for r in changed], who)
    return {"updated": len(changed)}


_SETTABLE = {"requirements": ("epic", "section", "type"), "stories": ("epic", "feature", "priority")}  # what a bulk "set" may change: grouping and classification, never the text


def _bulk_set(table: str, pid: str, body: SetFieldIn, who: str) -> Dict[str, int]:
    if body.field not in _SETTABLE[table]:
        raise HTTPException(400, "Only %s can be set for many at once" % ", ".join(_SETTABLE[table]))
    value = body.value.strip()
    if body.field == "type" and value not in pipeline.REQ_TYPES:
        raise HTTPException(400, "Type must be one of: %s" % ", ".join(pipeline.REQ_TYPES))
    if body.field == "priority" and value not in pipeline.PRIORITIES:
        raise HTTPException(400, "Priority must be one of: %s" % ", ".join(pipeline.PRIORITIES))
    if not value:
        raise HTTPException(400, "A value is needed (an epic, a section or a feature cannot be blank)")
    value = value[:200]
    kind = _kind(table)
    with db.conn() as c:
        rows = c.execute("SELECT id, seq, data FROM %s WHERE project_id=%%s AND id = ANY(%%s) FOR UPDATE" % table, (pid, body.ids)).fetchall()
        changed = [r for r in rows if (r["data"].get(body.field) or "") != value]
        for r in changed:
            c.execute("UPDATE %s SET data = jsonb_set(data, ARRAY[%%s], to_jsonb(%%s::text)), rev = rev + 1 WHERE id=%%s" % table, (body.field, value, r["id"]))
            history.record(c, pid, kind, r["id"], history.item_key(kind, r["seq"]), "edited", who, _label(r["data"]),
                           {body.field: {"from": r["data"].get(body.field) or "", "to": value}})
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


@router.post("/api/projects/{pid}/requirements/{rid}/refine", dependencies=[Depends(ai_fair_use)])
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


MAX_IMPORT_ROWS = 2000
_IMPORT_COLUMNS = {  # header (lower case, spaces and underscores alike) -> field; the export's own headers import back
    "statement": "statement", "requirement": "statement", "text": "statement", "description": "statement",
    "type": "type", "epic": "epic", "section": "section", "feature": "section", "source id": "source_id", "sourceid": "source_id",
    "source ref": "source_ref", "actors": "actors", "actor": "actors", "product scope": "product_scope", "compliance tags": "compliance_tags",
    "compliance": "compliance_tags", "rationale": "rationale", "status": "status", "assignee": "assignee", "owner": "assignee"}
_LIST_FIELDS = ("actors", "product_scope", "compliance_tags")


@router.post("/api/projects/{pid}/requirements/import")
def import_requirements(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    """Add requirements from CSV text (a spreadsheet saved as CSV, or Fabulist's own Requirements export). See `_import_rows`."""
    _project(pid)
    return _import_rows(pid, _csv_rows(str(body.get("csv") or "")), history.actor(request), "CSV")


@router.post("/api/projects/{pid}/requirements/import-file")
def import_requirements_file(pid: str, request: Request, file: UploadFile = File(...)):
    """The same from an uploaded .csv or .xlsx file (the sheet called Requirements, else the first sheet). A plain `def`: reading a workbook is blocking work."""
    _project(pid)
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        with _document_slot():
            try:
                rows = parsing.xlsx_rows(data, "Requirements")
            except parsing.ParseError as exc:
                raise HTTPException(400, str(exc))
        return _import_rows(pid, rows, history.actor(request), "Excel")
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")  # a spreadsheet saved as "CSV" on Windows is often not UTF-8
        return _import_rows(pid, _csv_rows(text), history.actor(request), "CSV")
    raise HTTPException(400, "Choose a .csv or .xlsx file")


csv.field_size_limit(config.MAX_UPLOAD_BYTES)  # the default (128 KB per cell) turned a long cell into a server error; long statements are cut to their own limit later


def _csv_rows(text: str) -> List[Dict[str, Any]]:
    try:
        return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    except csv.Error as exc:
        raise HTTPException(400, "The file could not be read as CSV (%s)" % exc)


_STORY_COLUMNS = {
    "title": "title", "summary": "title", "story": "title",
    "narrative": "narrative", "description": "narrative", "user story": "narrative",
    "epic": "epic", "epic name": "epic", "epic link": "epic", "feature": "feature",
    "persona": "persona", "role": "persona", "priority": "priority",
    "story points": "story_points", "points": "story_points", "estimate": "story_points",
    "acceptance criteria": "acceptance_criteria", "acceptance": "acceptance_criteria", "criteria": "acceptance_criteria",
    "requirement ids": "requirement_ids", "requirements": "requirement_ids", "requirement": "requirement_ids",
    "labels": "labels", "status": "status", "assignee": "assignee", "owner": "assignee"}


@router.post("/api/projects/{pid}/stories/import")
def import_stories(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    """Add stories from CSV text (a Jira export, a spreadsheet, or Fabulist's own Stories export). Same rules as the requirement import."""
    _project(pid)
    return _import_story_rows(pid, _csv_rows(str(body.get("csv") or "")), history.actor(request), "CSV")


@router.post("/api/projects/{pid}/stories/import-file")
def import_stories_file(pid: str, request: Request, file: UploadFile = File(...)):
    """The same from an uploaded .csv or .xlsx file (the sheet called Stories, else the first)."""
    _project(pid)
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        with _document_slot():
            try:
                rows = parsing.xlsx_rows(data, "Stories")
            except parsing.ParseError as exc:
                raise HTTPException(400, str(exc))
        return _import_story_rows(pid, rows, history.actor(request), "Excel")
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return _import_story_rows(pid, _csv_rows(text), history.actor(request), "CSV")
    raise HTTPException(400, "Choose a .csv or .xlsx file")


def _import_story_rows(pid: str, rows: List[Dict[str, Any]], who: str, origin: str) -> Dict[str, int]:
    """Needs a title column; validated first, nothing imported if any row is wrong; a title already in the project is skipped, not doubled."""
    if not rows:
        raise HTTPException(400, "The file has no rows. The first row must name the columns, with at least one called Title or Summary.")
    columns = {h: _STORY_COLUMNS[re.sub(r"[\s_]+", " ", h.strip().lower())] for h in rows[0] if h and re.sub(r"[\s_]+", " ", h.strip().lower()) in _STORY_COLUMNS}
    if "title" not in columns.values():
        raise HTTPException(400, "No Title or Summary column found in the first row")
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(400, "At most %d rows per import; split the file" % MAX_IMPORT_ROWS)
    seen = {pipeline._norm_key(r["title"]) for r in db.rows("SELECT data->>'title' AS title FROM stories WHERE project_id=%s", (pid,))}
    fresh: List[Tuple[Dict[str, Any], str]] = []
    errors: List[str] = []
    skipped = unrecognised = 0
    for line, row in enumerate(rows, 2):
        raw: Dict[str, Any] = {}
        for header, field_name in columns.items():
            value = (row.get(header) or "").strip()
            if field_name == "acceptance_criteria":
                raw[field_name] = [v for v in value.split("\n") if v.strip()]
            elif field_name in ("requirement_ids", "labels"):
                raw[field_name] = [v for v in re.split(r"[;,\s]+", value) if v.strip()]
            elif value:
                raw[field_name] = value
        status = str(raw.pop("status", "draft")).strip().lower()
        if status not in STORY_STATUSES:
            if status:
                unrecognised += 1  # another tool's status ("To Do", "Done"): review states are decided here, so the story arrives as a draft
            status = "draft"
        item = pipeline.normalize_story(raw)
        if item is None:
            errors.append("line %d: the title is missing" % line)
        elif item["assignee"] and not _is_person(item["assignee"]):
            errors.append("line %d: %s is not a person with an account" % (line, item["assignee"]))
        elif pipeline._norm_key(item["title"]) in seen:
            skipped += 1
        else:
            seen.add(pipeline._norm_key(item["title"]))
            fresh.append((item, status))
    if errors:
        raise HTTPException(400, "Nothing was imported. " + "; ".join(errors[:10]) + ("; and %d more" % (len(errors) - 10) if len(errors) > 10 else ""))
    with db.conn() as c:
        seq = c.execute("SELECT coalesce(max(seq), -1) + 1 AS n FROM stories WHERE project_id=%s", (pid,)).fetchone()["n"]
        for item, status in fresh:
            new = db.new_id()
            c.execute("INSERT INTO stories (id, project_id, run_id, seq, status, data) VALUES (%s,%s,'manual',%s,%s,%s)", (new, pid, seq, status, json.dumps(item)))
            history.record(c, pid, "story", new, history.item_key("story", seq), "created", who, _label(item))
            seq += 1
        if fresh:
            history.record(c, pid, "project", pid, "", "imported", who, "%d stories from %s" % (len(fresh), origin))
    return {"created": len(fresh), "duplicates_skipped": skipped, "statuses_not_recognised": unrecognised}


def _import_rows(pid: str, rows: List[Dict[str, Any]], who: str, origin: str) -> Dict[str, int]:
    """Needs a statement column; everything is validated first and nothing is imported if any row is wrong. A statement that already exists
    in the project is skipped, not doubled."""
    if not rows:
        raise HTTPException(400, "The file has no rows. The first row must name the columns, with at least one called Requirement or Statement.")
    columns = {h: _IMPORT_COLUMNS[re.sub(r"[\s_]+", " ", h.strip().lower())] for h in rows[0] if h and re.sub(r"[\s_]+", " ", h.strip().lower()) in _IMPORT_COLUMNS}
    if "statement" not in columns.values():
        raise HTTPException(400, "No Requirement or Statement column found in the first row")
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(400, "At most %d rows per import; split the file" % MAX_IMPORT_ROWS)
    seen = {pipeline._norm_key(r["statement"]) for r in db.rows("SELECT data->>'statement' AS statement FROM requirements WHERE project_id=%s", (pid,))}
    fresh: List[Tuple[Dict[str, Any], str]] = []
    errors: List[str] = []
    skipped = 0
    for line, row in enumerate(rows, 2):
        raw: Dict[str, Any] = {}
        for header, field_name in columns.items():
            value = (row.get(header) or "").strip()
            if field_name in _LIST_FIELDS:
                raw[field_name] = [v for v in re.split(r"[;\n]", value) if v.strip()]
            elif value:
                raw[field_name] = value
        status = str(raw.pop("status", "draft")).lower()
        item = pipeline.normalize_requirement(raw, {"heading": "CSV"}, "Imported")
        if status not in STATUSES:
            errors.append("line %d: status must be draft, approved or excluded" % line)
        elif item is None:
            errors.append("line %d: the statement is missing or shorter than 8 characters" % line)
        elif item["assignee"] and not _is_person(item["assignee"]):
            errors.append("line %d: %s is not a person with an account" % (line, item["assignee"]))
        elif pipeline._norm_key(item["statement"]) in seen:
            skipped += 1
        else:
            seen.add(pipeline._norm_key(item["statement"]))
            fresh.append((item, status))
    if errors:
        raise HTTPException(400, "Nothing was imported. " + "; ".join(errors[:10]) + ("; and %d more" % (len(errors) - 10) if len(errors) > 10 else ""))
    with db.conn() as c:
        seq = c.execute("SELECT coalesce(max(seq), -1) + 1 AS n FROM requirements WHERE project_id=%s", (pid,)).fetchone()["n"]
        for item, status in fresh:
            new = db.new_id()
            c.execute("INSERT INTO requirements (id, project_id, seq, status, data) VALUES (%s,%s,%s,%s,%s)", (new, pid, seq, status, json.dumps(item)))
            history.record(c, pid, "requirement", new, history.item_key("requirement", seq), "created", who, _label(item))
            seq += 1
        if fresh:
            history.record(c, pid, "project", pid, "", "imported", who, "%d requirements from %s" % (len(fresh), origin))
    return {"created": len(fresh), "duplicates_skipped": skipped}


@router.post("/api/projects/{pid}/requirements/merge")
def merge_requirements(pid: str, body: MergeIn, request: Request):
    """Merge duplicates: `keep` gains everything the others say (where each was stated, actors, product scope, compliance tags) and a note; the others are
    excluded with a pointer to it (not deleted: the trail and old links keep making sense); stories that pointed at them point at `keep`."""
    _project(pid)
    who = history.actor(request)
    if body.keep in body.merge:
        raise HTTPException(400, "A requirement cannot be merged into itself")
    with db.conn() as c:
        rows = {r["id"]: r for r in c.execute("SELECT id, seq, status, data FROM requirements WHERE project_id=%s AND id = ANY(%s) FOR UPDATE",
                                              (pid, [body.keep] + body.merge)).fetchall()}
        if body.keep not in rows or any(m not in rows for m in body.merge):
            raise HTTPException(404, "Requirement not found")
        keep = rows[body.keep]
        if "Merged into " in (keep["data"].get("notes") or "")[:13]:
            raise HTTPException(400, "%s was itself merged into another requirement; keep that one instead" % history.item_key("requirement", keep["seq"]))
        data = dict(keep["data"])
        keys = {m: history.item_key("requirement", rows[m]["seq"]) for m in body.merge}
        kept_key = history.item_key("requirement", keep["seq"])
        for m in body.merge:
            other = rows[m]["data"]
            for field_name in ("actors", "product_scope", "compliance_tags"):
                data[field_name] = list(dict.fromkeys((data.get(field_name) or []) + (other.get(field_name) or [])))
            if other.get("source_ref") and other["source_ref"] not in (data.get("source_ref") or ""):
                data["source_ref"] = ("%s; %s" % (data.get("source_ref") or "", other["source_ref"])).strip("; ")[:300]
        data["notes"] = ((data.get("notes") or "") + ("\n" if data.get("notes") else "") + "Merged %s into this requirement." % ", ".join(keys.values()))[:1000]
        c.execute("UPDATE requirements SET data=%s, rev=rev+1 WHERE id=%s", (json.dumps(data), keep["id"]))
        history.record(c, pid, "requirement", keep["id"], kept_key, "merged", who, "absorbed " + ", ".join(keys.values()), {"merged": {"from": "", "to": ", ".join(keys.values())}})
        for m in body.merge:
            other = dict(rows[m]["data"])
            other["notes"] = ("Merged into %s.\n" % kept_key + (other.get("notes") or ""))[:1000]
            c.execute("UPDATE requirements SET data=%s, status='excluded', rev=rev+1 WHERE id=%s", (json.dumps(other), m))
            history.record(c, pid, "requirement", m, keys[m], "merged into", who, "into " + kept_key,
                           {"status": {"from": rows[m]["status"], "to": "excluded"}})
        relinked = 0
        for s in c.execute("SELECT id, data FROM stories WHERE project_id=%s", (pid,)).fetchall():
            ids_ = s["data"].get("requirement_ids") or []
            if any(k in ids_ for k in keys.values()):
                new = list(dict.fromkeys(kept_key if k in keys.values() else k for k in ids_))
                c.execute("UPDATE stories SET data = jsonb_set(data, '{requirement_ids}', %s::jsonb), rev = rev + 1 WHERE id=%s", (json.dumps(new), s["id"]))
                relinked += 1
    return {"kept": kept_key, "merged": list(keys.values()), "stories_relinked": relinked}


@router.post("/api/projects/{pid}/requirements/set")
def set_requirements(pid: str, body: SetFieldIn, request: Request):
    _project(pid)
    return _bulk_set("requirements", pid, body, history.actor(request))


@router.post("/api/projects/{pid}/requirements/{rid}/split")
def split_requirement(pid: str, rid: str, body: SplitIn, request: Request):
    """The opposite of merging: the first statement replaces the text of this requirement; each other becomes a new draft requirement carrying the same
    epic, section, type, actors, scope, tags and where-it-was-stated, with a note saying what it was split from."""
    _project(pid)
    who = history.actor(request)
    statements = [t.strip() for t in body.statements]
    if any(len(t) < 8 or len(t) > 1000 for t in statements):
        raise HTTPException(400, "Each part needs a statement of 8 to 1000 characters")
    row = db.one("SELECT seq, data FROM requirements WHERE id=%s AND project_id=%s", (rid, pid))
    if not row:
        raise HTTPException(404, "Requirement not found")
    key = history.item_key("requirement", row["seq"])
    shared = {k: row["data"].get(k) for k in ("type", "actors", "epic", "section", "source_ref", "product_scope", "compliance_tags") if row["data"].get(k)}
    _save_item("requirements", pid, rid, {"statement": statements[0]}, who)
    created = []
    for text in statements[1:]:
        new = _save_item("requirements", pid, None, dict(shared, statement=text, notes="Split from %s." % key), who)
        created.append(history.item_key("requirement", db.one("SELECT seq FROM requirements WHERE id=%s", (new,))["seq"]))
    return {"kept": key, "created": created}


@router.post("/api/projects/{pid}/requirements/tag")
def tag_requirements(pid: str, body: TagIn, request: Request):
    """Add a compliance tag to, or remove it from, many requirements at once."""
    _project(pid)
    tag = body.tag.strip()
    if not tag:
        raise HTTPException(400, "A tag is needed")
    who = history.actor(request)
    updated = 0
    with db.conn() as c:
        for r in c.execute("SELECT id, seq, data FROM requirements WHERE project_id=%s AND id = ANY(%s) FOR UPDATE", (pid, body.ids)).fetchall():
            old = r["data"].get("compliance_tags") or []
            new = [t for t in old if t != tag] if body.remove else list(dict.fromkeys(old + [tag]))
            if new == old:
                continue
            c.execute("UPDATE requirements SET data = jsonb_set(data, '{compliance_tags}', %s::jsonb), rev = rev + 1 WHERE id=%s", (json.dumps(new), r["id"]))
            history.record(c, pid, "requirement", r["id"], history.item_key("requirement", r["seq"]), "edited", who, _label(r["data"]),
                           {"compliance_tags": {"from": ", ".join(old), "to": ", ".join(new)}})
            updated += 1
    return {"updated": updated}


@router.post("/api/projects/{pid}/stories/set")
def set_stories(pid: str, body: SetFieldIn, request: Request):
    _project(pid)
    return _bulk_set("stories", pid, body, history.actor(request))


@router.post("/api/projects/{pid}/requirements/assign")
def assign_requirements(pid: str, body: AssignIn, request: Request):
    _project(pid)
    return _bulk_assign("requirements", pid, body, history.actor(request))


@router.post("/api/projects/{pid}/stories/assign")
def assign_stories(pid: str, body: AssignIn, request: Request):
    _project(pid)
    return _bulk_assign("stories", pid, body, history.actor(request))


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


@router.post("/api/projects/{pid}/stories/{sid}/refine", dependencies=[Depends(ai_fair_use)])
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


@router.post("/api/projects/{pid}/stories/estimate", dependencies=[Depends(ai_fair_use)])
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
            c.execute("UPDATE stories SET data=%s, rev=rev+1 WHERE id=%s", (json.dumps(data), r["id"]))
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
def project_history(pid: str, item_id: str = "", limit: int = 100, actor: str = "", action: str = "", before: int = 0):
    _project(pid)
    return history.entries(pid, item_id, max(1, min(limit, 500)), actor, action, before)


@router.get("/api/projects/{pid}/history/filters")
def history_filters(pid: str):
    _project(pid)
    return history.filter_values(pid)


@router.get("/api/projects/{pid}/history.csv")
def history_export(pid: str):
    project = _project(pid)
    slug = re.sub(r"[^a-z0-9]+", "-", project["name"].lower()).strip("-") or "project"
    payload, mime = exporters.history_csv(history.all_entries(pid))
    return Response(payload, media_type=mime, headers={"Content-Disposition": 'attachment; filename="%s-audit-trail.csv"' % slug})


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
            "coverage": round(100 * covered / len(rows)) if rows else 0, "products": _product_coverage(reqs, stories)}


def _product_coverage(reqs: List[Dict[str, Any]], stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per product class: requirements that name it, and whether their linked stories address it
    (story product scope or a product-variant criterion for that class)."""
    by_key = {}
    for s in stories:
        for k in s["requirement_ids"]:
            by_key.setdefault(k, []).append(s)
    classes: Dict[str, Dict[str, Any]] = {}
    for r in reqs:
        for cls in r.get("product_scope") or []:
            entry = classes.setdefault(cls, {"product_class": cls, "requirements": 0, "covered": 0, "gaps": []})
            entry["requirements"] += 1
            linked = by_key.get(r["key"], [])
            addressed = any(cls in (s.get("product_scope") or []) or any(v.get("scope") == cls for v in s.get("variant_criteria") or [])
                            for s in linked)
            if addressed:
                entry["covered"] += 1
            else:
                entry["gaps"].append({"key": r["key"], "statement": r["statement"][:160],
                                      "reason": "no linked story" if not linked else "linked stories do not address this product class"})
    return sorted(classes.values(), key=lambda c: (-len(c["gaps"]), c["product_class"]))


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
            deadline = time.monotonic() + config.PUSH_BUDGET_SECONDS
            result = integrations.push(pid, conn, target, stories, out_of_time=lambda: time.monotonic() >= deadline)
    except (integrations.TrackerError, settings.SettingsError) as exc:
        raise HTTPException(400, str(exc))
    result.setdefault("remaining", 0)
    for outcome in ("created", "updated"):
        metrics.inc("fabulist_tracker_items_total", result[outcome], tracker=conn["kind"], result=outcome)
    metrics.inc("fabulist_tracker_items_total", len(result["failed"]), tracker=conn["kind"], result="failed")
    if result["remaining"]:
        metrics.inc("fabulist_tracker_items_total", result["remaining"], tracker=conn["kind"], result="remaining")
    with db.conn() as c:
        history.record(c, pid, "push", conn["id"], target, "pushed to " + conn["kind"], history.actor(request),
                       "%s %s: %d created, %d updated, %d failed%s" % (conn["name"], target, result["created"], result["updated"], len(result["failed"]),
                                                           ", %d not pushed (time limit)" % result["remaining"] if result["remaining"] else ""))
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
        item.pop("rev", None)
    details = ["Generated %s UTC" % _utc_minutes(db.now()), "Scope: " + ("approved only" if scope == "approved" else "everything in review")]
    payload, mime = (exporters.html_report(stories, reqs, project["name"] + " backlog", details) if format == "html"
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


def _utc_minutes(iso: str) -> str:
    return iso[:16].replace("T", " ")  # db.now() is UTC ISO-8601: "2026-10-21T02:41:15.123+00:00"


def _snapshot(pid: str, scope: str) -> Dict[str, Any]:
    reqs = [r for r in _requirements(pid) if r["status"] == "approved" or (scope == "all" and r["status"] == "draft")]
    stories = [s for s in _stories(pid) if s["status"] == "approved" or (scope == "all" and s["status"] == "draft")]
    for item in reqs + stories:
        for k in ("issues", "external", "project_id", "rev"):
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
        # Same scope as the baseline: against an approved-only sign-off, drafts are not changes.
        target, label = _snapshot(pid, base["scope"]), "current backlog (%s)" % ("approved only" if base["scope"] == "approved" else "approved and draft")
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
    details = ["Baseline v%d (%s), frozen %s UTC by %s" % (b["version"], b["name"], _utc_minutes(b["created"]), b.get("actor") or "unknown"),
               "Scope: " + ("approved only" if b["scope"] == "approved" else "approved and draft"),
               "Exported %s UTC" % _utc_minutes(db.now())]
    payload, mime = (exporters.html_report(snap["stories"], snap["requirements"], title, details) if format == "html"
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
