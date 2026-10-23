"""The search box at the top finds requirements and stories in every project, not just project names. Run with scripts/ui-check-k8s.sh."""
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
    a = api.post(BASE + "/api/projects", data={"name": "Zeta billing", "platform": "generic"}, headers=H).json()["id"]
    b = api.post(BASE + "/api/projects", data={"name": "Zeta portal", "platform": "generic"}, headers=H).json()["id"]
    api.post(f"{BASE}/api/projects/{a}/requirements", data={"statement": "The system shall export zebrafish invoices to CSV."}, headers=H)
    api.post(f"{BASE}/api/projects/{b}/requirements", data={"statement": "Users shall download their zebrafish invoices as PDF."}, headers=H)
    api.post(f"{BASE}/api/projects/{b}/stories", data={"title": "Zebrafish invoice download", "narrative": "As a user I want a PDF."}, headers=H)

    page.goto(BASE + "/#/")
    box = page.get_by_label("Search projects, requirements and stories")
    box.wait_for()
    box.fill("zebrafish"); box.press("Enter")
    page.wait_for_selector("text=Results for")
    text = page.locator(".content").inner_text()
    check("Zeta billing" in text and "Zeta portal" in text and "REQ-001" in text, "a word in a requirement finds it in both projects, with the project name")
    check("US-001" in text and "Zebrafish invoice download" in text, "and finds the story")
    page.get_by_role("link", name="Zeta billing").first.click()
    page.wait_for_selector(".page-head h1:has-text('Zeta billing')")
    check("/requirements" in page.url, "a requirement result opens that project's requirements")

    page.goto(BASE + "/#/")
    page.wait_for_selector(".project-card")  # the header is rebuilt on every route: type only once the page has settled
    box = page.get_by_label("Search projects, requirements and stories")
    box.fill("Zeta portal"); box.press("Enter")
    page.wait_for_selector(".page-head h1:has-text('Zeta portal')")
    check(True, "an exact project name still opens the project directly")

    page.goto(BASE + "/#/")
    page.wait_for_selector(".project-card")
    box = page.get_by_label("Search projects, requirements and stories")
    box.fill("qwertyuiop-nothing"); box.press("Enter")
    page.wait_for_selector("text=Nothing found")
    check(True, "a search with no hits says so")

    # the header is rebuilt on every route (a refresh, a poll that re-renders): half-typed text and the cursor must survive it
    page.goto(BASE + "/#/")
    page.wait_for_selector(".project-card")
    box = page.get_by_label("Search projects, requirements and stories")
    box.fill(""); box.type("zebra")
    page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")
    page.wait_for_timeout(800)
    box = page.get_by_label("Search projects, requirements and stories")
    check(box.input_value() == "zebra", "typed text survives the page being redrawn (%r)" % box.input_value())
    check(page.evaluate("document.activeElement === document.querySelector('.global-search input')"), "and so does the focus")
    box.type("fish")
    check(box.input_value() == "zebrafish", "typing carries on where it was")
    page.goto(BASE + "/#/search/sox")
    page.wait_for_selector("text=Results for")  # the previous page's header is also a .page-head: wait for the search page itself
    check("compliance tag" in page.locator(".page-head").inner_text(), "the search page says compliance tags are searched too")
    browser.close()
sys.exit(1 if failures else 0)
