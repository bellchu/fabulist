"""The review drawer's action buttons fit on one row at laptop width (no lone Delete on a second line). Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
ROWS = """(() => { const f = document.querySelector('.drawer-foot');
  return new Set([...f.querySelectorAll('button')].filter((b) => b.offsetParent).map((b) => Math.round((b.getBoundingClientRect().top + b.getBoundingClientRect().height / 2) / 20))).size; })()"""
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Footer", "platform": "generic"}, headers=H).json()["id"]
    for text in ("The system shall log every payment attempt.", "The system shall mask card numbers in reports."):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Audit payments"}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Mask cards"}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_role("button", name="Open REQ-001").click()
    page.wait_for_selector(".drawer-foot")
    page.wait_for_timeout(400)
    rows = page.evaluate(ROWS)
    check(rows == 1, f"requirement drawer: the buttons are on one row at 1280px ({rows} rows)")
    check(page.get_by_role("button", name="Delete").count() == 1, "Delete is still there, and named, for screen readers")
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.get_by_role("button", name="List").click()
    page.wait_for_selector("tr.clickable")
    page.get_by_role("button", name="Open US-001").click()
    page.wait_for_selector(".drawer-foot")
    page.wait_for_timeout(400)
    rows = page.evaluate(ROWS)
    check(rows == 1, f"story drawer: the buttons are on one row at 1280px ({rows} rows)")
    browser.close()
sys.exit(1 if failures else 0)
