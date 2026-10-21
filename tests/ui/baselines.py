"""Sign-off baselines: create an approved-only baseline, export it, and compare it with the current backlog in its own scope
(drafts are not changes; a newly approved story is). Run with scripts/ui-check-k8s.sh."""
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True, accept_downloads=True)
    api, page = ctx.request, ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    pid = api.post(BASE + "/api/projects", data={"name": "Sign-off", "platform": "generic"}, headers=H).json()["id"]
    story = lambda title, status: api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": title, "status": status, "persona": "buyer",
                                           "narrative": "As a buyer, I want it, so that value.", "acceptance_criteria": ["Given a, when b, then c"]}, headers=H).json()
    api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices.", "status": "approved"}, headers=H)
    story("Export invoices", "approved")
    story("Email invoices", "approved")

    page.goto(f"{BASE}/#/p/{pid}/export")
    page.reload()
    page.get_by_role("button", name="Create baseline").click()
    page.get_by_placeholder("e.g. Release 1 sign-off").fill("Release 1")
    page.get_by_role("button", name="Approved items only").click()
    page.get_by_role("button", name="Create baseline", exact=True).last.click()
    page.wait_for_selector("text=Release 1")
    row = page.locator("tbody tr", has_text="Release 1").inner_text()
    check("v1" in row and "Approved only" in row and "2 stories" in row, "the baseline is listed as v1, approved only, with its contents")

    with page.expect_download() as dl:
        page.locator("tbody tr", has_text="Release 1").get_by_role("button", name="Export").click()
    check("v1" in dl.value.suggested_filename, f"export downloads a file named for the version ({dl.value.suggested_filename})")

    def open_compare():
        page.get_by_role("button", name="Compare Release 1 with the current backlog").click()
        page.wait_for_selector("text=Changes since v1")
        return page.locator(".modal").inner_text()

    draft = story("Archive invoices", "draft")
    text = open_compare()
    check("current backlog (approved only)" in text and text.count("No changes.") == 2, "a new draft is not shown as a change to an approved-only baseline")
    page.keyboard.press("Escape")

    api.patch(f"{BASE}/api/projects/{pid}/stories/{draft['id']}", data={"status": "approved"}, headers=H)
    text = open_compare()
    check("Added" in text and "Archive invoices" in text, "a story approved since the baseline is shown as added")
    page.keyboard.press("Escape")
    check(not errors, f"no page errors {errors}")
    browser.close()
sys.exit(1 if failures else 0)
