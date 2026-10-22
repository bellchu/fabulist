"""Pressing / jumps to the search box from anywhere on the page, but never while typing somewhere else. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Slash", "platform": "generic"}, headers=H).json()["id"]
    for i in range(3):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability %d for buyers." % i}, headers=H)
    page = ctx.new_page()
    in_search = lambda: page.evaluate("document.activeElement === document.querySelector('.global-search input')")  # noqa: E731

    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.locator("h1").first.click()
    page.keyboard.press("/")
    check(in_search(), "/ moves the focus to the search box")
    check(page.locator(".global-search input").input_value() == "", "and does not type a slash into it")

    page.get_by_label("Search requirements").click()
    page.keyboard.type("a/b")
    check(page.get_by_label("Search requirements").input_value() == "a/b" and not in_search(), "while typing in another field, / is just a character")

    page.keyboard.press("Escape")
    page.get_by_role("button", name="Open REQ-001").click()
    page.wait_for_selector(".drawer")
    page.locator(".drawer textarea").first.click()
    page.keyboard.type("x/y")
    check(not in_search(), "in a review drawer, / is just a character too")
    check(page.locator(".global-search input").get_attribute("aria-keyshortcuts") == "/", "the search box declares its shortcut to assistive technology")
    browser.close()
sys.exit(1 if failures else 0)
