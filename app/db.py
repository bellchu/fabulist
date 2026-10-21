"""Postgres persistence (psycopg 3) with pgvector for retrieval."""
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

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
    """CREATE TABLE IF NOT EXISTS requirements (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         seq INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', data JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS providers (
         id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, base_url TEXT NOT NULL DEFAULT '',
         model TEXT NOT NULL DEFAULT '', api_key_enc TEXT, key_hint TEXT NOT NULL DEFAULT '',
         is_default BOOLEAN NOT NULL DEFAULT FALSE, created TEXT NOT NULL)""",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'stories'",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS tokens_in INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS tokens_out INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS duration_ms INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS item_count INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS history (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         item_type TEXT NOT NULL, item_id TEXT NOT NULL, item_key TEXT NOT NULL DEFAULT '',
         action TEXT NOT NULL, actor TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
         changes JSONB NOT NULL DEFAULT '{}', created TEXT NOT NULL)""",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived TEXT",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS updated TEXT",
    "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS rev INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE stories ADD COLUMN IF NOT EXISTS rev INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS actor TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE",
    # Monotonic order for entries written within the same second.
    "ALTER TABLE history ADD COLUMN IF NOT EXISTS n BIGSERIAL",
    """CREATE TABLE IF NOT EXISTS repositories (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         url TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', path_glob TEXT NOT NULL DEFAULT '*',
         token_enc TEXT, key_hint TEXT NOT NULL DEFAULT '', last_sync TEXT NOT NULL DEFAULT '',
         last_commit TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS comments (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         item_type TEXT NOT NULL, item_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
         resolved BOOLEAN NOT NULL DEFAULT FALSE, resolved_by TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_comments_item ON comments(item_id, created)",
    """CREATE TABLE IF NOT EXISTS baselines (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         version INTEGER NOT NULL, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL,
         actor TEXT NOT NULL DEFAULT '', snapshot JSONB NOT NULL, created TEXT NOT NULL,
         UNIQUE (project_id, version))""",
    """CREATE TABLE IF NOT EXISTS product_classes (
         id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, attributes JSONB NOT NULL DEFAULT '[]',
         keywords JSONB NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS attribute_rules (attribute TEXT PRIMARY KEY, criteria TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS users (
         id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
         role TEXT NOT NULL, disabled BOOLEAN NOT NULL DEFAULT FALSE, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS api_tokens (
         id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         name TEXT NOT NULL, prefix TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
         created TEXT NOT NULL, expires TEXT NOT NULL, last_used TEXT)""",
    """CREATE TABLE IF NOT EXISTS integrations (
         id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL,
         username TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '', token_enc TEXT,
         key_hint TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS external_links (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
         integration_id TEXT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE, target TEXT NOT NULL,
         item_type TEXT NOT NULL, item_ref TEXT NOT NULL, external_key TEXT NOT NULL, external_url TEXT NOT NULL,
         updated TEXT NOT NULL, UNIQUE (integration_id, target, item_type, item_ref))""",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS repo_id TEXT",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_history_project ON history(project_id, created)",
    "CREATE INDEX IF NOT EXISTS idx_history_item ON history(item_id, created)",
    "CREATE INDEX IF NOT EXISTS idx_reqs_project ON requirements(project_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created)",
    "CREATE INDEX IF NOT EXISTS idx_docs_project ON documents(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_stories_project ON stories(project_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id)",
    """CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
         USING hnsw (embedding vector_cosine_ops)""",
]


def now() -> str:
    # Millisecond precision keeps "latest run" ordering correct for runs started in the same second.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


_pools: Dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def _pool() -> ConnectionPool:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    with _pools_lock:
        pool = _pools.get(config.DATABASE_URL)
        if pool is None:
            # check: a connection that died with a restarted/failed-over database is replaced when it is handed out,
            # instead of failing the request that happened to get it. Keepalives and tcp_user_timeout make a
            # silently partitioned connection fail in ~20 s rather than hang until the OS gives up.
            pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL_MAX", "10")),
                                  kwargs={"row_factory": dict_row, "connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10,
                                          "keepalives_interval": 5, "keepalives_count": 3, "tcp_user_timeout": 20000},
                                  timeout=float(os.environ.get("DB_POOL_TIMEOUT", "10")), check=ConnectionPool.check_connection, open=True)
            _pools[config.DATABASE_URL] = pool
    return pool


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    """Pooled connection; commits on success, rolls back on error."""
    with _pool().connection() as c:
        yield c


def close() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


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


def item_row(r: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """Flatten a requirements/stories row: JSONB payload plus identity columns."""
    data = dict(r["data"] if isinstance(r["data"], dict) else json.loads(r["data"]))
    data.update({"id": r["id"], "project_id": r["project_id"], "status": r["status"], "seq": r["seq"],
                 "key": "%s-%03d" % (prefix, r["seq"] + 1), "rev": r.get("rev", 1)})
    return data
