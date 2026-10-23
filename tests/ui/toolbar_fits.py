"""The list toolbars hold their filters and actions on one line at laptop width. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Toolbar", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Audit payments", "compliance_tags": ["SOX"]}, headers=H)
    page = ctx.new_page()
    for tab, wait in (("requirements", "tr.clickable"), ("stories", "text=Audit payments")):
        page.goto(f"{BASE}/#/p/{pid}/{tab}")
        page.wait_for_selector(wait)
        page.wait_for_timeout(300)
        rows = page.evaluate("""(() => { const t = [...document.querySelectorAll('.toolbar')].pop();
          return new Set([...t.children].filter((c) => c.offsetParent).map((c) => Math.round((c.getBoundingClientRect().top + c.getBoundingClientRect().height / 2) / 20))).size; })()""")
        check(rows == 1, f"{tab}: the toolbar is one line at 1280px ({rows} lines)")
    browser.close()
sys.exit(1 if failures else 0)
