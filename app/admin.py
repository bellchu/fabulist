"""Admin API: AI provider profiles, generation settings, usage overview."""
import csv
import io
import json
import os
import re
import time
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request, Response

from . import audit, config, db, embeddings, exporters, llm, metrics, pipeline, settings, taxonomy
from .fairuse import ai_fair_use

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
    busy = db.one("SELECT count(*) FILTER (WHERE status = 'running') AS running, count(*) FILTER (WHERE status = 'queued') AS queued, "
                  "pg_database_size(current_database()) AS size FROM runs")
    rss, limit = metrics.memory_now()
    system = {"version": config.VERSION, "uptime_seconds": int(time.time() - metrics.STARTED_AT), "database_bytes": busy["size"],
              "runs_running": busy["running"], "runs_queued": busy["queued"], "max_concurrent_runs": config.MAX_CONCURRENT_RUNS,
              "max_queued_runs": config.MAX_QUEUED_RUNS, "memory_bytes": rss, "memory_limit_bytes": limit}
    return {"totals": totals, "last_30_days": window, "by_provider": by_provider, "system": system,
            "default_provider": next((p for p in settings.list_providers() if p["is_default"]), None),
            "encryption_configured": bool(config.SETTINGS_KEY), "embed_dim": config.EMBED_DIM}


@router.get("/runs")
def runs(limit: int = 50):
    return db.rows("""SELECT r.*, p.name AS project_name FROM runs r LEFT JOIN projects p ON p.id = r.project_id
                      ORDER BY r.created DESC, r.id LIMIT %s""", (max(1, min(limit, 200)),))


@router.get("/audit")
def admin_audit(limit: int = 100, before: int = 0, actor: str = "", action: str = ""):
    return audit.entries(limit, before, actor, action)


@router.get("/audit.csv")
def admin_audit_csv():
    payload, mime = exporters.admin_audit_csv(audit.all_entries())
    return Response(payload, media_type=mime, headers={"Content-Disposition": 'attachment; filename="admin-audit-trail.csv"'})


@router.get("/runs.csv")
def runs_csv():
    """Every run, newest first (up to 50,000), for usage and cost reporting."""
    rows = db.rows("SELECT r.*, p.name AS project_name FROM runs r LEFT JOIN projects p ON p.id = r.project_id ORDER BY r.created DESC, r.id LIMIT 50000")
    payload, mime = exporters.runs_csv(rows)
    return Response(payload, media_type=mime, headers={"Content-Disposition": 'attachment; filename="ai-runs.csv"'})


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return {"values": settings.get_settings(), "defaults": settings.DEFAULTS, "limits": settings.NUMERIC_LIMITS,
            "embed_dim": config.EMBED_DIM}


@router.put("/settings")
def put_settings(request: Request, body: Dict[str, Any] = Body(...)):
    before = settings.get_settings()
    values = settings.update_settings(body)
    changed = {k: {"from": before.get(k), "to": values.get(k)} for k in values if before.get(k) != values.get(k)}
    if changed:
        audit.record(request, "settings changed", "Generation settings", ", ".join(sorted(changed)), changed)
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
def create_provider(request: Request, body: Dict[str, Any] = Body(...)):
    saved = settings.save_provider(body)
    audit.record(request, "provider created", saved["name"], "%s%s" % (saved["kind"], ", model " + saved["model"] if saved.get("model") else ""))
    return saved


@router.put("/providers/{pid}")
def update_provider(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
    saved = settings.save_provider(body, pid)
    key_note = "; key replaced" if body.get("api_key") else "; key removed" if body.get("clear_key") else ""
    audit.record(request, "provider updated", saved["name"], "%s%s%s" % (saved["kind"], ", model " + saved["model"] if saved.get("model") else "", key_note))
    return saved


@router.delete("/providers/{pid}", status_code=204)
def delete_provider(pid: str, request: Request):
    row = db.one("SELECT name FROM providers WHERE id = %s", (pid,))
    settings.delete_provider(pid)
    audit.record(request, "provider deleted", row["name"])


@router.post("/providers/{pid}/default")
def make_default(pid: str, request: Request):
    settings.set_default(pid)
    audit.record(request, "provider set as default", db.one("SELECT name FROM providers WHERE id = %s", (pid,))["name"])
    return {"ok": True}


@router.post("/providers/models")
def provider_models(body: Dict[str, Any] = Body(...)):
    """List models for a provider being configured. Uses the key in the request, or the saved key of
    `provider_id` when the key field was left blank."""
    kind = str(body.get("kind") or "")
    base_url = str(body.get("base_url") or "").strip()
    key = str(body.get("api_key") or "").strip()
    if kind not in ("openai", "anthropic"):
        raise HTTPException(400, "Model lists are available for OpenAI-compatible and Anthropic providers")
    if not re.match(r"^https?://[^\s]+$", base_url):
        raise HTTPException(400, "Enter the endpoint URL first")
    if not key and body.get("provider_id"):
        key = settings.get_provider(str(body["provider_id"]))["api_key"]
    try:
        models = llm.list_models(kind, base_url, key)
    except llm.LLMError as exc:
        raise HTTPException(400, "Could not load models: %s" % exc)
    return {"models": models, "embedding_models": [m for m in models if "embed" in m["id"].lower()]}


@router.post("/providers/{pid}/test", dependencies=[Depends(ai_fair_use)])
def test_provider(pid: str):
    return llm.test_connection(settings.get_provider(pid), settings.get_settings())


SAMPLE_BRD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "order-to-cash-brd.md")


@router.post("/providers/{pid}/evaluate", dependencies=[Depends(ai_fair_use)])
def evaluate_provider(pid: str, request: Request, stages: str = "requirements"):
    """How does this provider do on a BRD whose answer is known? Runs the extraction on the built-in sample (twelve numbered requirements, one of
    them an open item that must stay open) and scores it: how many of the twelve numbers were found, what was missed, whether the TBD survived,
    and what it cost in time, requests and tokens. A trial before relying on a provider, not a benchmark."""
    profile = settings.get_provider(pid)
    with open(SAMPLE_BRD_PATH, encoding="utf-8") as f:
        text = f.read()
    expected = sorted(set(re.findall(r"\b(BR-\d+):", text)))
    usage = {"in": 0, "out": 0, "requests": 0}
    started = time.monotonic()
    error, items = "", []
    try:
        items = pipeline.extract_requirements([{"id": "sample", "filename": "order-to-cash-brd.md", "text": text}], "generic", profile,
                                              settings.get_settings(), lambda m: None, usage)
    except Exception as exc:  # noqa: BLE001 - an unreachable or misbehaving provider is the answer, not a server error
        error = str(exc)[:300] or type(exc).__name__
    found = sorted({i.get("source_id") for i in items if i.get("source_id") in expected})
    missing = [e for e in expected if e not in found]
    kept_tbd = any("tbd" in (i.get("statement") or "").lower() for i in items)
    seconds = round(time.monotonic() - started, 1)
    verdict = ("The provider could not be used: " + error) if error else (
        "Found %d of %d requirement numbers%s; %s the open item (TBD) %s; %d requests, %.1f s." % (
            len(found), len(expected), (" (missed " + ", ".join(missing) + ")") if missing else "", "kept" if kept_tbd else "LOST",
            "as it was" if kept_tbd else "(it was resolved or dropped)", usage.get("requests", 0), seconds))
    stories_result = None
    if "stories" in stages.split(","):
        stories_result = _evaluate_stories(profile, text)
        verdict += " " + stories_result["verdict"]
    audit.record(request, "provider evaluated", profile["name"], verdict[:280])
    return dict(({"stories": stories_result} if stories_result else {}), **{"provider": profile["name"], "model": profile.get("model") or "", "expected": len(expected), "found": len(found), "missing": missing,
            "items": len(items), "kept_tbd": kept_tbd, "seconds": seconds, "requests": usage.get("requests", 0), "tokens_in": usage.get("in", 0),
            "tokens_out": usage.get("out", 0), "error": error, "verdict": verdict})


def _evaluate_stories(profile: Dict[str, Any], sample_text: str) -> Dict[str, Any]:
    """Stories from the sample's twelve requirements (taken from the BRD itself, so the provider under test does not write its own exam)."""
    epic, section, reqs = "Order Management", "General", []
    for line in sample_text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        m = re.match(r"(BR-\d+): (.+)", line)
        if m:
            item = pipeline.normalize_requirement({"statement": m.group(2), "source_id": m.group(1), "epic": epic, "section": section}, {"epic": epic, "heading": section}, "Sample")
            if item:
                item["key"] = "REQ-%03d" % (len(reqs) + 1)
                reqs.append(item)
    usage = {"in": 0, "out": 0, "requests": 0}
    started = time.monotonic()
    error, stories = "", []
    try:
        stories = pipeline.generate_stories(reqs, "", "generic", profile, dict(settings.get_settings(), rag_top_k=0), lambda m: None, usage)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:300] or type(exc).__name__
    keys = [r["key"] for r in reqs]
    covered = sorted({k for s in stories for k in s.get("requirement_ids", []) if k in keys})
    uncovered = [k for k in keys if k not in covered]
    gherkin = sum(1 for s in stories if s.get("acceptance_criteria") and all(re.match(r"\s*(given|when|then|and|but)\b", c, re.I) for c in s["acceptance_criteria"]))
    seconds = round(time.monotonic() - started, 1)
    verdict = ("Stories: the provider could not be used: " + error) if error else (
        "Stories: %d stories cover %d of %d requirements%s; %d of them have Given/When/Then criteria; %d requests, %.1f s." % (
            len(stories), len(covered), len(keys), (" (not covered: " + ", ".join(uncovered) + ")") if uncovered else "", gherkin, usage.get("requests", 0), seconds))
    return {"requirements": len(keys), "covered": len(covered), "uncovered": uncovered, "stories": len(stories), "gherkin": gherkin, "seconds": seconds,
            "requests": usage.get("requests", 0), "tokens_in": usage.get("in", 0), "tokens_out": usage.get("out", 0), "error": error, "verdict": verdict}


# ---- product taxonomy ----------------------------------------------------

MAX_CLASSES = 2000
_ATTR = re.compile(r"^[a-z0-9_]{1,40}$")


def _split(value: Any) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[;,\n]", str(value or ""))
    return [str(x).strip() for x in items if str(x).strip()]


def _clean_class(body: Dict[str, Any]) -> Dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(400, "Class name is required (max 120 characters)")
    attributes = [a.lower().replace(" ", "_") for a in _split(body.get("attributes"))][:50]
    bad = [a for a in attributes if not _ATTR.match(a)]
    if bad:
        raise HTTPException(400, "Invalid attribute(s): %s. Use lowercase letters, digits and underscores." % ", ".join(bad))
    keywords = sorted({k.lower()[:60] for k in _split(body.get("keywords"))})[:100]
    return {"name": name, "attributes": list(dict.fromkeys(attributes)), "keywords": keywords,
            "description": str(body.get("description") or "").strip()[:500]}


@router.get("/taxonomy")
def get_taxonomy():
    return {"classes": db.rows("SELECT * FROM product_classes ORDER BY name"),
            "attribute_rules": db.rows("SELECT * FROM attribute_rules ORDER BY attribute")}


@router.delete("/taxonomy")
def clear_taxonomy(request: Request):
    """Start from scratch: remove every product class and attribute rule (for another industry, or for none). They are not seeded again at restart."""
    with db.conn() as c:
        classes = c.execute("DELETE FROM product_classes").rowcount
        rules = c.execute("DELETE FROM attribute_rules").rowcount
    taxonomy.invalidate()
    if classes or rules:
        audit.record(request, "taxonomy cleared", "Product taxonomy", "%d classes and %d rules removed" % (classes, rules))
    return {"removed_classes": classes, "removed_rules": rules}


@router.post("/taxonomy/classes", status_code=201)
def create_class(request: Request, body: Dict[str, Any] = Body(...)):
    c = _clean_class(body)
    if db.one("SELECT count(*) AS n FROM product_classes")["n"] >= MAX_CLASSES:
        raise HTTPException(400, "Taxonomy is limited to %d classes" % MAX_CLASSES)
    if db.one("SELECT 1 AS x FROM product_classes WHERE lower(name)=lower(%s)", (c["name"],)):
        raise HTTPException(409, "A class with that name exists")
    cid = db.new_id()
    db.execute("INSERT INTO product_classes (id, name, attributes, keywords, description, created) VALUES (%s,%s,%s,%s,%s,%s)",
               (cid, c["name"], json.dumps(c["attributes"]), json.dumps(c["keywords"]), c["description"], db.now()))
    taxonomy.invalidate()
    audit.record(request, "taxonomy class created", c["name"])
    return db.one("SELECT * FROM product_classes WHERE id=%s", (cid,))


@router.put("/taxonomy/classes/{cid}")
def update_class(cid: str, request: Request, body: Dict[str, Any] = Body(...)):
    c = _clean_class(body)
    if not db.one("SELECT 1 AS x FROM product_classes WHERE id=%s", (cid,)):
        raise HTTPException(404, "Class not found")
    if db.one("SELECT 1 AS x FROM product_classes WHERE lower(name)=lower(%s) AND id<>%s", (c["name"], cid)):
        raise HTTPException(409, "A class with that name exists")
    db.execute("UPDATE product_classes SET name=%s, attributes=%s, keywords=%s, description=%s WHERE id=%s",
               (c["name"], json.dumps(c["attributes"]), json.dumps(c["keywords"]), c["description"], cid))
    taxonomy.invalidate()
    audit.record(request, "taxonomy class updated", c["name"])
    return db.one("SELECT * FROM product_classes WHERE id=%s", (cid,))


@router.delete("/taxonomy/classes/{cid}", status_code=204)
def delete_class(cid: str, request: Request):
    row = db.one("SELECT name FROM product_classes WHERE id=%s", (cid,))
    db.execute("DELETE FROM product_classes WHERE id=%s", (cid,))
    taxonomy.invalidate()
    if row:
        audit.record(request, "taxonomy class deleted", row["name"])


@router.put("/taxonomy/rules")
def put_rules(request: Request, body: Dict[str, Any] = Body(...)):
    """Replace attribute -> acceptance-criterion rules. Body: {"rules": {attribute: criteria}}."""
    rules = body.get("rules")
    if not isinstance(rules, dict):
        raise HTTPException(400, "rules must be an object")
    clean = {}
    for attr, text in rules.items():
        attr = str(attr).strip().lower()
        text = str(text or "").strip()
        if not _ATTR.match(attr):
            raise HTTPException(400, "Invalid attribute: %s" % attr)
        if text:
            clean[attr] = text[:500]
    with db.conn() as c:
        c.execute("DELETE FROM attribute_rules")
        for attr, text in clean.items():
            c.execute("INSERT INTO attribute_rules (attribute, criteria) VALUES (%s,%s)", (attr, text))
    taxonomy.invalidate()
    audit.record(request, "taxonomy rules replaced", "Attribute rules", "%d rules" % len(clean))
    return {"attribute_rules": db.rows("SELECT * FROM attribute_rules ORDER BY attribute")}


@router.post("/taxonomy/import")
def import_taxonomy(request: Request, body: Dict[str, Any] = Body(...)):
    """CSV columns: name, attributes, keywords, description (lists separated by ';'). Upserts by name;
    mode=replace also removes classes that are not in the file."""
    text = str(body.get("csv") or "")
    mode = body.get("mode", "merge")
    if mode not in ("merge", "replace"):
        raise HTTPException(400, "mode must be merge or replace")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or "name" not in {k.strip().lower() for k in (rows[0].keys() if rows else [])}:
        raise HTTPException(400, "CSV needs a header row with at least a 'name' column")
    if len(rows) > MAX_CLASSES:
        raise HTTPException(400, "At most %d classes per import" % MAX_CLASSES)
    parsed, errors = [], []
    for i, row in enumerate(rows, 2):
        row = {k.strip().lower(): v for k, v in row.items() if k}
        try:
            parsed.append(_clean_class(row))
        except HTTPException as exc:
            errors.append("line %d: %s" % (i, exc.detail))
    if errors:
        raise HTTPException(400, "; ".join(errors[:10]))
    created = updated = 0
    with db.conn() as c:
        existing = {r["lower"]: r["id"] for r in c.execute("SELECT id, lower(name) AS lower FROM product_classes").fetchall()}
        for p in parsed:
            cid = existing.pop(p["name"].lower(), None)
            if cid:
                c.execute("UPDATE product_classes SET name=%s, attributes=%s, keywords=%s, description=%s WHERE id=%s",
                          (p["name"], json.dumps(p["attributes"]), json.dumps(p["keywords"]), p["description"], cid))
                updated += 1
            else:
                c.execute("INSERT INTO product_classes (id, name, attributes, keywords, description, created) VALUES (%s,%s,%s,%s,%s,%s)",
                          (db.new_id(), p["name"], json.dumps(p["attributes"]), json.dumps(p["keywords"]), p["description"], db.now()))
                created += 1
        removed = 0
        if mode == "replace" and existing:
            c.execute("DELETE FROM product_classes WHERE id = ANY(%s)", (list(existing.values()),))
            removed = len(existing)
    taxonomy.invalidate()
    audit.record(request, "taxonomy imported", "Product taxonomy", "%s: %d created, %d updated, %d removed" % (mode, created, updated, removed))
    return {"created": created, "updated": updated, "removed": removed}


@router.get("/taxonomy.csv")
def export_taxonomy():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "attributes", "keywords", "description"])
    for r in db.rows("SELECT * FROM product_classes ORDER BY name"):
        w.writerow([r["name"], "; ".join(r["attributes"]), "; ".join(r["keywords"]), r["description"]])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="product-taxonomy.csv"'})
