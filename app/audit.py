"""Append-only record of administrative changes (who changed which setting, account, provider or integration).

The project history covers what happens to requirements and stories; this covers the configuration around them,
which is what an auditor asks about next. Secrets and passwords are never written here, only that they changed.
"""
import json
from typing import Any, Dict, List

from fastapi import Request

from . import db
from .history import actor

MAX_ROWS = 5000


def record(request: Request, action: str, target: str, summary: str = "", changes: Dict[str, Any] = None) -> None:
    db.execute("INSERT INTO admin_audit (id, actor, action, target, summary, changes, created) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               (db.new_id(), actor(request), action, target[:200], summary[:300], json.dumps(changes or {}), db.now()))


def entries(limit: int = 100, before: int = 0, who: str = "", action: str = "") -> List[Dict[str, Any]]:
    """Newest first; `before` is the `n` of the last row of the previous page."""
    where, args = ["TRUE"], []  # type: List[str], List[Any]
    if before:
        where.append("n < %s"); args.append(before)
    if who:
        where.append("actor = %s"); args.append(who)
    if action:
        where.append("action = %s"); args.append(action)
    return db.rows("SELECT n, id, actor, action, target, summary, changes, created FROM admin_audit WHERE " + " AND ".join(where) +
                   " ORDER BY n DESC LIMIT %s", (*args, min(max(limit, 1), 500)))


def all_entries() -> List[Dict[str, Any]]:
    return db.rows("SELECT n, id, actor, action, target, summary, changes, created FROM admin_audit ORDER BY n DESC LIMIT %s", (MAX_ROWS,))
