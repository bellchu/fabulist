"""The business domain is a field on the Generation settings page and it sticks. Run with scripts/ui-check-k8s.sh."""
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
    page = ctx.new_page()
    page.goto(BASE + "/#/admin/settings")
    field = page.get_by_label("Business domain")
    field.wait_for()
    check(field.input_value() == "medical product distribution", "it starts with the default (%r)" % field.input_value())
    field.fill("retail banking")
    page.get_by_role("button", name="Save settings").click()
    page.wait_for_selector("text=Settings saved")
    page.reload()
    page.get_by_label("Business domain").wait_for()
    check(page.get_by_label("Business domain").input_value() == "retail banking", "the saved value is there after a reload")
    ctx.request.put(BASE + "/api/admin/settings", data={"domain": ""}, headers=H)  # back to the default for the tests that follow
    browser.close()
sys.exit(1 if failures else 0)
