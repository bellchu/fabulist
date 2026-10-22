"""When the API or the network fails, the user gets a plain message, a way to retry, and keeps their unsaved edits.
Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
DB_DOWN = '{"detail":"The database is temporarily unavailable. Please retry."}'
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    pid = api.post(BASE + "/api/projects", data={"name": "Errors", "platform": "generic"}, headers=H).json()["id"]
    for i in range(3):
        api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall do thing %d." % i}, headers=H)
        api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Story %d" % i}, headers=H)
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.wait_for_selector("tr.clickable")

    # a failed save keeps the drawer, and so the user's edits, and says why
    page.route("**/api/projects/*/requirements/*", lambda r: r.fulfill(status=503, content_type="application/json", body=DB_DOWN) if r.request.method == "PATCH" else r.continue_())
    page.locator("tr.clickable").first.click()
    page.wait_for_selector(".drawer")
    page.locator(".drawer textarea").first.fill("An edit that must not be lost.")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector(".toast.error")
    check(page.locator(".drawer").count() == 1 and page.locator(".drawer textarea").first.input_value() == "An edit that must not be lost.", "failed save: the drawer stays open with the edit intact")
    check("temporarily unavailable" in page.locator(".toast.error").first.inner_text(), "failed save: the toast says what happened")
    page.keyboard.press("Escape")
    page.unroute("**/api/projects/*/requirements/*")

    # the network drops while a page loads: plain words and a Retry that works once it is back
    page.route("**/api/projects/*/stories", lambda r: r.abort())
    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.wait_for_selector(".callout.error")
    text = page.locator(".callout.error").first.inner_text()
    check("Can't reach the server" in text and "Failed to fetch" not in text, f"network drop: a plain message, not the browser's ({text.splitlines()[0]})")
    check(page.get_by_role("button", name="Retry").count() == 1, "network drop: a Retry button is offered")
    page.unroute("**/api/projects/*/stories")
    page.get_by_role("button", name="Retry").click()
    page.wait_for_selector(".story-card, tr.clickable")
    check(page.get_by_text("Story 0").count() >= 1, "Retry loads the page once the network is back")

    # the database is down while a page loads
    page.route("**/api/projects/*/requirements", lambda r: r.fulfill(status=503, content_type="application/json", body=DB_DOWN))
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector(".callout.error")
    check("temporarily unavailable" in page.locator(".callout.error").first.inner_text() and page.get_by_role("button", name="Retry").count() == 1, "503 on load: the message and a Retry button")
    page.unroute("**/api/projects/*/requirements")
    page.get_by_role("button", name="Retry").click()
    page.wait_for_selector("tr.clickable")
    check(True, "Retry recovers after the database is back")
    browser.close()
sys.exit(1 if failures else 0)
