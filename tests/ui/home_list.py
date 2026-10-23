"""The project list can be sorted by what people worked on last, and its cards say so. Run with scripts/ui-check-k8s.sh."""
import os
import sys
import time

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
    ids = [ctx.request.post(BASE + "/api/projects", data={"name": "Act-%02d" % i, "platform": "generic"}, headers=H).json()["id"] for i in range(9)]
    time.sleep(0.2)
    ctx.request.post(f"{BASE}/api/projects/{ids[0]}/requirements", data={"statement": "The system shall export invoices to CSV on request."}, headers=H)  # the oldest project, touched last
    page = ctx.new_page()
    page.goto(BASE + "/#/")
    page.get_by_label("Sort projects").wait_for()
    page.get_by_label("Sort projects").select_option("active")
    page.wait_for_timeout(400)
    first = page.locator(".project-card h3").first.inner_text()
    check(first == "Act-00", "sorted by recent activity, the project worked on last comes first (%r)" % first)
    check("Active" in page.locator(".project-card").first.inner_text(), "and the card says when it was active")
    page.get_by_label("Sort projects").select_option("new")
    page.wait_for_timeout(400)
    check(page.locator(".project-card h3").first.inner_text() != "Act-00", "newest first still sorts by creation")
    browser.close()
sys.exit(1 if failures else 0)
