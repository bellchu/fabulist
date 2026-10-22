"""Edits on the Generation settings page are not lost by clicking another tab: the page asks first. Run with scripts/ui-check-k8s.sh."""
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
    page = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True).new_page()
    page.goto(BASE + "/#/admin/settings")
    temp = page.get_by_label("Temperature")
    temp.wait_for()
    tabs = page.get_by_role("link", name="Activity")

    tabs.click(); page.wait_for_function("location.hash.endsWith('/activity')")
    check(page.locator(".modal").count() == 0, "nothing edited: another tab opens at once")

    page.goto(BASE + "/#/admin/settings")
    temp = page.get_by_label("Temperature"); temp.wait_for()
    temp.fill("0.7")
    tabs.click(); page.wait_for_selector(".modal[role=alertdialog]")
    check(page.evaluate("location.hash").endswith("/settings"), "with an edit pending, the page stays where it is and asks")
    page.get_by_role("button", name="Cancel").last.click(); page.wait_for_timeout(300)
    check(page.get_by_label("Temperature").input_value() == "0.7" and page.evaluate("location.hash").endswith("/settings"), "Cancel keeps the page and the edit")

    tabs.click(); page.wait_for_selector(".modal[role=alertdialog]")
    page.get_by_role("button", name="Discard").click()
    page.wait_for_function("location.hash.endsWith('/activity')")
    check(True, "Discard goes on to the tab that was clicked")

    page.goto(BASE + "/#/admin/settings")
    temp = page.get_by_label("Temperature"); temp.wait_for()
    temp.fill("0.6")
    page.get_by_role("button", name="Save settings").click(); page.wait_for_selector("text=Settings saved")
    tabs.click(); page.wait_for_function("location.hash.endsWith('/activity')")
    check(page.locator(".modal").count() == 0, "after saving, leaving asks nothing")
    browser.close()
sys.exit(1 if failures else 0)
