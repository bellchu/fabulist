"""The bulk bar has grown to many buttons; on a phone it must wrap, not push the page sideways. Run with scripts/ui-check-k8s.sh."""
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
    ctx = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Phone bulk", "platform": "generic"}, headers=H).json()["id"]
    for text in ("The system shall export invoices to CSV on request.", "The system shall email order confirmations to buyers."):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text}, headers=H)
    page = ctx.new_page()
    for tab, noun in (("requirements", "REQ"),):
        page.goto(f"{BASE}/#/p/{pid}/{tab}")
        page.wait_for_selector("tr.clickable")
        page.get_by_label(f"Select {noun}-001").check()
        page.get_by_label(f"Select {noun}-002").check()
        page.wait_for_selector(".bulkbar")
        page.wait_for_timeout(300)
        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(over <= 0, f"{tab}: with the bulk bar open the page does not scroll sideways ({over}px over)")
        bar = page.locator(".bulkbar").bounding_box()
        check(bar["x"] >= 0 and bar["x"] + bar["width"] <= 376, f"{tab}: the bulk bar fits the screen")
    browser.close()
sys.exit(1 if failures else 0)
