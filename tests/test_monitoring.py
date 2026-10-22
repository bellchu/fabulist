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
