"""Requirements that already exist in a spreadsheet (or came out of another Fabulist) can be imported from CSV."""
CSV = ("Requirement,Type,Epic,Section,Source ID,Actors,Status\n"
       "The system shall export invoices to CSV on request.,functional,Billing,Invoices,BR-1,Buyer;Accountant,approved\n"
       "Responses shall arrive within 2 seconds for 95% of requests.,non-functional,Platform,Speed,BR-2,,draft\n"
       "Orders over 10000 must be approved by a manager.,rule,Orders,Approvals,BR-3,Manager,\n")


def _project(client, name="Import target"):
    return client.post("/api/projects", json={"name": name, "platform": "generic"}).json()["id"]


def _import(client, pid, text, **extra):
    return client.post("/api/projects/%s/requirements/import" % pid, json=dict({"csv": text}, **extra))


def _reqs(client, pid):
    return client.get("/api/projects/%s/requirements" % pid).json()


def test_a_spreadsheet_is_imported_with_its_columns(client):
    pid = _project(client)
    r = _import(client, pid, CSV)
    assert r.status_code == 200 and r.json() == {"created": 3, "duplicates_skipped": 0}
    reqs = {x["source_id"]: x for x in _reqs(client, pid)}
    assert reqs["BR-1"]["statement"] == "The system shall export invoices to CSV on request."
    assert reqs["BR-1"]["type"] == "functional" and reqs["BR-1"]["epic"] == "Billing" and reqs["BR-1"]["section"] == "Invoices"
    assert reqs["BR-1"]["actors"] == ["Buyer", "Accountant"] and reqs["BR-1"]["status"] == "approved"
    assert reqs["BR-2"]["status"] == "draft" and reqs["BR-3"]["status"] == "draft", "no status means draft: people still review"
    assert reqs["BR-3"]["type"] == "rule" and reqs["BR-1"]["source_ref"], "every requirement says where it came from"
    assert [x["key"] for x in _reqs(client, pid)] == ["REQ-001", "REQ-002", "REQ-003"]


def test_what_fabulist_exports_it_can_import_into_another_project(client):
    a = _project(client, "Source")
    _import(client, a, CSV)
    exported = client.get("/api/projects/%s/export?format=requirements-csv" % a)
    assert exported.status_code == 200, exported.text
    b = _project(client, "Copy")
    r = _import(client, b, exported.text)
    assert r.status_code == 200 and r.json()["created"] == 3
    assert sorted(x["statement"] for x in _reqs(client, a)) == sorted(x["statement"] for x in _reqs(client, b))


def test_repeats_are_skipped_not_doubled(client):
    pid = _project(client)
    _import(client, pid, CSV)
    again = _import(client, pid, CSV + "THE SYSTEM SHALL EXPORT INVOICES TO CSV ON REQUEST!,functional,,,,,\n")
    assert again.json() == {"created": 0, "duplicates_skipped": 4}
    assert len(_reqs(client, pid)) == 3


def test_bad_rows_refuse_the_whole_file_and_name_the_lines(client):
    pid = _project(client)
    bad = "Requirement,Status\nThe system shall do something useful here.,approved\nshort,draft\nThe system shall log every sign-in attempt.,done\n"
    r = _import(client, pid, bad)
    assert r.status_code == 400 and "line 3" in r.json()["detail"] and "line 4" in r.json()["detail"]
    assert _reqs(client, pid) == [], "nothing is imported when anything is wrong"


def test_the_file_needs_a_statement_column_and_a_sensible_size(client):
    pid = _project(client)
    assert _import(client, pid, "Name,Colour\nx,y\n").status_code == 400
    assert _import(client, pid, "").status_code == 400
    big = "Requirement\n" + "\n".join("The system shall support capability number %d." % i for i in range(2001))
    r = _import(client, pid, big)
    assert r.status_code == 400 and "2000" in r.json()["detail"]


def test_it_is_audited_and_cannot_touch_an_archived_project(client):
    pid = _project(client)
    _import(client, pid, CSV)
    trail = client.get("/api/projects/%s/history" % pid).json()
    assert sum(1 for e in trail if e["action"] == "created") == 3 and any(e["action"] == "imported" for e in trail)
    client.post("/api/projects/%s/archive" % pid, json={})
    assert _import(client, pid, "Requirement\nThe system shall be impossible to add to now.\n").status_code == 409


def test_viewers_cannot_import(client, monkeypatch):
    from app import config, users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    users._cache.clear()
    root = ("root", "root-password-123")
    client.post("/api/admin/users", json={"username": "vic", "role": "viewer", "password": "vic-password-12"}, auth=root)
    pid = client.post("/api/projects", json={"name": "P", "platform": "generic"}, auth=root).json()["id"]
    r = client.post("/api/projects/%s/requirements/import" % pid, json={"csv": CSV}, auth=("vic", "vic-password-12"))
    assert r.status_code == 403


def _upload(client, pid, name, data, mime="application/octet-stream"):
    return client.post("/api/projects/%s/requirements/import-file" % pid, files={"file": (name, data, mime)})


def _workbook(sheets):
    import io
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_an_excel_sheet_is_imported(client):
    pid = _project(client)
    data = _workbook([("Notes", [["Nothing here"]]), ("Requirements", [["Requirement", "Type", "Status", "Source ID"],
                      ["The system shall export invoices to CSV on request.", "functional", "approved", "BR-1"],
                      ["Responses shall arrive within 2 seconds for 95% of requests.", "non-functional", "", "BR-2"]])])
    r = _upload(client, pid, "reqs.xlsx", data)
    assert r.status_code == 200 and r.json() == {"created": 2, "duplicates_skipped": 0}, r.text
    assert {x["source_id"]: x["status"] for x in _reqs(client, pid)} == {"BR-1": "approved", "BR-2": "draft"}


def test_the_first_sheet_is_used_when_none_is_called_requirements(client):
    pid = _project(client)
    r = _upload(client, pid, "any.xlsx", _workbook([("Sheet1", [["Statement"], ["The system shall log every sign-in attempt."]])]))
    assert r.status_code == 200 and r.json()["created"] == 1


def test_fabulists_excel_export_imports_back(client):
    a = _project(client, "Source")
    _import(client, a, CSV)
    book = client.get("/api/projects/%s/export?format=xlsx" % a)
    assert book.status_code == 200
    b = _project(client, "Copy")
    r = _upload(client, b, "export.xlsx", book.content)
    assert r.status_code == 200 and r.json()["created"] == 3, r.text


def test_csv_files_and_odd_files_through_the_file_endpoint(client):
    pid = _project(client)
    assert _upload(client, pid, "reqs.csv", CSV.encode("utf-8-sig")).json()["created"] == 3
    assert _upload(client, pid, "reqs.docx", b"x").status_code == 400
    assert _upload(client, pid, "broken.xlsx", b"not a workbook").status_code == 400
    assert _upload(client, pid, "empty.csv", b"").status_code == 400
