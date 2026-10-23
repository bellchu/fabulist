"""Set one field on many requirements at once from the bulk bar. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Bulk set", "platform": "generic"}, headers=H).json()["id"]
    for i in range(3):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support groupable capability %d." % i, "section": "Old area"}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select REQ-001").check()
    page.get_by_label("Select REQ-002").check()
    page.get_by_role("button", name="Set…").click()
    page.get_by_label("Field").select_option("section")
    page.get_by_label("Value").fill("New area")
    page.locator(".modal-foot").get_by_role("button", name="Apply").click()
    page.wait_for_selector("text=2 requirements updated")
    page.wait_for_timeout(600)
    check(page.locator("td", has_text="New area").count() == 2 and page.locator("td", has_text="Old area").count() == 1, "two rows moved to the new section, the third did not")

    page.get_by_label("Select REQ-003").check()
    page.get_by_role("button", name="Set…").click()
    page.get_by_label("Field").select_option("type")
    page.get_by_label("Value").select_option("rule")
    page.locator(".modal-foot").get_by_role("button", name="Apply").click()
    page.wait_for_selector("text=1 requirement updated")
    page.wait_for_timeout(600)
    check(page.locator("tr", has_text="REQ-003").locator("text=rule").count() >= 1, "the Type field offers the real types and applies one")
    browser.close()
sys.exit(1 if failures else 0)
