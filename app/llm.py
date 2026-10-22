"""LLM provider adapters for provider profiles managed in the admin dashboard."""
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from . import config


class LLMError(RuntimeError):
    pass


Usage = Dict[str, int]


class _Retryable(Exception):
    pass


# A run in a background thread registers a check that raises when the user cancels. It is called before every attempt and
# while backing off, so Cancel waits for at most the request already in flight, not for all remaining retries.
_cancel = threading.local()


def set_cancel_check(fn: Any) -> None:
    _cancel.fn = fn


def get_cancel_check() -> Any:
    """The current thread's cancel hook, so a run can hand it to the worker threads it starts."""
    return getattr(_cancel, "fn", None)


def _check_cancelled() -> None:
    fn = getattr(_cancel, "fn", None)
    if fn:
        fn()


def _sleep(seconds: float) -> None:
    for _ in range(max(1, round(seconds / 0.25))):  # slices, so a cancel is noticed within a quarter of a second
        _check_cancelled()
        time.sleep(0.25)


HINTS = {401: "check the API key and the endpoint URL", 403: "the key may not have access to this model or endpoint",
         404: "check the model name and the endpoint URL"}


def _is_quota(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return "insufficient_quota" in text or ("quota" in text and "billing" in text)


MAX_WAIT_SECONDS = 60.0  # the longest single wait between attempts, whatever the provider asks for
_cooldowns: Dict[str, float] = {}  # provider host -> monotonic time before which nothing should be sent to it
_cooldown_lock = threading.Lock()


def _note_rate_limit(host: str, seconds: float) -> None:
    """The provider asked for a pause: every call to it, from every section of every run, waits it out, instead of each one finding out the hard way."""
    with _cooldown_lock:
        _cooldowns[host] = max(_cooldowns.get(host, 0.0), time.monotonic() + seconds)


def _wait_cooldown(host: str) -> None:
    with _cooldown_lock:
        remaining = _cooldowns.get(host, 0.0) - time.monotonic()
    if remaining > 0:
        _sleep(remaining)


def _retry_after(resp: httpx.Response) -> Optional[float]:
    try:
        return max(0.0, float(resp.headers.get("retry-after", "")))
    except ValueError:
        return None  # absent, or an HTTP date (rare): fall back to exponential backoff


def _backoff(attempt: int, asked: Optional[float]) -> float:
    """Seconds to wait before the next attempt: what the provider asked for, else 1.5 s doubling each time; capped, with jitter so that
    parallel sections do not all come back at the same instant."""
    if asked is not None:
        return min(asked, MAX_WAIT_SECONDS) + random.uniform(0.0, 1.0)
    return min(MAX_WAIT_SECONDS, 1.5 * (2 ** attempt)) * random.uniform(0.75, 1.25)


def _post(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """POST with bounded, patient retries for transient failures (429/5xx/network). A large run makes hundreds of requests, so even a
    modest failure rate means some request fails several times in a row: three quick attempts failed whole runs."""
    host = urlsplit(url).hostname or url
    last = "unknown error"
    attempts = max(1, int(config.LLM_MAX_ATTEMPTS))
    for attempt in range(attempts):
        _check_cancelled()
        _wait_cooldown(host)
        asked: Optional[float] = None
        limited = False
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429 and _is_quota(resp):
                # Not transient: retrying only delays the message that tells the administrator what to fix.
                raise LLMError("provider returned HTTP 429: %s (the account is out of quota; retrying will not help)" % _error_text(resp))
            if resp.status_code == 429 or resp.status_code >= 500:
                detail = _error_text(resp)
                asked, limited = _retry_after(resp), resp.status_code == 429 or resp.headers.get("retry-after") is not None
                raise _Retryable("provider returned HTTP %d%s" % (resp.status_code, ": " + detail if detail else ""))
            if resp.status_code >= 400:
                hint = HINTS.get(resp.status_code)
                raise LLMError("provider returned HTTP %d: %s%s" % (resp.status_code, _error_text(resp), " (%s)" % hint if hint else ""))
            return resp.json()
        except _Retryable as exc:
            last = str(exc)
        except httpx.HTTPError as exc:
            last = "cannot reach %s (%s): check the endpoint URL and network access" % (host, type(exc).__name__)
        _check_cancelled()  # the attempt that just failed may have been the one the user was waiting on
        if attempt < attempts - 1:
            wait = _backoff(attempt, asked)
            if limited:
                _note_rate_limit(host, wait)
            _sleep(wait)
    raise LLMError(last)


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return str(msg)[:200]
    except Exception:
        return resp.text[:200]


class AIBusy(Exception):
    """Too many AI calls made inside requests are in flight. Not an LLMError/RuntimeError on purpose: endpoints turn those
    into 502 "failed" answers, while this is a 429 telling the person to retry."""


_slots_lock = threading.Lock()
_slots: Optional[Tuple[int, threading.BoundedSemaphore]] = None


def _interactive_slots() -> threading.BoundedSemaphore:
    global _slots
    size = max(1, config.MAX_INTERACTIVE_AI_CALLS)
    with _slots_lock:
        if _slots is None or _slots[0] != size:
            _slots = (size, threading.BoundedSemaphore(size))
        return _slots[1]


def _reset_interactive_slots() -> None:
    global _slots
    with _slots_lock:
        _slots = None


_run_slots_lock = threading.Lock()
_run_slots_state: Optional[Tuple[int, threading.BoundedSemaphore]] = None


def _run_slots() -> threading.BoundedSemaphore:
    global _run_slots_state
    size = max(1, config.MAX_RUN_AI_CALLS)
    with _run_slots_lock:
        if _run_slots_state is None or _run_slots_state[0] != size:
            _run_slots_state = (size, threading.BoundedSemaphore(size))
        return _run_slots_state[1]


def _reset_run_slots() -> None:
    global _run_slots_state
    with _run_slots_lock:
        _run_slots_state = None


def complete(profile: Dict[str, Any], system: str, user: str, opts: Dict[str, Any]) -> Tuple[str, Usage]:
    """One provider call. Calls made inside a web request hold a request thread for as long as the provider takes (40 of them
    froze the whole app when the provider hung), so only a few may be in flight: the rest are refused at once instead of
    waiting. Background runs are bounded by their own pool and are never refused here."""
    if getattr(_cancel, "fn", None):
        # A background run. Each run asks several sections at once, so across runs the provider could see runs x sections calls: wait for
        # one of MAX_RUN_AI_CALLS slots (still noticing a cancel while waiting).
        sem = _run_slots()
        while not sem.acquire(timeout=0.25):
            _check_cancelled()
        try:
            return _complete(profile, system, user, opts)
        finally:
            sem.release()
    slots = _interactive_slots()
    if not slots.acquire(blocking=False):
        raise AIBusy("The AI service is busy with other requests")
    try:
        return _complete(profile, system, user, opts)
    finally:
        slots.release()


def _complete(profile: Dict[str, Any], system: str, user: str, opts: Dict[str, Any]) -> Tuple[str, Usage]:
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
