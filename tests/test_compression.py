"""Big JSON and static files are compressed for clients that accept it (an ingress in front may not do it)."""


def test_large_json_is_gzipped_when_accepted(client):
    pid = client.post("/api/projects", json={"name": "Big"}).json()["id"]
    for i in range(60):
        client.post("/api/projects/%s/requirements" % pid, json={"statement": "The system shall support capability %d for the buyer." % i})
    plain = client.get("/api/projects/%s/requirements" % pid, headers={"Accept-Encoding": "identity"})
    packed = client.get("/api/projects/%s/requirements" % pid, headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in plain.headers
    assert packed.headers["content-encoding"] == "gzip"
    assert packed.json() == plain.json()
    assert int(packed.headers.get("content-length", len(packed.content))) < len(plain.content) / 2


def test_small_responses_are_left_alone(client):
    r = client.get("/healthz", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers


def test_archive_download_is_still_valid_when_compressed(client):
    pid = client.post("/api/projects", json={"name": "Arch"}).json()["id"]
    r = client.get("/api/projects/%s/archive.json" % pid, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.json()["project"]["name"] == "Arch"
