"""An administrator can score a provider on the built-in sample BRD from the providers page. Run with scripts/ui-check-k8s.sh."""
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
    page.goto(BASE + "/#/admin/providers")
    row = page.locator(".provider-row", has_text="Offline heuristic").first
    row.get_by_role("button", name="Evaluate").click()
    page.wait_for_selector("text=Found 12 of 12")
    text = page.locator(".content").inner_text()
    check("kept the open item (TBD) as it was" in text, "the verdict says how many numbers were found and that the TBD survived")
    check(page.locator(".callout.ok").count() >= 1, "a full score is shown as good")
    browser.close()
sys.exit(1 if failures else 0)
