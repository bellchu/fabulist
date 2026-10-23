"""An archived project is read-only on the server, so the UI offers only reading, searching, asking and Restore, and offers
the edit controls again after Restore. Run with scripts/ui-check-k8s.sh."""
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
    refused, errors = [], []
    page.on("response", lambda r: r.status in (403, 409) and refused.append(f"{r.status} {r.request.method} {r.url[-40:]}"))
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    pid = api.post(BASE + "/api/projects", data={"name": "Retired", "platform": "generic"}, headers=H).json()["id"]
    req = api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices.", "status": "approved"}, headers=H).json()
    api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Export invoices", "status": "approved"}, headers=H)
    api.post(f"{BASE}/api/projects/{pid}/comments", data={"item_id": req["id"], "body": "Agreed."}, headers=H)

    def visible(*names):
        return [n for n in names if page.get_by_role("button", name=n, exact=True).count() or page.get_by_role("link", name=n, exact=True).count()]

    def open_tab(tab):
        page.goto(f"{BASE}/#/p/{pid}/{tab}")
        page.reload()
        page.wait_for_selector(".tabs")  # the project has loaded; every check here is about what is NOT offered, which would pass on a half-drawn page
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)

    WRITES = ("Edit", "Add document", "Connect repository", "Re-extract", "Add requirement", "Regenerate", "Estimate with AI", "Add story", "Create baseline")
    api.post(f"{BASE}/api/projects/{pid}/archive", data={}, headers=H)
    for tab in ("overview", "sources", "requirements", "stories", "export"):
        open_tab(tab)
        check(visible(*WRITES) == [], f"archived {tab}: no edit controls ({visible(*WRITES)})")
    open_tab("overview")
    check(visible("Restore") == ["Restore"], "archived: Restore is offered")
    open_tab("sources")
    check(visible("Ask", "Find passages") == ["Ask", "Find passages"], "archived sources: asking and passage search stay (the server allows them)")
    open_tab("requirements")
    check(page.locator("tbody input[type=checkbox]").count() == 0, "archived requirements: no selection checkboxes")
    page.locator("tr.clickable").first.click()
    page.wait_for_selector(".drawer")
    check(visible("Save", "Approve & next", "Refine with AI", "Delete") == [] and page.locator(".drawer fieldset:disabled").count() == 1, "archived drawer: read-only")
    check(page.get_by_text("the discussion is closed").count() == 1 and page.get_by_role("button", name="Post", exact=True).count() == 0
          and page.get_by_role("button", name="Resolve").count() == 0, "archived drawer: the discussion is closed, existing comments stay readable")
    check(page.get_by_text("Agreed.").count() == 1, "archived drawer: the earlier comment is still shown")
    page.keyboard.press("Escape")
    check(not refused, f"nothing offered on an archived project was refused by the server: {refused}")

    open_tab("overview")
    page.get_by_role("button", name="Restore").click()
    page.wait_for_timeout(1200)
    open_tab("requirements")
    check(set(visible("Add requirement", "Edit")) == {"Edit", "Add requirement"}, "after Restore: the edit controls are back")
    check(not errors, f"no page errors {errors}")
    browser.close()
sys.exit(1 if failures else 0)
