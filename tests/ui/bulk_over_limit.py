"""Selecting every requirement of a big project and changing them at once works even past the 2,000 ids one request may carry. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Over the limit", "platform": "generic"}, headers=H).json()["id"]
    for start in (0, 1100):
        rows = "".join("The system shall support scannable capability number %d for buyers.\n" % n for n in range(start, start + 1100))
        r = ctx.request.post(f"{BASE}/api/projects/{pid}/requirements/import", data={"csv": "Requirement\n" + rows}, headers=H)
        check(r.ok, "imported a batch of 1100 requirements")
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select all 2,200 matching").check()
    page.get_by_role("button", name="Tag…").click()
    page.wait_for_selector(".modal")
    page.get_by_label("Compliance tag").fill("SOX")
    page.locator(".modal-foot").get_by_role("button", name="Apply").click()
    page.wait_for_selector("text=2200 requirements updated", timeout=60000)
    tagged = [r for r in ctx.request.get(f"{BASE}/api/projects/{pid}/requirements").json() if r["compliance_tags"] == ["SOX"]]
    check(len(tagged) == 2200, "all 2,200 carry the tag (%d)" % len(tagged))
    browser.close()
sys.exit(1 if failures else 0)
