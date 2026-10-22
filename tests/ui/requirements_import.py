"""Import requirements from a CSV file on the Requirements tab. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
failures = []
GOOD = b"Requirement,Type,Status\nThe system shall export invoices to CSV on request.,functional,approved\nOrders over 10000 must be approved by a manager.,rule,\n"
BAD = b"Requirement\nshort\n"


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "CSV import", "platform": "generic"}, headers=H).json()["id"]
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.get_by_role("button", name="Import file").wait_for()
    file = page.get_by_label("Requirements file (CSV or Excel)")

    file.set_input_files({"name": "reqs.csv", "mimeType": "text/csv", "buffer": GOOD})
    page.wait_for_selector("text=Imported 2 requirements")
    page.wait_for_selector("tr.clickable")
    check(page.locator("tr.clickable").count() == 2, "the two rows appear in the table")
    check("1 of 2 approved" in page.locator(".toolbar").inner_text(), "the status column was used (1 of 2 approved)")

    file.set_input_files({"name": "again.csv", "mimeType": "text/csv", "buffer": GOOD})
    page.wait_for_selector("text=2 already there, skipped")
    page.wait_for_timeout(600)  # the table is redrawn after the notice: count it once it has settled
    page.wait_for_selector("tr.clickable")
    check(page.locator("tr.clickable").count() == 2, "importing the same file again adds nothing")

    import io
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Requirements"
    ws.append(["Requirement", "Type"]); ws.append(["Every export shall carry the date it was made.", "constraint"])
    buf = io.BytesIO(); wb.save(buf)
    file.set_input_files({"name": "reqs.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": buf.getvalue()})
    page.wait_for_selector("text=Imported 1 requirement")
    page.wait_for_function("document.querySelectorAll('tr.clickable').length === 3", timeout=10000)
    check(True, "an Excel workbook imports too")

    file.set_input_files({"name": "bad.csv", "mimeType": "text/csv", "buffer": BAD})
    page.wait_for_selector("text=Nothing was imported")
    check("line 2" in page.locator(".toasts").inner_text(), "a bad file says which line is wrong")
    browser.close()
sys.exit(1 if failures else 0)
