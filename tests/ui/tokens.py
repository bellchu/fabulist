"""API tokens for a named account: create, the one-time reveal dialog, copy, list (prefix only), use as a Bearer token, revoke.
Needs the sign-in-required app instance (AUTH_URL) that scripts/ui-check-k8s.sh starts."""
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
    made = admin.request.post(AUTH + "/api/admin/users", data={"username": "alice", "role": "analyst", "password": "Alice-Passw0rd!x"}, headers=H)
    check(made.status == 201, f"setup: named account created ({made.status})")
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True, http_credentials={"username": "alice", "password": "Alice-Passw0rd!x"})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=AUTH)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    page.goto(AUTH + "/#/account")
    page.reload()
    page.get_by_role("button", name="New token").first.click()
    page.get_by_placeholder("e.g. Nightly Jira sync").fill("ci-sync")
    page.get_by_role("button", name="Create token").click()
    box = page.get_by_label("New API token")
    box.wait_for()
    token = box.input_value()
    check(token.startswith("fab_") and len(token) > 20, "reveal dialog shows the new token once")
    page.get_by_role("button", name="Copy token").click()
    check(page.get_by_text("Token copied").count() >= 1 and page.evaluate("navigator.clipboard.readText()") == token, "copy puts the token on the clipboard")
    page.keyboard.press("Escape")
    page.wait_for_selector("table")
    row = page.locator("tbody tr").first.inner_text()
    check("ci-sync" in row and token not in row and token[:8] in row, "list shows the name and prefix only, never the secret")
    ok = ctx.request.get(AUTH + "/api/projects", headers={"Authorization": "Bearer " + token}, max_redirects=0)
    anon = browser.new_context().request.get(AUTH + "/api/projects", headers={"Authorization": "Bearer " + token})
    check(anon.status == 200, f"the token authenticates API calls ({anon.status})")
    page.get_by_role("button", name="Revoke token ci-sync").click()
    page.get_by_role("button", name="Revoke token", exact=True).last.click()
    page.wait_for_selector("text=No API tokens")
    gone = browser.new_context().request.get(AUTH + "/api/projects", headers={"Authorization": "Bearer " + token})
    check(gone.status == 401, f"a revoked token stops working ({gone.status})")
    check(not errors, f"no page errors {errors}")
    browser.close()
sys.exit(1 if failures else 0)
