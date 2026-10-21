"""WCAG A/AA scan (axe-core) of the main screens and review drawers, light and dark. Serious or critical violations fail.
Run with scripts/ui-check-k8s.sh (needs outbound access once, to fetch axe-core)."""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
AXE = urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode()
failures = []


def scan(page, what):
    page.add_script_tag(content=AXE)
    result = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 100)})))")
    bad = [v for v in result if v["impact"] in ("serious", "critical")]
    print(("ok   " if not bad else "FAIL ") + what + ("" if not bad else " " + str(bad)))
    if bad:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True, color_scheme=scheme)
        api, page = ctx.request, ctx.new_page()
        pid = api.post(BASE + "/api/projects", data={"name": "A11y " + scheme, "platform": "generic"}, headers=H).json()["id"]
        for i in range(8):
            made = api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability %d." % i}, headers=H).json()
            if i == 0:  # an open discussion renders on a tinted background: that is where ghost buttons lost contrast on the live site
                api.post(f"{BASE}/api/projects/{pid}/comments", data={"item_id": made["id"], "body": "Is this in scope for release 1?"}, headers=H)
            api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Story %d" % i, "persona": "buyer", "narrative": "As a buyer, I want %d, so that value." % i,
                                                              "acceptance_criteria": ["Given a, when b, then c"], "epic": "E", "feature": "F"}, headers=H)
        for tab in ("requirements", "stories"):
            page.goto(f"{BASE}/#/p/{pid}/{tab}")
            page.reload()
            page.wait_for_selector("tr.clickable, .story-card")
            scan(page, f"{scheme}: {tab} list")
            page.locator("tr.clickable, .story-card").first.click()
            page.wait_for_selector(".drawer")
            page.wait_for_timeout(300)
            scan(page, f"{scheme}: {tab} review drawer")
            page.keyboard.press("Escape")
            if tab == "stories":
                page.get_by_role("button", name="List").click()
                page.get_by_role("checkbox", name="Select all").check()  # the bulk bar is part of the page now
                scan(page, f"{scheme}: stories list view with a selection")
                page.get_by_role("button", name="Story map").click()
        page.goto(f"{BASE}/#/p/{pid}/requirements")
        page.reload()
        page.get_by_role("checkbox", name="Select all").check()
        scan(page, f"{scheme}: requirements list with a selection")
        ctx.close()
    browser.close()
sys.exit(1 if failures else 0)
