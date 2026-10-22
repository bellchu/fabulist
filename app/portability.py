"""Project archive: everything a team needs to leave or move a project, and the import that restores it.

Not included, because they belong to this installation: the AI run log, repository connections and tracker links
(they hold credentials and ids that mean nothing elsewhere).
"""
import json
import logging
from typing import Any, Dict, Iterator, List, Tuple

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import db, embeddings, history, parsing, pipeline
from .profiles import PLATFORMS

log = logging.getLogger("fabulist.portability")
router = APIRouter()
FORMAT, VERSION = "fabulist-project", 1
MAX_ITEMS = 20_000  # per collection (history may hold 10x)
HISTORY_TYPES = {"requirement", "story", "project", "repository", "push", "baseline"}
REQ_STATUSES, STORY_STATUSES = {"draft", "approved", "excluded"}, {"draft", "approved", "rejected"}


class ArchiveError(ValueError):
    pass


# ---- export ---------------------------------------------------------------

def _documents(pid: str) -> Iterator[Dict[str, Any]]:
    """The project's documents in upload order, read a few at a time: a project with a thousand BRDs does not fit in memory as one list."""
    ids = [r["id"] for r in db.rows("SELECT id FROM documents WHERE project_id=%s ORDER BY created, id", (pid,))]
    for i in range(0, len(ids), 20):
        batch = {r["id"]: r for r in db.rows("SELECT id, filename, text, created, source, path FROM documents WHERE id = ANY(%s)", (ids[i:i + 20],))}
        for did in ids[i:i + 20]:
            if did in batch:
                yield {k: batch[did][k] for k in ("filename", "text", "created", "source", "path")}


def stream(pid: str) -> Tuple[str, Iterator[str]]:
    """(project name, the archive as JSON text in pieces). Exporting a 167 MB project used to peak at 5x that in memory (dict, string and
    response body at once); streamed, only a few documents are held at a time."""
    project = db.one("SELECT name, platform, description FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Project not found")
    dump = lambda value: json.dumps(value, default=str)  # noqa: E731
    items = lambda table: [{"id": r["id"], "seq": r["seq"], "status": r["status"], "data": r["data"]}  # noqa: E731
                           for r in db.rows("SELECT id, seq, status, data FROM %s WHERE project_id=%%s ORDER BY seq" % table, (pid,))]
    rest = (
        ("requirements", lambda: items("requirements")), ("stories", lambda: items("stories")),
        ("baselines", lambda: db.rows("SELECT version, name, note, scope, actor, snapshot, created FROM baselines WHERE project_id=%s ORDER BY version", (pid,))),
        ("comments", lambda: db.rows("SELECT item_type, item_id, author, body, resolved, resolved_by, created FROM comments WHERE project_id=%s ORDER BY created, id", (pid,))),
        ("history", lambda: db.rows("SELECT item_type, item_id, item_key, action, actor, summary, changes, created FROM history WHERE project_id=%s ORDER BY n", (pid,))),
    )

    def pieces() -> Iterator[str]:
        yield '{"format": %s, "version": %d, "exported_at": %s, "project": %s, "documents": [' % (dump(FORMAT), VERSION, dump(db.now()), dump(dict(project)))
        for n, doc in enumerate(_documents(pid)):
            yield ("" if n == 0 else ", ") + dump(doc)
        yield "]"
        for key, load in rest:
            yield ', "%s": %s' % (key, dump(load()))
        yield "}"

    return project["name"], pieces()


@router.get("/api/projects/{pid}/archive.json")
def export_archive(pid: str):
    name, pieces = stream(pid)
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "project"
    return StreamingResponse(pieces, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="%s-archive.json"' % slug})


# ---- import ---------------------------------------------------------------

def _text(value: Any, what: str, limit: int, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ArchiveError("%s must be %stext" % (what, "non-empty " if required else ""))
    return value.strip()[:limit]


def _list(doc: Dict[str, Any], key: str, limit: int = 0) -> List[Any]:
    limit = limit or MAX_ITEMS  # read at call time, so the limit can be tuned
    value = doc.get(key, [])
    if not isinstance(value, list):
        raise ArchiveError("%s must be a list" % key)
    if len(value) > limit:
        raise ArchiveError("The archive has too many %s (limit %d)" % (key, limit))
    return value


def _items(raw: List[Any], kind: str, statuses: set) -> List[Tuple[int, str, Dict[str, Any]]]:
    out, seen = [], set()
    for i, it in enumerate(raw, 1):
        if not isinstance(it, dict) or not isinstance(it.get("data"), dict) or not isinstance(it.get("seq"), int) or it["seq"] < 0:
            raise ArchiveError("%s %d is malformed" % (kind, i))
        if it["seq"] in seen:
            raise ArchiveError("duplicate %s sequence number %d" % (kind, it["seq"]))
        seen.add(it["seq"])
        data = it["data"]
        if kind == "requirement":
            norm = pipeline.normalize_requirement(data, {"epic": str(data.get("epic") or ""), "heading": str(data.get("section") or "")},
                                                  str(data.get("source_ref") or "Imported"))
        else:
            if "requirement_ids" in data and not isinstance(data["requirement_ids"], list):
                raise ArchiveError("story %d: requirement_ids must be a list" % i)
            norm = pipeline.normalize_story(data)
        if norm is None:
            raise ArchiveError("%s %d has no usable %s" % (kind, i, "statement" if kind == "requirement" else "title"))
        status = it.get("status") if it.get("status") in statuses else "draft"
        out.append((it["seq"], status, norm))
    return out


def validate(doc: Any) -> Dict[str, Any]:
    """Check the whole archive and return what to insert. Nothing is written before this passes."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ArchiveError("This is not a Fabulist project archive")
    if doc.get("version") != VERSION:
        raise ArchiveError("Archive version %s is not supported (this server reads version %d)" % (doc.get("version"), VERSION))
    project = doc.get("project")
    if not isinstance(project, dict):
        raise ArchiveError("The archive has no project section")
    out: Dict[str, Any] = {
        "name": _text(project.get("name"), "project name", 120, True),
        "platform": project.get("platform") if project.get("platform") in PLATFORMS else "generic",
        "description": _text(project.get("description", ""), "project description", 2000),
        "documents": [], "baselines": [], "comments": [], "history": [],
    }
    for i, d in enumerate(_list(doc, "documents", 500), 1):
        if not isinstance(d, dict):
            raise ArchiveError("document %d is malformed" % i)
        out["documents"].append({"filename": _text(d.get("filename"), "document %d filename" % i, 200, True),
                                 "text": parsing.clip(_text(d.get("text"), "document %d text" % i, 10 ** 9, True)),
                                 "created": str(d.get("created") or db.now())[:40], "path": str(d.get("path") or "")[:300]})
    out["requirements"] = _items(_list(doc, "requirements"), "requirement", REQ_STATUSES)
    out["stories"] = _items(_list(doc, "stories"), "story", STORY_STATUSES)
    versions = set()
    for i, b in enumerate(_list(doc, "baselines", 500), 1):
        snap = b.get("snapshot") if isinstance(b, dict) else None
        if not (isinstance(b, dict) and isinstance(b.get("version"), int) and b["version"] >= 1 and isinstance(snap, dict)
                and isinstance(snap.get("requirements"), list) and isinstance(snap.get("stories"), list) and b.get("scope") in ("approved", "all")):
            raise ArchiveError("baseline %d is malformed" % i)
        if b["version"] in versions:
            raise ArchiveError("duplicate baseline version %d" % b["version"])
        versions.add(b["version"])
        out["baselines"].append({"version": b["version"], "name": _text(b.get("name"), "baseline %d name" % i, 120, True), "note": str(b.get("note") or "")[:2000],
                                 "scope": b["scope"], "actor": str(b.get("actor") or "")[:100], "snapshot": snap, "created": str(b.get("created") or db.now())[:40]})
    for i, c in enumerate(_list(doc, "comments"), 1):
        if not (isinstance(c, dict) and c.get("item_type") in ("requirement", "story")):
            raise ArchiveError("comment %d is malformed" % i)
        out["comments"].append({"item_type": c["item_type"], "item_id": str(c.get("item_id") or "")[:40], "author": str(c.get("author") or "unknown")[:100],
                                "body": _text(c.get("body"), "comment %d body" % i, 4000, True), "resolved": bool(c.get("resolved")),
                                "resolved_by": str(c.get("resolved_by") or "")[:100], "created": str(c.get("created") or db.now())[:40]})
    for i, h in enumerate(_list(doc, "history", MAX_ITEMS * 10), 1):
        if not (isinstance(h, dict) and h.get("item_type") in HISTORY_TYPES and isinstance(h.get("changes", {}), dict)):
            raise ArchiveError("history entry %d is malformed or has an unknown item_type" % i)
        out["history"].append({"item_type": h["item_type"], "item_id": str(h.get("item_id") or "")[:40], "item_key": str(h.get("item_key") or "")[:40],
                               "action": _text(h.get("action"), "history %d action" % i, 100, True), "actor": str(h.get("actor") or "unknown")[:100],
                               "summary": str(h.get("summary") or "")[:300], "changes": h.get("changes") or {}, "created": str(h.get("created") or db.now())[:40]})
    return out


def _unique_name(name: str) -> str:
    taken = {r["name"] for r in db.rows("SELECT name FROM projects")}
    candidate, n = name, 1
    while candidate in taken:
        n += 1
        candidate = "%s (imported)" % name if n == 2 else "%s (imported %d)" % (name, n - 1)
    return candidate


def restore(doc: Any, who: str, exported_at: str = "") -> Dict[str, Any]:
    data = validate(doc)
    pid, name = db.new_id(), _unique_name(data["name"])
    idmap: Dict[str, str] = {}
    old_pid = str(doc.get("project", {}).get("id") or "")
    indexing: List[Tuple[str, str]] = []
    src = {"requirement": {i["id"]: i["seq"] for i in doc["requirements"] if isinstance(i, dict) and "id" in i},
           "story": {i["id"]: i["seq"] for i in doc["stories"] if isinstance(i, dict) and "id" in i}}
    with db.conn() as c:
        c.execute("INSERT INTO projects (id, name, platform, description, created) VALUES (%s,%s,%s,%s,%s)",
                  (pid, name, data["platform"], data["description"], db.now()))
        for d in data["documents"]:
            did = db.new_id()
            c.execute("INSERT INTO documents (id, project_id, filename, text, created, source, path) VALUES (%s,%s,%s,%s,%s,'upload',%s)",
                      (did, pid, d["filename"], d["text"], d["created"], d["path"]))
            indexing.append((did, d["text"]))
        new_by_seq: Dict[Tuple[str, int], str] = {}
        for seq, status, item in data["requirements"]:
            iid = db.new_id()
            new_by_seq[("requirement", seq)] = iid
            c.execute("INSERT INTO requirements (id, project_id, seq, status, data) VALUES (%s,%s,%s,%s,%s)", (iid, pid, seq, status, json.dumps(item)))
        for seq, status, item in data["stories"]:
            iid = db.new_id()
            new_by_seq[("story", seq)] = iid
            c.execute("INSERT INTO stories (id, project_id, run_id, seq, status, data) VALUES (%s,%s,'imported',%s,%s,%s)", (iid, pid, seq, status, json.dumps(item)))
        for kind, olds in src.items():
            for old_id, seq in olds.items():
                if (kind, seq) in new_by_seq:
                    idmap[old_id] = new_by_seq[(kind, seq)]
        if old_pid:
            idmap[old_pid] = pid
        for b in data["baselines"]:
            c.execute("INSERT INTO baselines (id, project_id, version, name, note, scope, actor, snapshot, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (db.new_id(), pid, b["version"], b["name"], b["note"], b["scope"], b["actor"], json.dumps(b["snapshot"]), b["created"]))
        for m in data["comments"]:
            c.execute("INSERT INTO comments (id, project_id, item_type, item_id, author, body, resolved, resolved_by, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (db.new_id(), pid, m["item_type"], idmap.get(m["item_id"], m["item_id"]), m["author"], m["body"], m["resolved"], m["resolved_by"], m["created"]))
        for h in data["history"]:
            c.execute("INSERT INTO history (id, project_id, item_type, item_id, item_key, action, actor, summary, changes, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (db.new_id(), pid, h["item_type"], idmap.get(h["item_id"], h["item_id"]), h["item_key"], h["action"], h["actor"], h["summary"],
                       json.dumps(h["changes"]), h["created"]))
        history.record(c, pid, "project", pid, "", "imported", who,
                       ("Imported from an archive of '%s' exported %s. Earlier entries come from the archive and are not verified by this installation."
                        % (data["name"], (exported_at or "at an unknown time")[:16].replace("T", " ")))[:300])
    for did, text in indexing:  # search degrades without it; the documents themselves are already saved
        try:
            embeddings.index_document(pid, did, text)
        except Exception:
            log.exception("indexing failed for imported document %s", did)
    return db.one("SELECT id, name, platform, description, created FROM projects WHERE id=%s", (pid,))


@router.post("/api/projects/import", status_code=201)
def import_archive(request: Request, body: Dict[str, Any] = Body(...)):
    try:
        return restore(body, history.actor(request), str(body.get("exported_at") or ""))
    except ArchiveError as exc:
        raise HTTPException(400, str(exc))
