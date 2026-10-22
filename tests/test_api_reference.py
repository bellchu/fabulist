"""The API reference is served by the app itself (the stock Swagger page loads scripts from a public CDN, which the Content Security Policy blocks
and a locked-down network would not reach), and the OpenAPI document it renders is organised and described."""
import re


def test_the_docs_page_is_self_contained(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert re.findall(r'(?:src|href)="(https?://[^"]+)"', r.text) == [], "no resource may come from outside the app"
    assert "/static/js/docs.js" in r.text
    assert client.get("/redoc").status_code in (404, 307, 308), "the CDN-based alternative is gone"
    assert "'self'" in r.headers["content-security-policy"] and "unsafe-inline" not in r.headers["content-security-policy"], "and the policy was not loosened to make it work"


def test_the_openapi_document_groups_and_describes_every_operation(client):
    spec = client.get("/openapi.json").json()
    ops = [(path, method, op) for path, item in spec["paths"].items() for method, op in item.items()]
    assert len(ops) > 80
    untagged = [(m.upper(), p) for p, m, op in ops if not op.get("tags")]
    assert untagged == [], "every operation belongs to a group: %s" % untagged[:5]
    tags = {t["name"] for t in spec["tags"]}
    assert {"Projects", "Requirements", "Stories", "Documents", "Runs", "Administration", "Account"} <= tags, tags
    assert all(op.get("summary") and op["summary"][0].isupper() and "_" not in op["summary"] for _p, _m, op in ops), "summaries are sentences, not function names"
    assert all(op["summary"] == op["summary"][:1] + op["summary"][1:].lower() for _p, _m, op in ops), "in sentence case, not Title Case"
    by_path = {(m.upper(), p): op for p, m, op in ops}
    assert by_path[("POST", "/api/projects/{pid}/runs")]["tags"] == ["Runs"] and by_path[("GET", "/api/projects")]["tags"] == ["Projects"]
    assert by_path[("GET", "/api/admin/users")]["tags"] == ["Administration"] and by_path[("GET", "/api/me/tokens")]["tags"] == ["Account"]
    assert "Bearer" in spec["info"]["description"] and "X-Requested-With" in spec["info"]["description"], "the intro says how to authenticate"
