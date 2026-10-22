"""Import stories from a file on the Stories tab, from the empty state and from the toolbar. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
failures = []
GOOD = b"Summary,Description,Epic,Story points\nExport invoices,As a buyer I want a CSV so that I can reconcile.,Billing,3\nConfirm orders,As a buyer I want confirmation so that I can move on.,Ordering,5\n"
MORE = b"Title\nReset a password\n"


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Story import", "platform": "generic"}, headers=H).json()["id"]
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/stories")
    file = page.get_by_label("Stories file (CSV or Excel)")
    file.wait_for(state="attached")
    file.set_input_files({"name": "stories.csv", "mimeType": "text/csv", "buffer": GOOD})
    page.wait_for_selector("text=Imported 2 stories")
    page.wait_for_function("document.body.innerText.includes('US-001') && document.body.innerText.includes('Export invoices')", timeout=10000)
    check(True, "from the empty state: the stories appear on the map")
    page.get_by_label("Stories file (CSV or Excel)").set_input_files({"name": "more.csv", "mimeType": "text/csv", "buffer": MORE})
    page.wait_for_selector("text=Imported 1 story")
    page.wait_for_function("document.body.innerText.includes('Reset a password')", timeout=10000)
    check(True, "from the toolbar of a project that has stories")
    browser.close()
sys.exit(1 if failures else 0)
