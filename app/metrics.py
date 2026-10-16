"""Minimal Prometheus metrics (text exposition format) without extra dependencies."""
import threading
from typing import Dict, List, Tuple

from . import db

_lock = threading.Lock()
_counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
BUCKETS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]
_hist: Dict[Tuple[Tuple[str, str], ...], List[float]] = {}  # label set -> bucket counts + [sum, count]
HELP = {
    "fabulist_http_requests_total": ("counter", "HTTP requests by method, route and status."),
    "fabulist_runs_total": ("counter", "Completed AI runs by stage and final status."),
    "fabulist_llm_tokens_total": ("counter", "LLM tokens consumed, by direction."),
    "fabulist_tracker_items_total": ("counter", "Items pushed to issue trackers, by result."),
    "fabulist_http_request_duration_seconds": ("histogram", "HTTP request latency by route."),
}


def inc(name: str, value: float = 1, **labels: str) -> None:
    key = (name, tuple(sorted(labels.items())))
    with _lock:
        _counters[key] = _counters.get(key, 0) + value


def observe_request(route: str, seconds: float) -> None:
    key = (("route", route),)
    with _lock:
        h = _hist.setdefault(key, [0.0] * (len(BUCKETS) + 2))
        for i, b in enumerate(BUCKETS):
            if seconds <= b:
                h[i] += 1
        h[-2] += seconds
        h[-1] += 1


def _labels(pairs: Tuple[Tuple[str, str], ...], extra: str = "") -> str:
    parts = ['%s="%s"' % (k, str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")) for k, v in pairs]
    if extra:
        parts.append(extra)
    return "{%s}" % ",".join(parts) if parts else ""


def render() -> str:
    lines: List[str] = []
    with _lock:
        counters = dict(_counters)
        hist = {k: list(v) for k, v in _hist.items()}
    for name, (kind, text) in HELP.items():
        lines += ["# HELP %s %s" % (name, text), "# TYPE %s %s" % (name, kind)]
        if kind == "counter":
            lines += ["%s%s %g" % (name, _labels(labels), v) for (n, labels), v in sorted(counters.items()) if n == name]
        else:
            for labels, h in sorted(hist.items()):
                for i, b in enumerate(BUCKETS):
                    lines.append("%s_bucket%s %g" % (name, _labels(labels, 'le="%g"' % b), h[i]))
                lines.append("%s_bucket%s %g" % (name, _labels(labels, 'le="+Inf"'), h[-1]))
                lines.append("%s_sum%s %g" % (name, _labels(labels), h[-2]))
                lines.append("%s_count%s %g" % (name, _labels(labels), h[-1]))
    totals = db.one("""SELECT (SELECT count(*) FROM projects) AS projects, (SELECT count(*) FROM documents) AS documents,
        (SELECT count(*) FROM requirements) AS requirements, (SELECT count(*) FROM stories) AS stories,
        (SELECT count(*) FROM runs WHERE status = 'running') AS running_runs""")
    for key, value in totals.items():
        name = "fabulist_%s" % key
        lines += ["# HELP %s Current number of %s." % (name, key.replace("_", " ")), "# TYPE %s gauge" % name, "%s %d" % (name, value)]
    return "\n".join(lines) + "\n"
