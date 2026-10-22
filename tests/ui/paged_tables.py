"""Long tables are drawn a page at a time. Filters, sorting, select-all and the drawer's Next/Prev still work on every row, not just the ones
drawn. Run with scripts/ui-check-k8s.sh."""
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    pid = api.post(BASE + "/api/projects", data={"name": "Long lists", "platform": "generic"}, headers=H).json()["id"]
    for i in range(1200):
        api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability number %d for the business." % (i + 1)}, headers=H)
    for i in range(700):
        api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Story number %d" % (i + 1), "persona": "buyer", "epic": "E", "feature": "F%d" % (i // 50)}, headers=H)
    rows = lambda: page.locator("tbody tr.clickable").count()  # noqa: E731

    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.wait_for_selector("tr.clickable")
    check(rows() == 300, f"requirements: 300 of 1,200 rows are drawn first ({rows()})")
    check("Showing 300 of 1,200" in page.locator("tr.pager").inner_text(), "requirements: the page says how many are shown")
    page.get_by_role("button", name="Show 300 more").click()
    check(rows() == 600 and page.evaluate("document.activeElement.getAttribute('aria-label')") == "Open REQ-301", f"requirements: Show more draws 300 more and puts focus on the first new row ({rows()})")

    page.get_by_placeholder("Search requirements").fill("number 1150 ")
    page.wait_for_function("document.querySelectorAll('tbody tr.clickable').length === 1")
    check("1150" in page.locator("tbody tr.clickable").inner_text() and page.locator("tr.pager").count() == 0, "requirements: search finds a row that was never drawn")
    page.get_by_placeholder("Search requirements").fill("")
    page.wait_for_function("document.querySelectorAll('tbody tr.clickable').length === 300")
    check(True, "requirements: clearing the search starts again from the first page")

    page.get_by_role("checkbox", name="Select all 1,200 matching").check()
    check("1,200 selected" in page.locator(".bulkbar").inner_text() or "1200 selected" in page.locator(".bulkbar").inner_text(), "requirements: select all selects every matching row, drawn or not")
    page.get_by_role("button", name="Clear").click()

    page.get_by_role("button", name="Open REQ-300").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer")
    page.wait_for_function("document.activeElement.closest('.drawer')")
    page.keyboard.press("Alt+ArrowRight")
    page.wait_for_function("document.querySelector('.drawer .muted.small').innerText.startsWith('REQ-301')")
    check(True, "requirements: Next from the last drawn row opens the next requirement")
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Show all").click()
    check(rows() == 1200 and page.locator("tr.pager").count() == 0, f"requirements: Show all draws everything ({rows()})")

    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.reload()
    page.get_by_role("button", name="List").click()
    page.wait_for_selector("tr.clickable")
    check(rows() == 300 and "Showing 300 of 700" in page.locator("tr.pager").inner_text(), f"stories: 300 of 700 rows are drawn first ({rows()})")
    page.get_by_role("button", name="Show 300 more").click()
    check(rows() == 600, f"stories: Show more draws 300 more ({rows()})")
    page.get_by_role("checkbox", name="Select all 700 matching").check()
    check("700 selected" in page.locator(".bulkbar").inner_text(), "stories: select all selects every matching story")
    browser.close()
sys.exit(1 if failures else 0)
