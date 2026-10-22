"""The read-only viewer role: the UI offers reading, searching passages and discussing, and none of the controls the server refuses.
Needs the sign-in-required instance (AUTH_URL) that scripts/ui-check-k8s.sh starts."""
import os
import sys

from playwright.sync_api import sync_playwright

AUTH = os.environ.get("AUTH_URL", "http://127.0.0.1:8001")
ADMIN = {"username": "admin", "password": os.environ.get("ADMIN_PASSWORD", "ui-check-admin-pass")}
H = {"X-Requested-With": "fabulist"}
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    root = browser.new_context(http_credentials=ADMIN).request
    pid = root.post(AUTH + "/api/projects", data={"name": "Read only", "platform": "generic"}, headers=H).json()["id"]
    reqs = [root.post(f"{AUTH}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices %d." % i, "status": "approved"}, headers=H).json() for i in range(3)]
    for i in range(3):
        root.post(f"{AUTH}/api/projects/{pid}/stories", data={"title": "Story %d" % i, "status": "approved"}, headers=H)
    root.post(AUTH + "/api/admin/users", data={"username": "vera", "role": "viewer", "password": "Vera-Passw0rd!x"}, headers=H)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True, http_credentials={"username": "vera", "password": "Vera-Passw0rd!x"})
    page = ctx.new_page()
    forbidden = []
    page.on("response", lambda r: r.status == 403 and forbidden.append(f"{r.request.method} {r.url[-40:]}"))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))

    def visible(*names):
        return [n for n in names if page.get_by_role("button", name=n, exact=True).count() or page.get_by_role("link", name=n, exact=True).count()]

    def open_tab(tab):
        page.goto(f"{AUTH}/#/p/{pid}/{tab}")
        page.reload()
        page.wait_for_timeout(1200)

    page.goto(f"{AUTH}/#/")
    page.reload()
    page.wait_for_timeout(1000)
    check(visible("New", "Import") == [], "home: no New / Import for a viewer")
    open_tab("overview")
    check(visible("Edit") == [] and visible("Export") == ["Export"], "project header: no Edit, Export still there")
    open_tab("sources")
    check(visible("Add document", "Connect repository", "Remove", "Sync now", "Ask") == [] and visible("Find passages") == ["Find passages"], "sources: no upload, connect, remove or Ask; passage search stays")
    open_tab("requirements")
    check(visible("Re-extract", "Extract", "Add requirement") == [] and page.locator("thead input[type=checkbox]").count() == 0 and page.locator("tbody input[type=checkbox]").count() == 0,
          "requirements: no extract, add or selection checkboxes")
    page.locator("tr.clickable").first.click()
    page.wait_for_selector(".drawer")
    check(visible("Save", "Approve & next", "Refine with AI", "Delete") == [], "requirement drawer: no Save, Approve, Refine or Delete")
    check(page.locator(".drawer fieldset:disabled").count() == 1 and page.locator(".drawer textarea").first.is_disabled(), "requirement drawer: the fields are read-only")
    check(page.get_by_role("button", name="Next requirement").is_enabled(), "requirement drawer: Prev/Next still work for reading")
    page.wait_for_function("document.activeElement.closest('.drawer')")
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(300)
    check(page.locator(".drawer").count() == 1 and not forbidden, "requirement drawer: the save shortcut does nothing instead of failing")
    page.get_by_placeholder("Ask a question or leave review feedback…").fill("In scope for release 1?")
    page.get_by_role("button", name="Post", exact=True).click()
    page.wait_for_selector("text=In scope for release 1?")
    check(page.get_by_role("button", name="Delete comment").count() == 0 and page.get_by_role("button", name="Resolve").count() == 1, "discussion: a viewer can comment and resolve, not delete")
    page.keyboard.press("Escape")
    open_tab("stories")
    check(visible("Regenerate", "Generate", "Estimate with AI", "Add story") == [], "stories: no generate, estimate or add")
    check(page.locator(".story-card[draggable=true]").count() == 0, "story map: cards are not draggable")
    page.get_by_role("button", name="List").click()
    page.wait_for_selector("tr.clickable")
    check(page.locator("tbody input[type=checkbox]").count() == 0, "story list: no selection checkboxes")
    page.locator("tr.clickable").first.click()
    page.wait_for_selector(".drawer")
    check(visible("Save", "Approve & next", "Refine with AI", "Delete") == [] and page.locator(".drawer fieldset:disabled").count() == 1, "story drawer: read-only")
    page.keyboard.press("Escape")
    open_tab("export")
    check(visible("Create baseline") == [] and page.get_by_text("Push to issue tracker").count() == 0, "export: no Create baseline, no push to tracker")
    check(page.get_by_role("button", name="Download").count() >= 1, "export: downloads stay available")
    check(not forbidden, f"nothing the viewer could click was refused by the server: {forbidden}")
    check(not errors, f"no page errors {errors}")
    browser.close()
sys.exit(1 if failures else 0)
