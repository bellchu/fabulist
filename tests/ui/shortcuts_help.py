"""'?' lists the keyboard shortcuts, but never while typing. Run with scripts/ui-check-k8s.sh."""
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
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    page.goto(BASE + "/#/")
    page.wait_for_selector(".page-head")
    page.get_by_label("Search projects, requirements and stories").fill("a?b")
    check(page.locator(".modal").count() == 0, "typing a question mark in the search box does not open the list")
    page.get_by_label("Search projects, requirements and stories").fill("")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("?")
    page.wait_for_selector(".modal")
    check(page.get_by_role("heading", name="Keyboard shortcuts").count() == 1, "'?' opens the shortcuts list")
    check(page.locator(".modal kbd").count() >= 8, "it shows the keys")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('.modal')")
    check(True, "Esc closes it")
    page.get_by_role("button", name="Keyboard shortcuts").click()
    page.wait_for_selector(".modal")
    check(page.get_by_role("heading", name="Keyboard shortcuts").count() == 1, "the footer link opens the same list, for people without a keyboard shortcut habit")
    browser.close()
sys.exit(1 if failures else 0)
