"""Stories that already exist in a spreadsheet (a Jira export, another Fabulist) can be imported, with the same care as requirements."""

CSV = ("Summary,Description,Epic,Feature,Persona,Priority,Story points,Acceptance criteria,Requirement IDs,Status\n"
       'Export invoices,As a buyer I want a CSV of my invoices so that I can reconcile them.,Billing,Invoices,Buyer,High,3,"Given invoices exist\nWhen I export\nThen I get a CSV",REQ-001;REQ-002,approved\n'
       "Confirm orders quickly,As a buyer I want confirmation within 3 seconds so that I can move on.,Ordering,Confirmation,Buyer,medium,5,,REQ-003,\n"
       "Audit price changes,As finance I want every price change logged so that audits are easy.,Billing,Pricing,Finance,Low,,,,draft\n")


def _project(client, name="Story import"):
    return client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]


def _import(client, pid, text):
    return client.post("/api/projects/%s/stories/import" % pid, json={"csv": text})


def _stories(client, pid):
    return client.get("/api/projects/%s/stories" % pid).json()


def test_a_spreadsheet_of_stories_is_imported_with_its_columns(client):
    pid = _project(client)
    r = _import(client, pid, CSV)
    assert r.status_code == 200 and r.json() == {"created": 3, "duplicates_skipped": 0, "statuses_not_recognised": 0}, r.text
    s = {x["title"]: x for x in _stories(client, pid)}
    first = s["Export invoices"]
    assert first["epic"] == "Billing" and first["feature"] == "Invoices" and first["persona"] == "Buyer"
    assert first["priority"] == "High" and first["story_points"] == 3 and first["status"] == "approved"
    assert first["acceptance_criteria"] == ["Given invoices exist", "When I export", "Then I get a CSV"]
    assert first["requirement_ids"] == ["REQ-001", "REQ-002"]
    assert s["Confirm orders quickly"]["priority"] == "Medium" and s["Confirm orders quickly"]["status"] == "draft"
    assert s["Audit price changes"]["story_points"] is None
    assert [x["key"] for x in _stories(client, pid)] == ["US-001", "US-002", "US-003"]


def test_what_fabulist_exports_it_imports_into_another_project(client):
    a = _project(client, "Source")
    _import(client, a, CSV)
    exported = client.get("/api/projects/%s/export?format=stories-csv" % a)
    assert exported.status_code == 200
    b = _project(client, "Copy")
    r = _import(client, b, exported.text)
    assert r.status_code == 200 and r.json()["created"] == 3, r.text
    assert sorted(x["title"] for x in _stories(client, a)) == sorted(x["title"] for x in _stories(client, b))


def test_statuses_from_another_tool_come_in_as_drafts_and_are_counted(client):
    """A Jira export always has a Status column ("To Do", "Done"...). Those are not Fabulist's review states: the stories arrive as drafts to be reviewed."""
    pid = _project(client)
    jira = ("Summary,Status\nFirst story from Jira,To Do\nSecond story from Jira,In Progress\nThird story from Jira,Done\n"
            "Fourth story from Jira,approved\nFifth story from Jira,\n")
    r = _import(client, pid, jira)
    assert r.status_code == 200 and r.json() == {"created": 5, "duplicates_skipped": 0, "statuses_not_recognised": 3}, r.text
    assert {x["title"]: x["status"] for x in _stories(client, pid)} == {
        "First story from Jira": "draft", "Second story from Jira": "draft", "Third story from Jira": "draft",
        "Fourth story from Jira": "approved", "Fifth story from Jira": "draft"}


def test_repeats_are_skipped_and_numbering_continues(client):
    pid = _project(client)
    _import(client, pid, CSV)
    again = _import(client, pid, CSV + "EXPORT INVOICES!,As a buyer I want something else entirely here.,,,,,,,,\nA new story to add,As a user I want to add this so that it counts.,,,,,,,,\n")
    assert again.json() == {"created": 1, "duplicates_skipped": 4, "statuses_not_recognised": 0}
    assert [x["key"] for x in _stories(client, pid)][-1] == "US-004"


def test_a_row_without_a_title_refuses_the_whole_file(client):
    pid = _project(client)
    bad = "Title,Status\nA fine story title here,approved\n,draft\nAnother fine title here,draft\n"
    r = _import(client, pid, bad)
    assert r.status_code == 400 and "line 3" in r.json()["detail"] and "title" in r.json()["detail"]
    assert _stories(client, pid) == []
    assert _import(client, pid, "Name,Colour\nx,y\n").status_code == 400


def test_excel_and_files_work_through_the_file_endpoint(client):
    import io
    from openpyxl import Workbook
    pid = _project(client)
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Stories")
    ws.append(["Title", "Narrative", "Story Points"])
    ws.append(["Reset a password", "As a user I want to reset my password so that I can get back in.", 2])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/projects/%s/stories/import-file" % pid, files={"file": ("stories.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200 and r.json()["created"] == 1, r.text
    assert _stories(client, pid)[0]["story_points"] == 2
    assert client.post("/api/projects/%s/stories/import-file" % pid, files={"file": ("stories.csv", CSV.encode(), "text/csv")}).json()["created"] == 3
    assert client.post("/api/projects/%s/stories/import-file" % pid, files={"file": ("x.pdf", b"x", "application/pdf")}).status_code == 400


def test_it_is_audited_and_respects_archive_and_roles(client, monkeypatch):
    pid = _project(client)
    _import(client, pid, CSV)
    trail = client.get("/api/projects/%s/history?limit=100" % pid).json()
    assert sum(1 for e in trail if e["action"] == "created" and e["item_type"] == "story") == 3
    assert any(e["action"] == "imported" and "stories" in e["summary"] for e in trail)
    client.post("/api/projects/%s/archive" % pid, json={})
    assert _import(client, pid, "Title\nA story that must be refused now\n").status_code == 409
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "vic", "role": "viewer", "password": "vic-password-12"}, auth=root)
    other = client.post("/api/projects", json={"name": "P", "platform": "generic"}, auth=root).json()["id"]
    assert client.post("/api/projects/%s/stories/import" % other, json={"csv": CSV}, auth=("vic", "vic-password-12")).status_code == 403
