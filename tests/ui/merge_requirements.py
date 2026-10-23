"""Merge duplicate requirements from the bulk bar. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Merge UI", "platform": "generic"}, headers=H).json()["id"]
    for text in ("The system shall export invoices to CSV on request.", "Invoices shall be exportable as CSV when a user asks.", "The system shall email order confirmations to buyers."):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select REQ-001").check()
    check(page.get_by_role("button", name="Merge…").count() == 0, "one selected: no Merge offered")
    page.get_by_label("Select REQ-002").check()
    page.get_by_role("button", name="Merge…").click()
    page.wait_for_selector(".modal")
    check(page.locator(".modal input[type=radio]").count() == 2, "the dialog lists the two to choose the survivor from")
    page.locator(".modal-foot").get_by_role("button", name="Merge", exact=True).click()
    page.wait_for_selector("text=Merged REQ-002 into REQ-001")
    page.wait_for_timeout(600)
    check(page.locator("tr", has_text="REQ-002").locator("text=excluded").count() >= 1, "the other one now shows as excluded")
    browser.close()
sys.exit(1 if failures else 0)
