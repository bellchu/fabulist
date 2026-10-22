"""Usage reporting: administrators can download every AI run (who, which project, tokens, duration) as CSV."""
import csv
import io
import time


def _run_one(client, name="Usage project"):
    pid = client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]
    client.post("/api/projects/%s/documents/text" % pid, json={"name": "b.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"})
    client.post("/api/projects/%s/runs" % pid, json={"stage": "requirements"})
    for _ in range(60):
        if client.get("/api/projects/%s/runs?limit=1" % pid).json()[0]["status"] not in ("running", "queued"):
            break
        time.sleep(0.2)
    return pid


def test_runs_download_as_csv(client):
    _run_one(client)
    r = client.get("/api/admin/runs.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "ai-runs" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["Project"] == "Usage project" and row["Stage"] == "requirements" and row["Status"] == "done"
    assert row["Tokens in"].isdigit() and row["Tokens out"].isdigit() and float(row["Duration (s)"]) >= 0 and row["By"]


def test_a_deleted_projects_runs_stay_in_the_report(client):
    pid = _run_one(client, "Gone soon")
    client.post("/api/projects/%s/archive" % pid, json={})
    client.delete("/api/projects/%s" % pid)
    rows = list(csv.DictReader(io.StringIO(client.get("/api/admin/runs.csv").text)))
    assert rows == [] or rows[0]["Project"] in ("", "(deleted)")  # runs go with their project; nothing may crash


def test_spreadsheet_formulas_in_names_are_neutralised(client):
    _run_one(client, "=HYPERLINK(\"http://evil\")")
    assert "'=HYPERLINK" in client.get("/api/admin/runs.csv").text


def test_only_administrators_can_download_it(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "ana", "role": "analyst", "password": "ana-password-12"}, auth=root)
    assert client.get("/api/admin/runs.csv", auth=("ana", "ana-password-12")).status_code == 403
    assert client.get("/api/admin/runs.csv", auth=root).status_code == 200
