"""'Try a sample project' on an empty project list: one click to a project with a BRD, and the workflow runs offline. Run with scripts/ui-check-k8s.sh
(on a database without projects: the script clears them first)."""
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
    others = [x["id"] for x in ctx.request.get(BASE + "/api/projects", headers=H).json()]
    for pid in others:  # an empty list is where the button lives; they are restored at the end
        ctx.request.post(f"{BASE}/api/projects/{pid}/archive", data={}, headers=H)
    providers = ctx.request.get(BASE + "/api/admin/providers", headers=H).json()["providers"]  # earlier tests leave their own default behind
    was_default = next((x["id"] for x in providers if x["is_default"]), None)
    offline = next(x["id"] for x in providers if x["kind"] == "mock")
    ctx.request.post(f"{BASE}/api/admin/providers/{offline}/default", data={}, headers=H)
    page = ctx.new_page()
    page.goto(BASE + "/#/")
    page.get_by_role("button", name="Try a sample project").click()
    page.wait_for_selector(".page-head h1:has-text('Sample: order to cash')")
    check("/sources" in page.url, "one click lands on the new project's Sources tab")
    page.wait_for_selector("text=order-to-cash-brd.md", timeout=10000)  # the header draws first; the documents list follows its own request
    check("order-to-cash-brd.md" in page.locator(".content").inner_text(), "with the BRD already there")
    page.goto(page.url.replace("/sources", "/requirements"))
    page.locator(".runbox .btn-primary").click()
    page.wait_for_selector("tr.clickable", timeout=60000)
    check(page.locator("tr.clickable").count() >= 8, "extraction works with no AI provider configured (%d requirements)" % page.locator("tr.clickable").count())
    if was_default:
        ctx.request.post(f"{BASE}/api/admin/providers/{was_default}/default", data={}, headers=H)
    for pid in others:
        ctx.request.post(f"{BASE}/api/projects/{pid}/restore", data={}, headers=H)
    browser.close()
sys.exit(1 if failures else 0)
