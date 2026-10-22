"""A table under a card heading has air between them (the Sources tab's Documents card had none). Run with scripts/ui-check-k8s.sh."""
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Spacing", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{pid}/documents/text", data={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices to CSV on request."}, headers=H)
    page = ctx.new_page()
    gap = "(sel) => { const card = [...document.querySelectorAll('.card')].find((c) => c.querySelector('.card-head h3')?.textContent.trim() === sel); " \
          "const head = card.querySelector('.card-head').getBoundingClientRect(), table = card.querySelector('thead').getBoundingClientRect(); return table.top - head.bottom; }"
    page.goto(f"{BASE}/#/p/{pid}/sources")
    page.wait_for_selector("text=brd.md")
    g = page.evaluate(gap, "Documents")
    check(g >= 8, "Sources > Documents: %.0f px between the heading and the table" % g)
    browser.close()
sys.exit(1 if failures else 0)
