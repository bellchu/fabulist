"""WCAG A/AA scan (axe-core) of the assignment screens: the "Waiting for you" card, the assignee filter and picker, the bulk assign dialog, light and dark.
Needs the sign-in-required app instance (AUTH_URL). Run with scripts/ui-check-k8s.sh."""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

AUTH = os.environ.get("AUTH_URL", "http://127.0.0.1:8001")
ADMIN = {"username": "admin", "password": os.environ.get("ADMIN_PASSWORD", "ui-check-admin-pass")}
H = {"X-Requested-With": "fabulist"}
AXE = urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode()
failures = []


def scan(page, what):
    page.wait_for_timeout(600)  # let a drawer or dialog finish its entrance animation: axe measures contrast on what is painted
    page.add_script_tag(content=AXE)
    result = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 100)})))")
    bad = [v for v in result if v["impact"] in ("serious", "critical")]
    print(("ok   " if not bad else "FAIL ") + what + ("" if not bad else " " + str(bad)))
    if bad:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    admin = browser.new_context(http_credentials=ADMIN)
    for name in ("axeann", "axebob"):
        admin.request.post(AUTH + "/api/admin/users", data={"username": name, "role": "analyst", "password": name + "-Passw0rd!x"}, headers=H)
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, color_scheme=scheme, http_credentials={"username": "axeann", "password": "axeann-Passw0rd!x"})
        pid = ctx.request.post(AUTH + "/api/projects", data={"name": "Axe assign " + scheme, "platform": "generic"}, headers=H).json()["id"]
        ids = [ctx.request.post(f"{AUTH}/api/projects/{pid}/requirements", data={"statement": "The system shall support scannable capability %d." % i}, headers=H).json()["id"] for i in range(3)]
        ctx.request.post(f"{AUTH}/api/projects/{pid}/requirements/assign", data={"ids": ids[:2], "assignee": "axeann"}, headers=H)
        page = ctx.new_page()
        page.goto(AUTH + "/#/")
        page.wait_for_selector("#waiting-for-me")
        scan(page, f"{scheme}: home with the Waiting for you card")
        page.goto(f"{AUTH}/#/p/{pid}/requirements")
        page.wait_for_selector("tr.clickable")
        scan(page, f"{scheme}: requirements with the assignee filter and the assignee shown")
        page.get_by_role("button", name="Open REQ-003").click()
        page.wait_for_selector(".drawer")
        scan(page, f"{scheme}: the requirement drawer with the assignee picker and the copy-link button")
        page.keyboard.press("Escape")
        page.get_by_label("Select REQ-001").check()
        page.get_by_role("button", name="Assign…").click()
        page.wait_for_selector(".modal")
        scan(page, f"{scheme}: the bulk assign dialog")
        ctx.close()
    browser.close()
sys.exit(1 if failures else 0)
