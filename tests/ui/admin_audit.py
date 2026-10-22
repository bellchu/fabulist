"""Administrators see every change to settings, providers and accounts on the Activity page, paged, exportable, accessible.
Run with scripts/ui-check-k8s.sh."""
import os
import sys
import urllib.request

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
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    api.post(BASE + "/api/admin/providers", data={"name": "Audit AI", "kind": "mock"}, headers=H)
    for i in range(30):
        api.put(BASE + "/api/admin/settings", data={"temperature": 0.3 + (i % 2) / 10}, headers=H)

    page.goto(BASE + "/#/admin/activity")
    page.wait_for_selector("#admin-audit table")
    rows = page.locator("#admin-audit tbody tr")
    check(rows.count() == 25, "the first page lists 25 changes, newest first (%d)" % rows.count())
    check("settings changed" in rows.first.inner_text() and "temperature" in rows.first.inner_text(), "a settings change names what changed")
    page.get_by_role("button", name="Show older").click()
    page.wait_for_function("document.querySelectorAll('#admin-audit tbody tr').length > 25")
    check("provider created" in page.locator("#admin-audit").inner_text() and "Audit AI" in page.locator("#admin-audit").inner_text(), "older entries load, including the provider")
    with page.expect_download() as d:
        page.get_by_role("button", name="Export trail").click()
    check(d.value.suggested_filename == "admin-audit-trail.csv", "Export trail downloads the CSV")

    page.add_script_tag(content=urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode())
    bad = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id))")
    check(not bad, "no serious accessibility violations %s" % bad)
    browser.close()
sys.exit(1 if failures else 0)
