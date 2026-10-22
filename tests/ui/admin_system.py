"""The admin Overview shows how the installation itself is doing. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True).new_page()
    page.goto(BASE + "/#/admin/overview")
    page.wait_for_selector("[aria-label=System]")
    text = page.locator("[aria-label=System]").inner_text()
    for label in ("Version", "Database", "AI runs now", "Memory"):
        check(label in text, "the system row has %s" % label)
    check("MiB" in text and "running" in text and "waiting" in text and "up " in text, "with readable sizes, counts and uptime (%r)" % text.replace("\n", " | "))
    browser.close()
sys.exit(1 if failures else 0)
