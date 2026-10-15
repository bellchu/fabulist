"""Admin-managed generation settings and AI provider profiles (stored in Postgres)."""
import base64
import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken

from . import config, db

KINDS = {
    "mock": "Offline heuristic (no AI)",
    "openai": "OpenAI-compatible API",
    "anthropic": "Anthropic Messages API",
}
# Starting points offered in the admin UI; every field stays editable.
PRESETS = [
    {"label": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1", "model": ""},
    {"label": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"label": "Anthropic", "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5"},
    {"label": "Azure OpenAI / gateway", "kind": "openai", "base_url": "https://<resource>.openai.azure.com/openai/v1", "model": ""},
    {"label": "Self-hosted (vLLM / Ollama)", "kind": "openai", "base_url": "http://ollama:11434/v1", "model": ""},
]

DEFAULTS: Dict[str, Any] = {
    "temperature": 0.2,
    "max_output_tokens": 8000,
    "timeout_seconds": config.LLM_TIMEOUT,
    "chunk_chars": config.CHUNK_CHARS,
    "max_chunks_per_run": config.MAX_CHUNKS_PER_RUN,
    "rag_top_k": config.RAG_TOP_K,
    "redact_pii": True,
    "embed_provider_id": "",  # "" = offline hash embeddings
    "embed_model": "",
}
NUMERIC_LIMITS = {
    "temperature": (0.0, 1.0), "max_output_tokens": (256, 64000), "timeout_seconds": (10, 600),
    "chunk_chars": (1000, 50000), "max_chunks_per_run": (1, 2000), "rag_top_k": (0, 20),
}


class SettingsError(ValueError):
    pass


def _fernet() -> Fernet:
    if not config.SETTINGS_KEY:
        raise SettingsError("SETTINGS_ENCRYPTION_KEY is not configured on the server; API keys cannot be stored.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SETTINGS_KEY.encode()).digest()))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SettingsError("Stored API key cannot be decrypted (encryption key changed?). Re-enter it.") from exc


# ---- generation settings -------------------------------------------------

def get_settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for r in db.rows("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = r["value"]
    return out


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise SettingsError("Unknown setting: %s" % key)
        if key in NUMERIC_LIMITS:
            lo, hi = NUMERIC_LIMITS[key]
            try:
                num = float(value) if isinstance(DEFAULTS[key], float) else int(value)
            except (TypeError, ValueError):
                raise SettingsError("%s must be a number" % key)
            if not lo <= num <= hi:
                raise SettingsError("%s must be between %s and %s" % (key, lo, hi))
            clean[key] = num
        elif key == "redact_pii":
            clean[key] = bool(value)
        elif key == "embed_provider_id":
            value = str(value or "")
            if value:
                p = db.one("SELECT kind FROM providers WHERE id = %s", (value,))
                if not p or p["kind"] != "openai":
                    raise SettingsError("Embeddings require an OpenAI-compatible provider")
            clean[key] = value
        else:
            clean[key] = str(value or "").strip()[:200]
    with db.conn() as c:
        for key, value in clean.items():
            c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) "
                      "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, json.dumps(value)))
    return get_settings()


# ---- provider profiles ---------------------------------------------------

def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "kind_label": KINDS.get(r["kind"], r["kind"]),
            "base_url": r["base_url"], "model": r["model"], "has_key": bool(r["api_key_enc"]),
            "key_hint": r["key_hint"], "is_default": r["is_default"], "created": r["created"]}


def list_providers() -> List[Dict[str, Any]]:
    return [_public(r) for r in db.rows("SELECT * FROM providers ORDER BY is_default DESC, created")]


def get_provider(pid: str) -> Dict[str, Any]:
    """Internal: full profile including the decrypted API key."""
    r = db.one("SELECT * FROM providers WHERE id = %s", (pid,))
    if not r:
        raise SettingsError("Provider not found")
    out = _public(r)
    out["api_key"] = _decrypt(r["api_key_enc"]) if r["api_key_enc"] else ""
    return out


def default_provider() -> Dict[str, Any]:
    r = db.one("SELECT id FROM providers ORDER BY is_default DESC, created LIMIT 1")
    if not r:
        raise SettingsError("No AI provider configured")
    return get_provider(r["id"])


def save_provider(data: Dict[str, Any], pid: Optional[str] = None) -> Dict[str, Any]:
    name = str(data.get("name") or "").strip()[:80]
    kind = str(data.get("kind") or "").strip()
    base_url = str(data.get("base_url") or "").strip().rstrip("/")[:300]
    model = str(data.get("model") or "").strip()[:120]
    if not name:
        raise SettingsError("Name is required")
    if kind not in KINDS:
        raise SettingsError("Unknown provider type")
    if kind != "mock":
        if not re.match(r"^https?://[^\s]+$", base_url):
            raise SettingsError("Endpoint must be an http(s) URL")
        if not model:
            raise SettingsError("Model is required")
    existing = db.one("SELECT * FROM providers WHERE id = %s", (pid,)) if pid else None
    if pid and not existing:
        raise SettingsError("Provider not found")
    enc = existing["api_key_enc"] if existing else None
    hint = existing["key_hint"] if existing else ""
    new_key = str(data.get("api_key") or "").strip()
    if data.get("clear_key"):
        enc, hint = None, ""
    elif new_key:
        enc, hint = _encrypt(new_key), "…" + new_key[-4:]
    if pid:
        db.execute("UPDATE providers SET name=%s, kind=%s, base_url=%s, model=%s, api_key_enc=%s, key_hint=%s WHERE id=%s",
                   (name, kind, base_url, model, enc, hint, pid))
    else:
        pid = db.new_id()
        first = db.one("SELECT 1 AS x FROM providers LIMIT 1") is None
        db.execute("INSERT INTO providers (id, name, kind, base_url, model, api_key_enc, key_hint, is_default, created) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (pid, name, kind, base_url, model, enc, hint, first, db.now()))
    return _public(db.one("SELECT * FROM providers WHERE id = %s", (pid,)))


def set_default(pid: str) -> None:
    if not db.one("SELECT 1 AS x FROM providers WHERE id = %s", (pid,)):
        raise SettingsError("Provider not found")
    with db.conn() as c:
        c.execute("UPDATE providers SET is_default = (id = %s)", (pid,))


def delete_provider(pid: str) -> None:
    row = db.one("SELECT is_default FROM providers WHERE id = %s", (pid,))
    if not row:
        raise SettingsError("Provider not found")
    if db.one("SELECT count(*) AS n FROM providers")["n"] <= 1:
        raise SettingsError("At least one provider must remain")
    with db.conn() as c:
        c.execute("DELETE FROM providers WHERE id = %s", (pid,))
        if row["is_default"]:
            c.execute("UPDATE providers SET is_default = TRUE WHERE id = "
                      "(SELECT id FROM providers ORDER BY created LIMIT 1)")
    if get_settings()["embed_provider_id"] == pid:
        update_settings({"embed_provider_id": ""})


def seed() -> None:
    """First start: create the offline provider, plus one from env if configured."""
    if db.one("SELECT 1 AS x FROM providers LIMIT 1"):
        return
    save_provider({"name": "Offline heuristic", "kind": "mock"})
    if config.LLM_PROVIDER in ("openai", "anthropic") and config.LLM_MODEL and config.LLM_BASE_URL:
        data = {"name": "Default (%s)" % config.LLM_PROVIDER, "kind": config.LLM_PROVIDER,
                "base_url": config.LLM_BASE_URL, "model": config.LLM_MODEL}
        if config.LLM_API_KEY and config.SETTINGS_KEY:
            data["api_key"] = config.LLM_API_KEY
        set_default(save_provider(data)["id"])
