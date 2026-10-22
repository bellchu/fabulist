"""A named account changes its own password on the Account page. Needs the sign-in-required app instance (AUTH_URL). Run with scripts/ui-check-k8s.sh."""
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
    assert admin.request.post(AUTH + "/api/admin/users", data={"username": "pwuser", "role": "viewer", "password": "Old-Passw0rd!xyz"}, headers=H).status == 201
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials={"username": "pwuser", "password": "Old-Passw0rd!xyz"})
    page = ctx.new_page()
    page.goto(AUTH + "/#/account")
    page.get_by_label("Current password").wait_for()
    page.get_by_label("Current password").fill("Old-Passw0rd!xyz")
    page.get_by_label("New password", exact=True).fill("New-Passw0rd!abc")
    page.get_by_label("New password again").fill("something-else-entirely")
    page.get_by_role("button", name="Change password").click()
    page.wait_for_selector("text=different")
    check(admin.request.get(AUTH + "/api/me", headers=H).ok and browser.new_context(http_credentials={"username": "pwuser", "password": "Old-Passw0rd!xyz"}).request.get(AUTH + "/api/me").ok,
          "a typo in the repetition changes nothing")
    page.get_by_label("New password again").fill("New-Passw0rd!abc")
    page.get_by_role("button", name="Change password").click()
    page.wait_for_selector("text=Password changed")
    old = browser.new_context(http_credentials={"username": "pwuser", "password": "Old-Passw0rd!xyz"}).request.get(AUTH + "/api/me")
    new = browser.new_context(http_credentials={"username": "pwuser", "password": "New-Passw0rd!abc"}).request.get(AUTH + "/api/me")
    check(old.status == 401 and new.ok, "the old password stops working and the new one works (%s, %s)" % (old.status, new.status))
    check(page.get_by_label("Current password").input_value() == "", "the form is emptied")
    browser.close()
sys.exit(1 if failures else 0)
