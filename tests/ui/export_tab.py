"""The Export tab reads correctly for one story and one requirement, and its compliance choice is a normal-sized dropdown. Run with scripts/ui-check-k8s.sh."""
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Export tab", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"], "status": "approved"}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Audit payments", "status": "approved"}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/export")
    page.wait_for_selector("text=What to export")
    text = page.locator(".card", has=page.get_by_role("heading", name="What to export")).first.inner_text()
    check("1 story (excluding rejected) and 1 requirement (excluding excluded)" in text, "singular counts read correctly: %r" % text[:110].replace("\n", " "))
    page.get_by_role("button", name="Approved only").click()
    page.wait_for_selector("text=approved story")
    text = page.locator(".card", has=page.get_by_role("heading", name="What to export")).first.inner_text()
    check("1 approved story and 1 approved requirement will be exported" in text, "the approved-only sentence too: %r" % text[:110].replace("\n", " "))
    width = page.get_by_label("Compliance tag").bounding_box()["width"]
    check(width < 400, "the compliance choice is a normal dropdown, not a full-width bar (%dpx)" % width)
    page.goto(f"{BASE}/#/p/{pid}/sources")
    page.wait_for_selector("text=No repository connected")
    above = page.get_by_text("Drop BRD files here").bounding_box()["y"] < page.get_by_text("No repository connected").bounding_box()["y"]
    check(above and "Upload files above" in page.get_by_text("No repository connected").inner_text(), "the repository card points at the upload area where it really is (above)")
    browser.close()
sys.exit(1 if failures else 0)
