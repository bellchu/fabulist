"""The audit tab narrows to one item by its key. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Trail UI", "platform": "generic"}, headers=H).json()["id"]
    for text in ("The system shall log every payment attempt.", "The system shall mask card numbers in reports."):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/audit")
    page.wait_for_selector("text=REQ-001")
    page.get_by_label("Filter by item key").fill("req-002")
    page.wait_for_selector("text=1 entry")
    check("REQ-002" in page.inner_text(".card-body") and "REQ-001" not in page.inner_text(".card-body"), "only the chosen item's entries are listed")
    browser.close()
sys.exit(1 if failures else 0)
