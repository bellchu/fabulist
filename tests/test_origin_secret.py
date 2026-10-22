"""ORIGIN_SECRET: an optional shared secret the proxy adds to every request (a Cloudflare transform rule, for example), so the app answers nothing that
did not come through the proxy: headers like CF-Connecting-IP are then trustworthy. Off unless configured."""
from app import config


def test_off_by_default(client):
    assert config.ORIGIN_SECRET == ""
    assert client.get("/api/meta").status_code == 200


def test_with_a_secret_only_requests_that_carry_it_are_answered(client, monkeypatch):
    monkeypatch.setattr(config, "ORIGIN_SECRET", "s3cret-shared-with-the-proxy")
    for path in ("/api/meta", "/api/projects", "/", "/static/app.css", "/docs"):
        r = client.get(path)
        assert r.status_code == 403, path
        assert "x-request-id" in r.headers
    assert client.get("/api/meta", headers={"X-Origin-Secret": "wrong"}).status_code == 403
    ok = client.get("/api/meta", headers={"X-Origin-Secret": "s3cret-shared-with-the-proxy"})
    assert ok.status_code == 200
    assert client.get("/", headers={"X-Origin-Secret": "s3cret-shared-with-the-proxy"}).status_code == 200


def test_probes_and_the_metrics_scrape_do_not_need_it(client, monkeypatch):
    monkeypatch.setattr(config, "ORIGIN_SECRET", "s3cret-shared-with-the-proxy")
    monkeypatch.setattr(config, "METRICS_TOKEN", "tok")
    assert client.get("/healthz").status_code == 200 and client.get("/livez").status_code == 200  # the kubelet does not go through the proxy
    assert client.get("/metrics", headers={"Authorization": "Bearer tok"}).status_code == 200  # Prometheus does not either (and has its own token)


def test_refusals_are_counted_and_do_not_reveal_why(client, monkeypatch):
    from app import metrics
    monkeypatch.setattr(config, "ORIGIN_SECRET", "s3cret-shared-with-the-proxy")
    monkeypatch.setattr(config, "METRICS_TOKEN", "tok")
    before = metrics.render()
    r = client.get("/api/meta")
    assert "secret" not in r.text.lower() and "proxy" not in r.text.lower()
    assert metrics.render() != before and "fabulist_origin_refused_total" in metrics.render()
