"""The compliance field of a requirement or story suggests the tags already used in the project, not only the built-in ones. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Suggest", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall log every payment attempt.", "compliance_tags": ["Corp-Policy-7"]}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall mask card numbers in reports."}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Mask cards"}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_role("button", name="Open REQ-002").click()
    page.wait_for_selector(".drawer")
    options = page.locator(".drawer datalist option").evaluate_all("els => els.map(e => e.value)")
    check("Corp-Policy-7" in options and "SOX" in options, "a requirement's compliance field offers the project's own tag and the built-in ones (%d options)" % len(options))
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.get_by_role("button", name="List").click()
    page.wait_for_selector("tr.clickable")
    page.get_by_role("button", name="Open US-001").click()
    page.wait_for_selector(".drawer")
    options = page.locator(".drawer datalist option").evaluate_all("els => els.map(e => e.value)")
    check("Corp-Policy-7" in options, "a story's compliance field offers it too")
    browser.close()
sys.exit(1 if failures else 0)
