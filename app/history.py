"""Append-only change history (who changed what, when) for requirements and stories.

Supports audit expectations common in regulated delivery (e.g. 21 CFR Part 11, SOX):
entries are only ever inserted, never updated or deleted through the API.
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import Request

from . import db

PREFIX = {"requirement": "REQ", "story": "US"}


def actor(request: Request) -> str:
    """Authenticated username set by the auth middleware."""
    return getattr(request.state, "user", "") or "anonymous"


def item_key(item_type: str, seq: int) -> str:
    return "%s-%03d" % (PREFIX[item_type], seq + 1)


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {k: {"from": old.get(k), "to": new.get(k)}
            for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)}


def record(c: Any, project_id: str, item_type: str, item_id: str, key: str, action: str, who: str,
           summary: str = "", changes: Optional[Dict[str, Any]] = None) -> None:
    """Insert one entry using an open connection (so it commits with the change itself)."""
    c.execute("INSERT INTO history (id, project_id, item_type, item_id, item_key, action, actor, summary, changes, created) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (db.new_id(), project_id, item_type, item_id, key, action, who, summary[:300], json.dumps(changes or {}), db.now()))


def entries(project_id: str, item_id: str = "", limit: int = 100, actor: str = "", action: str = "", before: int = 0) -> List[Dict[str, Any]]:
    """Newest first. `before` is the `n` of the last row of the previous page (cursor paging)."""
    where, args = ["project_id=%s"], [project_id]
    for clause, value in (("item_id=%s", item_id), ("actor=%s", actor), ("action=%s", action)):
        if value:
            where.append(clause)
            args.append(value)
    if before > 0:
        where.append("n<%s")
        args.append(before)
    return db.rows("SELECT * FROM history WHERE %s ORDER BY n DESC LIMIT %%s" % " AND ".join(where), tuple(args) + (limit,))


def filter_values(project_id: str) -> Dict[str, List[str]]:
    return {"actors": [r["v"] for r in db.rows("SELECT DISTINCT actor AS v FROM history WHERE project_id=%s ORDER BY v", (project_id,))],
            "actions": [r["v"] for r in db.rows("SELECT DISTINCT action AS v FROM history WHERE project_id=%s ORDER BY v", (project_id,))]}


def all_entries(project_id: str) -> List[Dict[str, Any]]:
    """The complete trail, oldest first (for audit evidence exports)."""
    return db.rows("SELECT * FROM history WHERE project_id=%s ORDER BY n", (project_id,))
