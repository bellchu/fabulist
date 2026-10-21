"""LLM provider adapters for provider profiles managed in the admin dashboard."""
import re
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

import httpx


class LLMError(RuntimeError):
    pass


Usage = Dict[str, int]


class _Retryable(Exception):
    pass


HINTS = {401: "check the API key and the endpoint URL", 403: "the key may not have access to this model or endpoint",
         404: "check the model name and the endpoint URL"}


def _is_quota(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return "insufficient_quota" in text or ("quota" in text and "billing" in text)


def _post(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """POST with bounded retries for transient failures (429/5xx/network)."""
    host = urlsplit(url).hostname or url
    last = "unknown error"
    for attempt in range(3):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429 and _is_quota(resp):
                # Not transient: retrying only delays the message that tells the administrator what to fix.
                raise LLMError("provider returned HTTP 429: %s (the account is out of quota; retrying will not help)" % _error_text(resp))
            if resp.status_code == 429 or resp.status_code >= 500:
                detail = _error_text(resp)
                raise _Retryable("provider returned HTTP %d%s" % (resp.status_code, ": " + detail if detail else ""))
            if resp.status_code >= 400:
                hint = HINTS.get(resp.status_code)
                raise LLMError("provider returned HTTP %d: %s%s" % (resp.status_code, _error_text(resp), " (%s)" % hint if hint else ""))
            return resp.json()
        except _Retryable as exc:
            last = str(exc)
        except httpx.HTTPError as exc:
            last = "cannot reach %s (%s): check the endpoint URL and network access" % (host, type(exc).__name__)
        if attempt < 2:
            time.sleep(1.5 * (2 ** attempt))
    raise LLMError(last)


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return str(msg)[:200]
    except Exception:
        return resp.text[:200]


def complete(profile: Dict[str, Any], system: str, user: str, opts: Dict[str, Any]) -> Tuple[str, Usage]:
    kind, model = profile["kind"], profile["model"]
    timeout = float(opts.get("timeout_seconds", 120))
    temperature = float(opts.get("temperature", 0.2))
    max_tokens = int(opts.get("max_output_tokens", 8000))
    base = (profile.get("base_url") or "").rstrip("/")
    key = profile.get("api_key") or ""
    if kind == "openai":
        headers = {"Authorization": "Bearer " + key} if key else {}
        data = _post(base + "/chat/completions", headers, {
            "model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, timeout)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected provider response shape") from exc
        u = data.get("usage") or {}
        return text, {"in": int(u.get("prompt_tokens") or 0), "out": int(u.get("completion_tokens") or 0)}
    if kind == "anthropic":
        if not key:
            raise LLMError("No API key configured for this provider")
        data = _post(base + "/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, {
            "model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
            "messages": [{"role": "user", "content": user}],
        }, timeout)
        try:
            text = "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError("Unexpected provider response shape") from exc
        u = data.get("usage") or {}
        return text, {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0)}
    raise LLMError("Provider type %s cannot generate text" % kind)


def list_models(kind: str, base_url: str, api_key: str) -> List[Dict[str, str]]:
    """Models the key can use: OpenAI-compatible GET /models, Anthropic GET /v1/models."""
    base = base_url.rstrip("/")
    if kind == "openai":
        url, headers = base + "/models", ({"Authorization": "Bearer " + api_key} if api_key else {})
    elif kind == "anthropic":
        url, headers = base + "/v1/models?limit=100", {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        return []
    try:
        resp = httpx.get(url, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        raise LLMError("%s contacting provider" % type(exc).__name__)
    if resp.status_code >= 400:
        raise LLMError("provider returned HTTP %d: %s" % (resp.status_code, _error_text(resp)))
    try:
        items = resp.json().get("data") or []
    except ValueError:
        raise LLMError("Provider did not return a model list")
    models = [{"id": str(m["id"]), "label": str(m.get("display_name") or m["id"])} for m in items if isinstance(m, dict) and m.get("id")]
    return sorted(models, key=lambda m: m["id"])


def test_connection(profile: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:
    if profile["kind"] == "mock":
        return {"ok": True, "latency_ms": 0, "reply": "Offline heuristic is always available."}
    started = time.monotonic()
    quick = dict(opts, max_output_tokens=16, timeout_seconds=min(30, int(opts.get("timeout_seconds", 30))))
    try:
        text, _ = complete(profile, "You are a connectivity check.", "Reply with the single word OK.", quick)
    except LLMError as exc:
        return {"ok": False, "latency_ms": int((time.monotonic() - started) * 1000), "error": str(exc)}
    return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000), "reply": text.strip()[:80]}


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_LONGNUM = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def redact(text: str) -> str:
    """Mask obvious personal identifiers before text leaves the cluster."""
    for pat, tag in ((_EMAIL, "[EMAIL]"), (_SSN, "[SSN]"), (_LONGNUM, "[NUMBER]"), (_PHONE, "[PHONE]")):
        text = pat.sub(tag, text)
    return text
