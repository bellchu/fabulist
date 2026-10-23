"""The same pages and a short flow in Firefox and WebKit (Safari's engine), not only Chromium: no script errors, no console errors, the main things work.
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
    for name in ("firefox", "webkit"):
        browser = getattr(p, name).launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        problems = []
        pid = ctx.request.post(BASE + "/api/projects/sample", data={}, headers=H).json()["id"]
        ctx.request.post(f"{BASE}/api/projects/{pid}/runs", data={"stage": "requirements", "scope": "all"}, headers=H)
        import time
        for _ in range(60):
            if ctx.request.get(f"{BASE}/api/projects/{pid}/runs?limit=1", headers=H).json()[0]["status"] not in ("running", "queued"):
                break
            time.sleep(0.5)
        page = ctx.new_page()
        page.on("pageerror", lambda e: problems.append("script error: " + str(e)[:140]))
        page.on("console", lambda m: m.type == "error" and problems.append("console: " + m.text[:140]))
        page.goto(BASE + "/#/")
        page.wait_for_selector(".global-search")
        check(page.locator(".project-card").count() >= 1, f"{name}: the project list draws")
        page.goto(f"{BASE}/#/p/{pid}/requirements")
        page.wait_for_selector("tr.clickable")
        check(page.locator("tr.clickable").count() >= 8, f"{name}: the requirements table draws ({page.locator('tr.clickable').count()} rows)")
        page.get_by_role("button", name="Open REQ-001").click()
        page.wait_for_selector(".drawer")
        check(page.get_by_label("Statement").input_value().startswith("The system shall"), f"{name}: the review drawer opens with the statement")
        page.keyboard.press("Escape")
        page.wait_for_selector(".drawer", state="detached")
        page.goto(f"{BASE}/#/p/{pid}/overview")
        page.wait_for_selector(".legend")
        check(True, f"{name}: the overview draws")
        page.goto(BASE + "/#/admin/overview")
        page.wait_for_selector("[aria-label=System]")
        page.goto(BASE + "/#/account")
        page.get_by_role("button", name="Dark").click()
        dark = page.evaluate("(() => { const c = getComputedStyle(document.body).backgroundColor.match(/\\d+/g).map(Number); return (c[0] + c[1] + c[2]) / 3 < 100; })()")
        check(dark, f"{name}: choosing the dark theme works")
        page.get_by_role("button", name="System").click()
        page.goto(BASE + "/docs")
        page.wait_for_selector("text=API reference")
        check(not problems, f"{name}: no script or console errors {problems[:3]}")
        browser.close()
sys.exit(1 if failures else 0)
