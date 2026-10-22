"""Keyboard-only use: open a requirement from its ID, Tab stays inside the drawer, Escape returns focus to where the user was,
sort headers and checkboxes work from the keyboard. Run with scripts/ui-check-k8s.sh."""
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
    api, page = ctx.request, ctx.new_page()
    pid = api.post(BASE + "/api/projects", data={"name": "Keys", "platform": "generic"}, headers=H).json()["id"]
    for i in range(5):
        api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall do thing %d." % i}, headers=H)
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.wait_for_selector("tr.clickable")
    inside = lambda: page.evaluate("!!document.activeElement.closest('.drawer')")
    focused = lambda: page.evaluate("document.activeElement.getAttribute('aria-label') || document.activeElement.innerText")

    # open a requirement from the keyboard: focus its ID button and press Enter
    page.get_by_role("button", name="Open REQ-002").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer")
    page.wait_for_function("document.activeElement.closest('.drawer')")
    check(inside(), "Enter on a row's ID opens the drawer and moves focus into it")

    # Tab and Shift+Tab never leave the drawer
    escaped = False
    for _ in range(40):
        page.keyboard.press("Tab")
        escaped = escaped or not inside()
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        escaped = escaped or not inside()
    check(not escaped, "Tab and Shift+Tab stay inside the drawer")

    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached")
    check(focused() == "Open REQ-002", f"Escape closes the drawer and returns focus to the row's ID button ({focused()})")

    # sorting from the keyboard
    page.get_by_role("button", name="ID").focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    check(page.locator("th", has=page.get_by_role("button", name="ID")).get_attribute("aria-sort") == "descending", "Enter on a sort header sorts it")

    # selection from the keyboard
    page.get_by_role("checkbox", name="Select REQ-001").focus()
    page.keyboard.press("Space")
    check(page.locator(".bulkbar").count() == 1 and "1 selected" in page.locator(".bulkbar").inner_text(), "Space on a row checkbox selects it and shows the bulk bar")
    page.get_by_role("button", name="Clear").focus()
    page.keyboard.press("Enter")
    check(page.locator(".bulkbar").count() == 0, "the bulk bar's Clear works from the keyboard")

    # a confirmation dialog is reachable and dismissible by keyboard
    page.get_by_role("checkbox", name="Select all").focus()
    page.keyboard.press("Space")
    check(page.locator(".bulkbar").count() == 1, "Select all works from the keyboard")

    # dialogs: a form dialog and a confirmation, both by keyboard
    api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall be approved.", "status": "approved"}, headers=H)
    page.goto(f"{BASE}/#/p/{pid}/export")
    page.reload()
    page.get_by_role("button", name="Create baseline").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".modal")
    page.wait_for_function("document.activeElement.closest('.modal')")
    check(True, "dialog: opening it from the keyboard moves focus inside")
    left = False
    for _ in range(25):
        page.keyboard.press("Tab")
        left = left or not page.evaluate("!!document.activeElement.closest('.modal')")
    check(not left, "dialog: Tab cycles inside the dialog")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")
    check(page.evaluate("document.activeElement.innerText").strip().endswith("Create baseline"), "dialog: Escape returns focus to the button that opened it")

    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.get_by_role("button", name="Open REQ-001").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer")
    page.get_by_role("button", name="Delete", exact=True).focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".modal")
    page.wait_for_function("document.activeElement.closest('.modal')")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")
    check(page.locator(".drawer").count() == 1 and page.get_by_role("button", name="Delete", exact=True).count() == 1, "confirm: Escape cancels and leaves the drawer open")
    check(page.evaluate("document.activeElement.innerText").strip().endswith("Delete"), "confirm: focus returns to the Delete button")
    page.get_by_role("button", name="Delete", exact=True).focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".modal")
    page.get_by_role("button", name="Delete", exact=True).last.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer", state="detached")
    page.wait_for_timeout(500)
    check(page.get_by_role("button", name="Open REQ-001").count() == 0, "confirm: Enter on the confirm button deletes the requirement")
    browser.close()
sys.exit(1 if failures else 0)
