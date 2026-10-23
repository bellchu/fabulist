"""An account whose password an administrator chose is taken to the password form and nothing else works until it is set. Needs the sign-in-required
app instance (AUTH_URL). Run with scripts/ui-check-k8s.sh."""
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
    made = admin.request.post(AUTH + "/api/admin/users", data={"username": "freshhire", "role": "analyst", "password": "Temp-Passw0rd!xyz", "must_change": True}, headers=H)
    check(made.status == 201, "setup: account with a password to be changed (%s)" % made.status)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials={"username": "freshhire", "password": "Temp-Passw0rd!xyz"})
    page = ctx.new_page()
    page.goto(AUTH + "/#/")
    page.wait_for_selector("text=Choose your own password")
    check("#/account" in page.url, "the home page sends them to the password form")
    check(page.locator("text=New token").count() == 0, "and nothing else on the page is offered")
    page.get_by_label("Current password").fill("Temp-Passw0rd!xyz")
    page.get_by_label("New password", exact=True).fill("My-Own-Passw0rd!99")
    page.get_by_label("New password again").fill("My-Own-Passw0rd!99")
    page.get_by_role("button", name="Change password").click()
    page.wait_for_selector("text=Password changed")
    fresh = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials={"username": "freshhire", "password": "My-Own-Passw0rd!99"}).new_page()
    fresh.goto(AUTH + "/#/")
    fresh.wait_for_selector(".global-search")
    fresh.wait_for_timeout(500)
    check("#/account" not in fresh.url and fresh.locator("text=Choose your own password").count() == 0, "with the new password the application opens normally")
    # the Users page flags a pending change
    admin_page = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials=ADMIN).new_page()
    admin.request.post(AUTH + "/api/admin/users", data={"username": "pending1", "role": "viewer", "password": "Temp-Passw0rd!xyz", "must_change": True}, headers=H)
    admin_page.goto(AUTH + "/#/admin/users")
    admin_page.wait_for_selector("text=Must change password")
    check(True, "the Users page shows who still has to change their password")
    browser.close()
sys.exit(1 if failures else 0)
