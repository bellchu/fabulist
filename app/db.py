"""Postgres persistence (psycopg 3) with pgvector for retrieval."""
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence

import psycopg
from psycopg.conninfo import conninfo_to_dict
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
    """CREATE TABLE IF NOT EXISTS admin_audit (
         n BIGSERIAL, id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
         summary TEXT NOT NULL DEFAULT '', changes JSONB NOT NULL DEFAULT '{}', created TEXT NOT NULL)""",
    # The audit tables are append-only in the database too (defence in depth against a bug or an injected statement; the table owner can still drop
    # a trigger). A project's own history may still go with the project (ON DELETE CASCADE), so history refuses only UPDATE.
    """CREATE OR REPLACE FUNCTION fabulist_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
         BEGIN RAISE EXCEPTION 'the % table is append-only', TG_TABLE_NAME; END $$""",
    "DROP TRIGGER IF EXISTS history_append_only ON history",
    "CREATE TRIGGER history_append_only BEFORE UPDATE ON history FOR EACH ROW EXECUTE FUNCTION fabulist_append_only()",
    "DROP TRIGGER IF EXISTS admin_audit_append_only ON admin_audit",
    "CREATE TRIGGER admin_audit_append_only BEFORE UPDATE OR DELETE ON admin_audit FOR EACH ROW EXECUTE FUNCTION fabulist_append_only()",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived TEXT",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'everyone'",  # 'everyone', or 'members' (see project_members)
    """CREATE TABLE IF NOT EXISTS project_members (
         project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, username TEXT NOT NULL,
         added_by TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY (project_id, username))""",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS updated TEXT",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS requests INTEGER NOT NULL DEFAULT 0",
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
    # Each server instance beats while it lives; a running run names the instance that executes it, so a server starting (a rolling deploy) does not
    # fail the runs of one that is still alive, and the runs of one that stopped are found by whoever notices first.
    """CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, started TEXT NOT NULL, seen TEXT NOT NULL)""",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change BOOLEAN NOT NULL DEFAULT FALSE",  # set when an administrator chose the password
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TEXT",  # when the account last used the API (about once per quarter hour): access reviews
    "ALTER TABLE integrations ADD COLUMN IF NOT EXISTS events TEXT NOT NULL DEFAULT ''",  # 'runs': a webhook that also hears when an AI run finishes
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS repo_id TEXT",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS provider_id TEXT",  # how a run is started, so a waiting run can be resumed after a restart
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS run_scope TEXT",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS started TEXT",  # when a worker began executing it (created is when it was queued)
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS run_documents TEXT",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS claimed BOOLEAN NOT NULL DEFAULT FALSE",  # set by the one server that executes it
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted TEXT",  # when an extraction run read it; NULL = not yet read
    "CREATE INDEX IF NOT EXISTS idx_history_project ON history(project_id, created)",
    "CREATE INDEX IF NOT EXISTS idx_history_item ON history(item_id, created)",
    "CREATE INDEX IF NOT EXISTS idx_reqs_project ON requirements(project_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created)",
    "CREATE INDEX IF NOT EXISTS idx_docs_project ON documents(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_stories_project ON stories(project_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)",  # per-document counts and the ON DELETE CASCADE from documents
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
            pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL_MAX", "20")),
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


_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@contextmanager
def _schema_owner() -> Iterator[psycopg.Connection]:
    """The migration login when there is one (one short-lived connection), else the ordinary pool."""
    if not config.MIGRATION_DATABASE_URL:
        with conn() as c:
            yield c
        return
    with psycopg.connect(config.MIGRATION_DATABASE_URL, row_factory=dict_row, connect_timeout=10) as c:
        yield c


def _grant_runtime_role(c: psycopg.Connection) -> None:
    """Everything the app does with data, and nothing more. History and the administrative trail can be added to and read but not changed:
    a project's history still goes with the project, because the cascade runs with the owner's rights."""
    role = conninfo_to_dict(config.DATABASE_URL).get("user", "")
    if not _ROLE.match(role) or role == conninfo_to_dict(config.MIGRATION_DATABASE_URL).get("user"):
        return  # same login for both (or an odd name): nothing to separate
    for stmt in ('GRANT USAGE ON SCHEMA public TO "%s"' % role,
                 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "%s"' % role,
                 'REVOKE UPDATE, DELETE, TRUNCATE ON history, admin_audit FROM "%s"' % role,
                 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "%s"' % role):
        c.execute(stmt)


def init() -> None:
    with _schema_owner() as c:
        # Serialize concurrent startups (multiple replicas) on schema creation.
        c.execute("SELECT pg_advisory_xact_lock(727274)")
        had_table = c.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'documents'").fetchone()
        had_flag = c.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'documents' AND column_name = 'extracted'").fetchone()
        for stmt in SCHEMA:
            c.execute(stmt)
        if had_table and not had_flag:
            # Upgrading: documents that already sit behind requirements have been read; those of projects with none are new.
            c.execute("UPDATE documents SET extracted = created WHERE project_id IN (SELECT DISTINCT project_id FROM requirements)")
        if config.MIGRATION_DATABASE_URL:
            _grant_runtime_role(c)


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
