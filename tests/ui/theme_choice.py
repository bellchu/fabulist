"""The Account page lets people choose light, dark or follow their system, and the choice sticks. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def dark(page):
    return page.evaluate("(() => { const c = getComputedStyle(document.body).backgroundColor.match(/\\d+/g).map(Number); return (c[0] + c[1] + c[2]) / 3 < 100; })()")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for system in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1100, "height": 800}, color_scheme=system, bypass_csp=True)
        page = ctx.new_page()
        page.goto(BASE + "/#/account")
        page.get_by_role("button", name="System").wait_for()
        check(dark(page) == (system == "dark"), "with the system on %s and no choice made, the page follows it" % system)
        page.get_by_role("button", name="Dark").click()
        check(dark(page), "choosing Dark makes it dark (system: %s)" % system)
        page.reload(); page.get_by_role("button", name="System").wait_for()
        check(dark(page) and page.get_by_role("button", name="Dark").get_attribute("aria-pressed") == "true", "the choice survives a reload, and the control shows it")
        page.get_by_role("button", name="Light").click()
        check(not dark(page), "choosing Light makes it light (system: %s)" % system)
        page.get_by_role("button", name="System").click()
        check(dark(page) == (system == "dark"), "System goes back to following the system")
        ctx.close()
    browser.close()
sys.exit(1 if failures else 0)
