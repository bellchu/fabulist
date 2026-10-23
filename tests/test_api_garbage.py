"""No request, however malformed, may make the server answer 500: every operation in the API description is called with nonsense bodies, parameters and
identifiers, and a 500 (or a crash) fails the test. A 4xx is the right answer to nonsense."""
import json
import re


BODIES = [None, {}, [], "text", 12345, {"x": 1}, {"statement": 5, "status": [], "title": {}, "name": None, "csv": 7, "ids": "no", "rev": "x"},
          {"ids": [None, 1, {}], "status": "approved"}, {"stage": "nope", "scope": 3, "documents": [], "stories_for": {}},
          {"name": "x" * 5000, "text": "y" * 50, "platform": "?"}, {"query": 5, "question": [], "limit": "ten"}, {"username": 5, "role": [], "password": {}},
          {"from": 5, "to": []}, {"keep": 5, "merge": "no"}, {"keep": "a", "merge": ["a", None, 3]}, {"statements": ["a", 5, None]}, {"statements": []},
          {"ids": ["x"], "tag": 5, "remove": "yes"}, {"ids": ["x"], "tag": "x" * 500}, {"ids": ["x"], "field": 5, "value": []}, {"ids": ["x"], "assignee": {}}]
PARAMS = [{}, {"limit": "abc"}, {"limit": "-5"}, {"limit": "99999999999999999999"}, {"before": "x"}, {"format": "../../etc/passwd"}, {"scope": "?"},
          {"q": "%" * 50}, {"archived": "maybe"}, {"stage": "\x00"}, {"mode": 1}]
SKIP = {("POST", "/api/projects/{pid}/documents")}  # multipart upload: covered by the upload tests


def _fill(path, ids):
    return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), "nonexistent"), path)


def test_nothing_the_api_is_sent_makes_it_answer_500(client):
    spec = client.get("/openapi.json").json()["paths"]
    pid = client.post("/api/projects", json={"name": "Garbage", "platform": "generic"}).json()["id"]
    rid = client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall export invoices to CSV."}).json()["id"]
    sid = client.post("/api/projects/%s/stories" % pid, json={"title": "Export invoices", "narrative": "As a buyer I want a CSV."}).json()["id"]
    ids = {"pid": pid, "rid": rid, "iid": rid, "sid": sid}
    bad = []
    calls = 0
    for path, ops in sorted(spec.items()):
        for method in ops:
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE") or (method.upper(), path) in SKIP:
                continue
            if path in ("/api/projects/{pid}/archive", "/api/admin/reindex") and method == "post":
                continue
            for k, body in enumerate(BODIES):
                params = PARAMS[k % len(PARAMS)]
                real = _fill(path, ids if k % 3 else {})  # a third of the calls use identifiers that do not exist
                kwargs = {"params": params}
                if method.upper() != "GET":
                    kwargs["content"] = json.dumps(body) if body is not None else b""
                    kwargs["headers"] = {"Content-Type": "application/json"}
                try:
                    r = client.request(method.upper(), real, **kwargs)
                except Exception as exc:  # noqa: BLE001 - a crash is a failure too
                    bad.append("%s %s body=%r -> raised %r" % (method.upper(), real, body, exc))
                    continue
                calls += 1
                if r.status_code >= 500 and r.status_code != 503:
                    bad.append("%s %s body=%.60r params=%s -> %d %.80s" % (method.upper(), real, body, params, r.status_code, r.text))
    assert calls > 400, calls
    assert not bad, "\n".join(bad[:25])


def test_garbage_files_never_make_an_upload_answer_500(client):
    pid = client.post("/api/projects", json={"name": "Files", "platform": "generic"}).json()["id"]
    blobs = [b"", b"\x00" * 100, b"PK\x03\x04 not really a zip", b"%PDF-1.7 truncated", bytes(range(256)) * 40, "ünïcödé \udcff".encode("utf-8", "replace")]
    bad = []
    for ext in (".docx", ".pdf", ".xlsx", ".csv", ".md", ".txt", ".exe", "", ".DOCX", ".tar.gz"):
        for blob in blobs:
            for url in ("/api/projects/%s/documents" % pid, "/api/projects/%s/requirements/import-file" % pid):
                r = client.post(url, files={"file": ("sample" + ext, blob, "application/octet-stream")})
                if r.status_code >= 500:
                    bad.append("%s sample%s %r -> %d %.80s" % (url.split("/")[-1], ext, blob[:12], r.status_code, r.text))
    assert not bad, "\n".join(bad[:15])
