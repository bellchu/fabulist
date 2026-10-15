"""Postgres persistence (psycopg 3) with pgvector for retrieval."""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

from . import config

SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS projects (
         id TEXT PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS documents (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         filename TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runs (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
         error TEXT NOT NULL DEFAULT '', story_count INTEGER NOT NULL DEFAULT 0,
         progress TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS stories (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         run_id TEXT NOT NULL, seq INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
         data JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chunks (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
         seq INTEGER NOT NULL, text TEXT NOT NULL, embedding vector(%d) NOT NULL)""" % config.EMBED_DIM,
    "CREATE INDEX IF NOT EXISTS idx_docs_project ON documents(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_stories_project ON stories(project_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id)",
    """CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
         USING hnsw (embedding vector_cosine_ops)""",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row, connect_timeout=10) as c:
        yield c


def init() -> None:
    with conn() as c:
        # Serialize concurrent startups (multiple replicas) on schema creation.
        c.execute("SELECT pg_advisory_xact_lock(727274)")
        for stmt in SCHEMA:
            c.execute(stmt)


def rows(sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    with conn() as c:
        return list(c.execute(sql, args).fetchall())


def one(sql: str, args: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql: str, args: Sequence[Any] = ()) -> None:
    with conn() as c:
        c.execute(sql, args)


def vec(v: Sequence[float]) -> str:
    return "[" + ",".join("%.6f" % x for x in v) + "]"


def story_row(r: Dict[str, Any]) -> Dict[str, Any]:
    data = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
    data = dict(data)
    data.update({"id": r["id"], "project_id": r["project_id"], "status": r["status"], "seq": r["seq"]})
    return data
