"""Browser-side protections that come from response headers, on every kind of response."""


def test_every_response_carries_the_protective_headers(client):
    for path in ("/", "/api/meta", "/static/app.css", "/docs", "/healthz", "/api/projects/nope", "/nothing-here"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY", path
        assert h["referrer-policy"] == "no-referrer", path
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"], path
        assert h["cross-origin-opener-policy"] == "same-origin", path
        assert h["cross-origin-resource-policy"] == "same-origin", path
        assert "camera=()" in h["permissions-policy"], path


def test_the_content_security_policy_allows_no_inline_script_and_no_plugins(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0] and "unsafe-eval" not in csp
    assert "object-src 'none'" in csp


def test_a_browser_without_javascript_is_told_why_the_page_is_empty(client):
    html = client.get("/").text
    assert "<noscript>" in html and "needs JavaScript" in html
    assert 'style="' not in html, "inline style attributes are blocked by the Content Security Policy: use a class"
    assert ".noscript-note" in client.get("/static/app.css").text
