"""The requirement mix legend lists the kinds that are there, not a row of zeros. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Mix", "platform": "generic"}, headers=H).json()["id"]
    for i in range(3):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability %d for buyers." % i, "type": "functional"}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "Orders over 10000 must be approved by a manager.", "type": "rule"}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/overview")
    page.wait_for_selector(".legend")
    legend = page.locator(".legend").inner_text()
    check("functional 3" in legend and "rule 1" in legend, "the kinds that are there are listed with their counts (%r)" % legend)
    check("business" not in legend and "constraint" not in legend and "non-functional" not in legend, "the kinds that are not there are not")
    browser.close()
sys.exit(1 if failures else 0)
