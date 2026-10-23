"""Split one requirement into parts by hand from its drawer. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Split UI", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices to CSV and email them to the buyer."}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.locator("tr.clickable").first.click()
    page.get_by_role("button", name="Split…").click()
    page.wait_for_selector(".modal")
    page.get_by_label("Part 1").fill("The system shall export invoices to CSV on request.")
    page.get_by_label("Part 2").fill("The system shall email invoices to the buyer.")
    page.locator(".modal-foot").get_by_role("button", name="Split", exact=True).click()
    page.wait_for_selector("text=split; new: REQ-002")
    got = {r["key"]: r["statement"] for r in ctx.request.get(f"{BASE}/api/projects/{pid}/requirements").json()}
    check(got.get("REQ-001", "").endswith("CSV on request.") and got.get("REQ-002", "").endswith("to the buyer."), "the requirement is now two")
    browser.close()
sys.exit(1 if failures else 0)
