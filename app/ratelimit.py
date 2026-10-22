"""A request-rate ceiling per person (or per address when nobody is signed in), so one runaway script cannot starve everyone else.

Token bucket: RATE_LIMIT_PER_MINUTE requests of burst, refilled evenly over a minute. Generous on purpose (the UI polls); it exists to stop loops.
"""
import threading
import time
from typing import Dict, Tuple

from . import config

MAX_KEYS = 10000  # bounded memory: when exceeded, buckets that have refilled completely (idle people) are dropped
_lock = threading.Lock()
_buckets: Dict[str, Tuple[float, float]] = {}  # key -> (tokens, last refill time)


def take(key: str, now: float = 0.0) -> float:
    """Seconds to wait before the next request is allowed; 0 when this one is allowed (and counted)."""
    capacity = config.RATE_LIMIT_PER_MINUTE
    if capacity <= 0:
        return 0.0
    now = now or time.monotonic()
    rate = capacity / 60.0
    with _lock:
        tokens, last = _buckets.get(key, (float(capacity), now))
        tokens = min(float(capacity), tokens + (now - last) * rate)
        if tokens < 1.0:
            _buckets[key] = (tokens, now)
            return (1.0 - tokens) / rate
        _buckets[key] = (tokens - 1.0, now)
        if len(_buckets) > MAX_KEYS:
            for k in [k for k, (t, at) in _buckets.items() if t + (now - at) * rate >= capacity]:
                del _buckets[k]
        return 0.0


def reset() -> None:
    with _lock:
        _buckets.clear()
