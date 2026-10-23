"""At phone width no screen, dialog or drawer pushes the page sideways. Run with scripts/ui-check-k8s.sh."""
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


WIDE = """[...document.querySelectorAll('body *')].filter((e) => {
  if (e.getBoundingClientRect().right <= window.innerWidth + 1) return false;
  for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;  // inside something that scrolls or clips by itself
  return true;
}).slice(0, 4).map((e) => e.tagName + '.' + e.className + ' ' + (e.textContent || '').slice(0, 40))"""


def fits(page, what):
    what = f"{what} at {page.viewport_size['width']}px"
    page.wait_for_timeout(500)
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 0, f"{what}: no sideways scroll ({over}px over)" + ("" if over <= 0 else " " + str(page.evaluate(WIDE))))


with sync_playwright() as p:
    browser = p.chromium.launch()
    for W in (320, 768):
        ctx = browser.new_context(viewport={"width": W, "height": 800}, bypass_csp=True)
        pid = ctx.request.post(BASE + "/api/projects", data={"name": "Phone sweep" + "w" * 90, "platform": "generic"}, headers=H).json()["id"]
        for text in ("The system shall export invoices to CSV on request, see https://example.com/" + "p" * 150, "The system shall email order confirmations to buyers."):
            ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text, "compliance_tags": ["SOX"]}, headers=H)
        ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Export invoices", "requirement_ids": ["REQ-001"], "compliance_tags": ["SOX"]}, headers=H)
        ctx.request.post(BASE + "/api/projects", data={"name": "Unbroken" + "x" * 90, "description": "y" * 200 + " " + "z" * 120, "platform": "generic"}, headers=H)
        page = ctx.new_page()
        page.goto(BASE + "/#/")
        page.wait_for_selector(".page-head")
        fits(page, "home")
        for tab in ("overview", "sources", "requirements", "stories", "trace", "audit", "export"):
            page.evaluate("document.querySelectorAll('.page-head').forEach((e) => e.remove())")  # the old page's header would satisfy the wait below
            page.goto(f"{BASE}/#/p/{pid}/{tab}")
            page.wait_for_selector(".page-head")
            fits(page, tab)
        for route in ("admin/overview", "admin/users", "admin/providers", "admin/settings", "admin/integrations", "admin/taxonomy", "admin/activity", "account", "compliance", "search/" + "q" * 120):
            page.evaluate("document.querySelectorAll('.page-head').forEach((e) => e.remove())")  # the old page's header would satisfy the wait below
            page.goto(f"{BASE}/#/{route}")
            page.wait_for_selector(".page-head")
            fits(page, route.split("/")[0] + "/" + route.split("/")[-1][:12])
        page.goto(f"{BASE}/docs")
        page.wait_for_load_state()
        fits(page, "API reference")
        page.goto(f"{BASE}/#/p/{pid}/requirements")
        page.wait_for_selector("tr.clickable")
        page.get_by_label("Select REQ-001").check()
        page.get_by_label("Select REQ-002").check()
        for button in ("Tag…", "Set…", "Merge…", "Assign…"):
            page.get_by_role("button", name=button).click()
            page.wait_for_selector(".modal")
            fits(page, button + " dialog")
            box = page.locator(".modal").bounding_box()
            check(box["x"] >= 0 and box["x"] + box["width"] <= W + 1, f"{button} dialog fits the screen")
            page.locator(".modal-foot").get_by_role("button", name="Cancel").click()
            page.wait_for_function("!document.querySelector('.modal')")
        page.get_by_role("button", name="Open REQ-001").click()
        page.wait_for_selector(".drawer")
        fits(page, "requirement drawer")
        page.get_by_role("button", name="Split…").click()
        page.wait_for_selector(".modal")
        fits(page, "Split… dialog")
        box = page.locator(".modal").bounding_box()
        check(box["x"] >= 0 and box["x"] + box["width"] <= W + 1, "Split… dialog fits the screen")
    browser.close()
sys.exit(1 if failures else 0)
