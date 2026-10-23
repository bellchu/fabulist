"""A private project, end to end with two accounts: priya makes it private, ben cannot see or open it, priya adds him, then opens it to
everyone again. Needs the sign-in-required instance (AUTH_URL) that scripts/ui-check-k8s.sh starts."""
import os
import sys
import urllib.request

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
    for name, password in (("priya", "Priya-Passw0rd!x"), ("ben", "Ben-Passw0rd!xyz")):
        root.post(AUTH + "/api/admin/users", data={"username": name, "role": "analyst", "password": password}, headers=H)

    def person(name, password):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True, http_credentials={"username": name, "password": password})
        return ctx.new_page()
    priya, ben = person("priya", "Priya-Passw0rd!x"), person("ben", "Ben-Passw0rd!xyz")

    priya.goto(f"{AUTH}/#/")
    priya.reload()
    priya.get_by_role("button", name="New", exact=True).click()
    priya.wait_for_selector(".modal")
    priya.get_by_placeholder("e.g. CRM order capture").fill("Confidential programme")
    priya.locator(".modal").get_by_role("button", name="Only people I choose").click()
    priya.locator(".modal").get_by_role("button", name="Create project").click()
    priya.wait_for_url("**/#/p/**")
    priya.wait_for_selector(".page-head h1:has-text('Confidential programme')")
    pid = priya.url.split("/p/")[1].split("/")[0]
    check(priya.locator(".page-head").get_by_text("Private").count() == 1, "priya: the new project shows a Private badge")

    ben.goto(f"{AUTH}/#/")
    ben.reload()
    ben.wait_for_selector("text=Projects")
    ben.wait_for_timeout(500)
    check(ben.get_by_text("Confidential programme").count() == 0, "ben: the private project is not in his list")
    ben.goto(f"{AUTH}/#/p/{pid}")
    ben.reload()
    ben.wait_for_selector(".callout.error")
    check("not found" in ben.locator(".callout.error").inner_text().lower(), "ben: opening it directly says it was not found")

    priya.get_by_role("button", name="Edit", exact=True).click()
    priya.wait_for_selector(".modal")
    check(priya.locator(".modal").get_by_role("button", name="Only people I choose").get_attribute("aria-pressed") == "true", "priya: the settings show it is private")
    priya.add_script_tag(content=urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode())
    bad = priya.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id))")
    check(not bad, f"priya: the access controls in the settings dialog have no serious accessibility violations {bad}")
    box = priya.locator(".modal").get_by_placeholder("Type and press Enter")
    box.fill("ben")
    box.press("Enter")
    priya.locator(".modal").get_by_role("button", name="Save").click()
    priya.wait_for_selector(".modal", state="detached")

    ben.goto(f"{AUTH}/#/")
    ben.reload()
    ben.wait_for_selector("text=Confidential programme")
    check(ben.locator(".project-card", has_text="Confidential programme").get_by_text("Private").count() == 1, "ben: once added, the project is listed, marked Private")
    ben.locator(".project-card", has_text="Confidential programme").click()
    ben.locator(".page-head h1", has_text="Confidential programme").wait_for()  # not just any .page-head: the list page has one too
    check(ben.locator(".page-head h1").inner_text() == "Confidential programme", "ben: and he can open it")

    priya.get_by_role("button", name="Edit", exact=True).click()
    priya.wait_for_selector(".modal")
    priya.locator(".modal").get_by_role("button", name="Everyone", exact=True).click()
    priya.locator(".modal").get_by_role("button", name="Save").click()
    priya.wait_for_selector(".modal", state="detached")
    priya.reload()
    priya.wait_for_selector(".tabs")  # the project itself has loaded: a missing badge now means it is really gone
    check(priya.locator(".page-head").get_by_text("Private").count() == 0, "priya: opened to everyone again, the badge is gone")
    browser.close()
sys.exit(1 if failures else 0)
