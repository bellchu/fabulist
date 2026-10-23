"""Review work can be assigned to a person and each person can find theirs. Needs the sign-in-required app instance (AUTH_URL). Run with scripts/ui-check-k8s.sh."""
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
    admin = browser.new_context(http_credentials=ADMIN)
    for name in ("assignerann", "assigneebob"):
        admin.request.post(AUTH + "/api/admin/users", data={"username": name, "role": "analyst", "password": name + "-Passw0rd!x"}, headers=H)

    def person(name):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials={"username": name, "password": name + "-Passw0rd!x"})
        return ctx, ctx.new_page()

    ann_ctx, ann = person("assignerann")
    pid = ann_ctx.request.post(AUTH + "/api/projects", data={"name": "Assignments", "platform": "generic"}, headers=H).json()["id"]
    for i in range(2):
        ann_ctx.request.post(f"{AUTH}/api/projects/{pid}/requirements", data={"statement": "The system shall support assignable capability %d." % i}, headers=H)
    ann.goto(f"{AUTH}/#/p/{pid}/requirements")
    ann.get_by_role("button", name="Open REQ-001").click()
    ann.wait_for_selector(".drawer")
    ann.get_by_label("Assignee", exact=True).select_option("assigneebob")
    ann.get_by_role("button", name="Save", exact=True).click()
    ann.wait_for_selector("text=→ assigneebob")
    check(True, "an assignee chosen in the drawer is saved and shown in the table")

    bob_ctx, bob = person("assigneebob")
    bob.goto(f"{AUTH}/#/p/{pid}/requirements")
    bob.get_by_label("Assignee filter").wait_for()
    bob.get_by_label("Assignee filter").select_option("me")
    bob.wait_for_timeout(500)
    check(bob.locator("tr.clickable").count() == 1 and "capability 0" in bob.locator("tr.clickable").first.inner_text(), "Assigned to me shows just my requirement")

    ann.get_by_label("Assignee filter").select_option("me")
    ann.wait_for_timeout(500)
    check(ann.locator("tr.clickable").count() == 0, "and nothing for someone with nothing assigned")
    ann.get_by_label("Assignee filter").select_option("none")
    ann.wait_for_timeout(500)
    check(ann.locator("tr.clickable").count() == 1 and "capability 1" in ann.locator("tr.clickable").first.inner_text(), "Unassigned shows the other one")
    # many at once
    ann.get_by_label("Assignee filter").select_option("")
    ann.wait_for_timeout(300)
    ann.get_by_label("Select REQ-001").check()
    ann.get_by_label("Select REQ-002").check()
    ann.get_by_role("button", name="Assign…").click()
    ann.get_by_label("Assignee", exact=True).select_option("assigneebob")
    ann.locator(".modal-foot").get_by_role("button", name="Assign", exact=True).click()
    ann.wait_for_selector("text=1 requirement updated")  # REQ-001 was already bob's
    ann.wait_for_timeout(600)
    check(ann.locator("text=→ assigneebob").count() == 2, "Assign… in the bulk bar gives every selected requirement to one person")
    # what is waiting for me, on the home page
    bob.goto(f"{AUTH}/#/")
    bob.wait_for_selector("#waiting-for-me")
    text = bob.locator("#waiting-for-me").inner_text()
    check("Assignments" in text and "2 requirements" in text, "the home page lists the projects with work waiting for me (%r)" % text.replace("\n", " | "))
    bob.locator("#waiting-for-me a", has_text="Assignments").click()
    bob.wait_for_selector("tr.clickable")
    bob.wait_for_timeout(300)
    check(bob.get_by_label("Assignee filter").input_value() == "me", "and following it opens the list already filtered to mine")
    browser.close()
sys.exit(1 if failures else 0)
