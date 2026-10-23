"""A requirement or story can be linked to: search results and the audit trail open the item itself, and so does an address ending in its key.
Run with scripts/ui-check-k8s.sh."""
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
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Deep links", "platform": "generic"}, headers=H).json()["id"]
    for i in range(3):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support linkable capability %d for buyers." % i}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Linkable story", "narrative": "As a buyer I want a link so that I can share it."}, headers=H)
    page = ctx.new_page()

    page.goto(f"{BASE}/#/p/{pid}/requirements/REQ-002")
    page.wait_for_selector(".drawer")
    check("capability 1" in page.get_by_label("Statement").input_value(), "an address ending in a requirement's key opens that requirement")
    check(page.evaluate("location.hash") == f"#/p/{pid}/requirements", "and the address goes back to the tab, so saving or closing does not reopen it")
    page.get_by_role("button", name="Copy link to REQ-002").click()
    page.wait_for_selector("text=Link to REQ-002 copied")
    check(page.evaluate("navigator.clipboard.readText()") == f"{BASE}/#/p/{pid}/requirements/REQ-002", "Copy link puts the item's address on the clipboard")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached")

    page.goto(f"{BASE}/#/p/{pid}/requirements/REQ-999")
    page.wait_for_function(f"location.hash === '#/p/{pid}/requirements'", timeout=10000)
    check(page.locator(".drawer").count() == 0, "a key that does not exist just shows the tab (no drawer, and the address is cleaned up)")

    page.goto(f"{BASE}/#/p/{pid}/stories/US-001")
    page.wait_for_selector(".drawer")
    check(page.get_by_label("Title").input_value() == "Linkable story", "the same for a story")
    page.keyboard.press("Escape")

    page.goto(f"{BASE}/#/search/linkable")
    page.wait_for_selector("text=Results for")
    page.get_by_role("link", name="REQ-003").first.click()
    page.wait_for_selector(".drawer")
    check("capability 2" in page.get_by_label("Statement").input_value(), "a search result opens the item itself, not only its tab")
    page.keyboard.press("Escape")

    page.goto(f"{BASE}/#/p/{pid}/audit")
    page.wait_for_selector("text=entries")
    page.get_by_role("link", name="REQ-001").first.click()
    page.wait_for_selector(".drawer")
    check("capability 0" in page.get_by_label("Statement").input_value(), "a requirement's key in the audit trail opens it")
    browser.close()
sys.exit(1 if failures else 0)
