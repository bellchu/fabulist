"""The alert rules and the Grafana dashboard name metrics by hand: they must match what /metrics really exposes."""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITORING = open(os.path.join(REPO, "deploy", "k8s", "monitoring.yaml")).read()


def _referenced():
    return set(re.findall(r"\bfabulist_[a-z_]+", MONITORING))


def _exposed(client, monkeypatch):
    from app import config, metrics
    monkeypatch.setattr(config, "METRICS_TOKEN", "t")
    metrics.inc("fabulist_http_requests_total", method="GET", route="/x", status="200")  # make every kind of series appear
    metrics.observe_request("/x", 0.1)
    text = client.get("/metrics", headers={"Authorization": "Bearer t"}).text
    names = set(re.findall(r"^# TYPE (\S+) ", text, flags=re.M))
    return names | {n + suffix for n in names for suffix in ("_bucket", "_sum", "_count")}


def test_every_metric_in_the_alerts_and_dashboard_exists(client, monkeypatch):
    referenced, exposed = _referenced(), _exposed(client, monkeypatch)
    assert len(referenced) >= 12, "the extractor found the metric names"
    assert referenced - exposed == set(), "alerts/dashboard use metrics the app does not expose"
    assert "fabulist_made_up_total" not in exposed


def test_the_new_signals_have_alerts_and_panels():
    for name in ("fabulist_database_up", "fabulist_auth_failures_total", "fabulist_edit_conflicts_total", "fabulist_database_unavailable_total"):
        assert name in MONITORING, name + " is exposed but neither alerted on nor graphed"


def test_memory_is_exposed_and_alerted_on(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "METRICS_TOKEN", "t")
    text = client.get("/metrics", headers={"Authorization": "Bearer t"}).text
    values = dict(re.findall(r"^(fabulist_(?:process_resident_memory|process_peak_resident_memory|memory_limit)_bytes) (\d+)$", text, flags=re.M))
    assert int(values["fabulist_process_resident_memory_bytes"]) > 10_000_000, "a running Python process holds more than 10 MB"
    assert int(values["fabulist_process_peak_resident_memory_bytes"]) >= int(values["fabulist_process_resident_memory_bytes"]) * 0.9
    assert "fabulist_memory_limit_bytes" in values
    assert "FabulistMemoryNearLimit" in MONITORING


def _dashboard():
    import json
    block = MONITORING.split("fabulist.json: |\n", 1)[1]
    return json.loads(re.sub(r"(?m)^    ", "", block.split("\n---", 1)[0]))


def test_the_dashboard_is_valid_json_and_graphs_the_capacity_signals():
    dash = _dashboard()
    panels = {p["title"]: p for p in dash["panels"]}
    queue, memory = panels["AI runs: running and waiting in the queue"], panels["Memory against the container limit"]
    exprs = lambda p: [t["expr"] for t in p["targets"]]  # noqa: E731
    assert "fabulist_running_runs" in exprs(queue) and "fabulist_queued_runs" in exprs(queue)
    assert {"fabulist_process_resident_memory_bytes", "fabulist_process_peak_resident_memory_bytes", "fabulist_memory_limit_bytes"} <= set(exprs(memory))
    assert memory["fieldConfig"]["defaults"]["unit"] == "bytes"
    boxes = [(p["gridPos"]["x"], p["gridPos"]["y"], p["gridPos"]["w"], p["gridPos"]["h"]) for p in dash["panels"] if p["type"] != "row"]
    for i, (x1, y1, w1, h1) in enumerate(boxes):
        for x2, y2, w2, h2 in boxes[i + 1:]:
            assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, f"dashboard panels overlap: {(x1, y1, w1, h1)} and {(x2, y2, w2, h2)}"


def test_provider_throttling_is_graphed_and_alerted_on():
    dash = _dashboard()
    panel = next(p for p in dash["panels"] if p["title"] == "AI provider retries by reason (5 min)")
    assert "fabulist_llm_retries_total" in panel["targets"][0]["expr"]
    assert "FabulistAIProviderThrottling" in MONITORING and "fabulist_llm_retries_total" in MONITORING.split("FabulistAIProviderThrottling", 1)[1][:400]


def test_every_alert_has_a_runbook_row():
    readme = open(os.path.join(REPO, "README.md")).read()
    alerts = re.findall(r"- alert: (\w+)", MONITORING)
    assert len(alerts) >= 10 and "FabulistRestarting" in alerts
    assert [a for a in alerts if "| %s |" % a not in readme] == []


def test_the_database_size_is_exposed_and_alerted_on(client, monkeypatch):
    from app import config, metrics
    monkeypatch.setattr(config, "METRICS_TOKEN", "t")
    text = client.get("/metrics", headers={"Authorization": "Bearer t"}).text
    size = [l for l in text.splitlines() if l.startswith("fabulist_database_size_bytes ")]
    assert size and int(size[0].split()[1]) > 100_000
    assert "FabulistDatabaseLarge" in MONITORING and "fabulist_database_size_bytes" in MONITORING
