"""Admin API: AI provider profiles, generation settings, usage overview."""
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Body

from . import config, db, embeddings, llm, settings

router = APIRouter(prefix="/api/admin")


@router.get("/overview")
def overview() -> Dict[str, Any]:
    totals = db.one("""SELECT
        (SELECT count(*) FROM projects) AS projects,
        (SELECT count(*) FROM documents) AS documents,
        (SELECT count(*) FROM requirements) AS requirements,
        (SELECT count(*) FROM stories) AS stories,
        (SELECT count(*) FROM chunks) AS chunks""")
    window = db.one("""SELECT count(*) AS runs,
        count(*) FILTER (WHERE status = 'failed') AS failed,
        coalesce(sum(tokens_in), 0) AS tokens_in, coalesce(sum(tokens_out), 0) AS tokens_out,
        coalesce(avg(duration_ms) FILTER (WHERE status = 'done'), 0)::int AS avg_ms
        FROM runs WHERE created >= to_char(now() - interval '30 days', 'YYYY-MM-DD')""")
    by_provider = db.rows("""SELECT provider, model, count(*) AS runs, coalesce(sum(tokens_in + tokens_out), 0) AS tokens
        FROM runs WHERE created >= to_char(now() - interval '30 days', 'YYYY-MM-DD')
        GROUP BY provider, model ORDER BY runs DESC LIMIT 10""")
    return {"totals": totals, "last_30_days": window, "by_provider": by_provider,
            "default_provider": next((p for p in settings.list_providers() if p["is_default"]), None),
            "encryption_configured": bool(config.SETTINGS_KEY), "embed_dim": config.EMBED_DIM}


@router.get("/runs")
def runs(limit: int = 50):
    return db.rows("""SELECT r.*, p.name AS project_name FROM runs r LEFT JOIN projects p ON p.id = r.project_id
                      ORDER BY r.created DESC, r.id LIMIT %s""", (min(limit, 200),))


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return {"values": settings.get_settings(), "defaults": settings.DEFAULTS, "limits": settings.NUMERIC_LIMITS,
            "embed_dim": config.EMBED_DIM}


@router.put("/settings")
def put_settings(body: Dict[str, Any] = Body(...)):
    before = settings.get_settings()
    values = settings.update_settings(body)
    embed_changed = (before["embed_provider_id"], before["embed_model"]) != (values["embed_provider_id"], values["embed_model"])
    return {"values": values, "reindex_recommended": embed_changed}


@router.post("/reindex", status_code=202)
def reindex(tasks: BackgroundTasks):
    tasks.add_task(embeddings.reindex_all)
    return {"status": "started"}


@router.get("/providers")
def providers() -> Dict[str, Any]:
    return {"providers": settings.list_providers(), "kinds": settings.KINDS, "presets": settings.PRESETS,
            "encryption_configured": bool(config.SETTINGS_KEY)}


@router.post("/providers", status_code=201)
def create_provider(body: Dict[str, Any] = Body(...)):
    return settings.save_provider(body)


@router.put("/providers/{pid}")
def update_provider(pid: str, body: Dict[str, Any] = Body(...)):
    return settings.save_provider(body, pid)


@router.delete("/providers/{pid}", status_code=204)
def delete_provider(pid: str):
    settings.delete_provider(pid)


@router.post("/providers/{pid}/default")
def make_default(pid: str):
    settings.set_default(pid)
    return {"ok": True}


@router.post("/providers/{pid}/test")
def test_provider(pid: str):
    return llm.test_connection(settings.get_provider(pid), settings.get_settings())
