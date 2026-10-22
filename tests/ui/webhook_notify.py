"""The run-notification choice appears for webhooks only, is saved, and shows as saved. Run with scripts/ui-check-k8s.sh."""
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
    page.goto(BASE + "/#/admin/integrations")
    page.get_by_role("button", name="Add integration").first.click()
    page.wait_for_selector(".modal")
    box = page.get_by_label("Also tell this webhook when an AI run finishes", exact=False)
    check(not box.is_visible(), "a Jira integration (the default type) has no notification choice")
    page.get_by_label("Type").select_option("webhook")
    check(box.is_visible() and not box.is_checked(), "a webhook shows it, off by default")
    page.get_by_label("Display name").fill("Chat bridge")
    page.get_by_label("Base URL").fill("https://hooks.example.com/runs")
    box.check()
    page.locator(".modal-foot").get_by_role("button", name="Add integration").click()
    page.wait_for_selector("text=Integration saved")
    page.get_by_role("button", name="Edit").first.click()
    page.wait_for_selector(".modal")
    check(page.get_by_label("Also tell this webhook when an AI run finishes", exact=False).is_checked(), "reopened, the choice is still on")
    browser.close()
sys.exit(1 if failures else 0)
