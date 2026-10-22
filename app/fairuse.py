"""Fair use of the shared pool of interactive AI calls."""
import threading
from typing import Dict, Iterator

from fastapi import Request

from . import config, history, llm

_user_ai_lock = threading.Lock()
_user_ai: Dict[str, int] = {}


def ai_fair_use(request: Request) -> Iterator[None]:
    """A dependency for the routes that make an AI call inside the request (Ask, Refine, Estimate, provider test): they share a small pool,
    so one person may have at most MAX_INTERACTIVE_PER_USER running at once, and cannot lock everybody else out of the rest."""
    user, limit = history.actor(request), max(1, config.MAX_INTERACTIVE_PER_USER)
    with _user_ai_lock:
        if _user_ai.get(user, 0) >= limit:
            raise llm.AIBusy("You already have %d AI requests running" % limit)
        _user_ai[user] = _user_ai.get(user, 0) + 1
    try:
        yield
    finally:
        with _user_ai_lock:
            _user_ai[user] -= 1
            if not _user_ai[user]:
                del _user_ai[user]
