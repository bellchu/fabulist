"""WCAG A/AA scan (axe-core) of the newer review tools: the shortcuts list, Tag…, Merge… and Split… dialogs, the compliance filter and the overview's
compliance card, light and dark. Run with scripts/ui-check-k8s.sh."""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
AXE = urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode()
failures = []


def scan(page, what):
    page.wait_for_timeout(600)  # let a dialog finish its entrance animation: axe measures contrast on what is painted
    page.add_script_tag(content=AXE)
    result = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 100)})))")
    bad = [v for v in result if v["impact"] in ("serious", "critical")]
    print(("ok   " if not bad else "FAIL ") + what + ("" if not bad else " " + str(bad)))
    if bad:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, color_scheme=scheme)
        pid = ctx.request.post(BASE + "/api/projects", data={"name": "Axe tools " + scheme, "platform": "generic"}, headers=H).json()["id"]
        for text in ("The system shall export invoices to CSV on request.", "Invoices shall be exportable as CSV when a user asks.", "The system shall email order confirmations to buyers."):
            ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text, "compliance_tags": ["SOX"]}, headers=H)
        page = ctx.new_page()
        page.goto(f"{BASE}/#/p/{pid}/overview")
        page.wait_for_selector("text=Compliance coverage")
        scan(page, f"{scheme}: overview with the compliance coverage card")
        page.keyboard.press("?")
        page.wait_for_selector(".modal")
        scan(page, f"{scheme}: the keyboard shortcuts list")
        page.keyboard.press("Escape")
        page.goto(f"{BASE}/#/p/{pid}/requirements")
        page.wait_for_selector("tr.clickable")
        scan(page, f"{scheme}: requirements with the compliance filter")
        page.get_by_label("Select REQ-001").check()
        page.get_by_label("Select REQ-002").check()
        page.get_by_role("button", name="Tag…").click()
        page.wait_for_selector(".modal")
        scan(page, f"{scheme}: the Tag… dialog")
        page.locator(".modal-foot").get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Merge…").click()
        page.wait_for_selector(".modal")
        scan(page, f"{scheme}: the Merge… dialog")
        page.locator(".modal-foot").get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Open REQ-003").click()
        page.wait_for_selector(".drawer")
        page.get_by_role("button", name="Split…").click()
        page.wait_for_selector(".modal")
        scan(page, f"{scheme}: the Split… dialog")
        ctx.close()
    browser.close()
sys.exit(1 if failures else 0)
