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


def entries(project_id: str, item_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    if item_id:
        return db.rows("SELECT * FROM history WHERE project_id=%s AND item_id=%s ORDER BY n DESC LIMIT %s",
                       (project_id, item_id, limit))
    return db.rows("SELECT * FROM history WHERE project_id=%s ORDER BY n DESC LIMIT %s", (project_id, limit))
