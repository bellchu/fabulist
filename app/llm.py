"""LLM provider adapters. Only the text-completion call lives here."""
import re
import time
from typing import Optional

import httpx

from . import config


class LLMError(RuntimeError):
    pass


def _post(url: str, headers: dict, payload: dict) -> dict:
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise LLMError("provider returned HTTP %d" % resp.status_code)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, LLMError) as exc:
            last = exc
            time.sleep(1.5 * (2 ** attempt))
    raise LLMError("LLM request failed: %s" % type(last).__name__)


def complete(system: str, user: str, provider: str, model: str = "") -> str:
    model = model or config.LLM_MODEL
    if not model:
        raise LLMError("No model configured (set LLM_MODEL or pass a model).")
    if provider == "openai":
        if not config.LLM_BASE_URL:
            raise LLMError("LLM_BASE_URL is not configured.")
        headers = {"Authorization": "Bearer " + config.LLM_API_KEY} if config.LLM_API_KEY else {}
        data = _post(config.LLM_BASE_URL.rstrip("/") + "/chat/completions", headers, {
            "model": model, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected provider response shape") from exc
    if provider == "anthropic":
        if not config.LLM_API_KEY:
            raise LLMError("LLM_API_KEY is not configured.")
        base = config.LLM_BASE_URL.rstrip("/") if config.LLM_BASE_URL else "https://api.anthropic.com"
        data = _post(base + "/v1/messages", {
            "x-api-key": config.LLM_API_KEY, "anthropic-version": "2023-06-01",
        }, {"model": model, "max_tokens": 8000, "temperature": 0.2, "system": system,
            "messages": [{"role": "user", "content": user}]})
        try:
            return "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError("Unexpected provider response shape") from exc
    raise LLMError("Unknown provider: %s" % provider)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_LONGNUM = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def redact(text: str) -> str:
    """Mask obvious personal identifiers before text leaves the cluster."""
    for pat, tag in ((_EMAIL, "[EMAIL]"), (_SSN, "[SSN]"), (_LONGNUM, "[NUMBER]"), (_PHONE, "[PHONE]")):
        text = pat.sub(tag, text)
    return text
