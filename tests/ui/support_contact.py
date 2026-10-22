"""The footer shows the support contact when one is configured. Run with scripts/ui-check-k8s.sh (the throwaway app sets SUPPORT_CONTACT)."""
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
    page = browser.new_context(viewport={"width": 1100, "height": 800}, bypass_csp=True).new_page()
    page.goto(BASE + "/#/")
    page.wait_for_selector(".app-footer")
    text = page.locator(".app-footer").inner_text()
    check("Fabulist" in text and "Support: helpdesk@ui-check.example" in text, "the footer names the version and the support contact (%r)" % text)
    browser.close()
sys.exit(1 if failures else 0)
