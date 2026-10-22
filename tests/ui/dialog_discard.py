"""A form dialog does not throw away what someone typed on a stray click outside it or an Escape: it asks first. An untouched dialog and the
Cancel button close at once. Run with scripts/ui-check-k8s.sh."""
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
    page.goto(BASE + "/#/")
    new = page.get_by_role("button", name="New", exact=True)
    new.wait_for()
    modals = lambda: page.locator(".modal").count()
    outside = lambda: page.mouse.click(5, 400)  # on the dimmed backdrop, left of the dialog

    new.click(); page.wait_for_selector(".modal")
    outside(); page.wait_for_timeout(300)
    check(modals() == 0, "an untouched dialog closes on a click outside")

    new.click(); page.wait_for_selector(".modal")
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    check(modals() == 0, "an untouched dialog closes on Escape")

    new.click(); page.wait_for_selector(".modal")
    page.get_by_label("Name").fill("Half-typed programme")
    outside(); page.wait_for_selector(".modal[role=alertdialog]")
    check(page.locator(".modal[role=alertdialog]").inner_text().count("Discard") >= 1, "a click outside a dialog with typing in it asks before discarding")
    page.get_by_role("button", name="Cancel").last.click(); page.wait_for_timeout(300)
    check(modals() == 1 and page.get_by_label("Name").input_value() == "Half-typed programme", "answering Cancel keeps the dialog and what was typed")

    page.keyboard.press("Escape"); page.wait_for_selector(".modal[role=alertdialog]")
    page.get_by_role("button", name="Discard").click(); page.wait_for_timeout(300)
    check(modals() == 0, "Escape asks too, and Discard closes the dialog")

    new.click(); page.wait_for_selector(".modal")
    page.get_by_label("Name").fill("Typed then cancelled")
    page.locator(".modal-foot").get_by_role("button", name="Cancel").click(); page.wait_for_timeout(300)
    check(modals() == 0, "the dialog's own Cancel button closes at once, without asking")
    browser.close()
sys.exit(1 if failures else 0)
