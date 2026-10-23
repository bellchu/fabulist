"""Tag many requirements at once from the bulk bar. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Tag UI", "platform": "generic"}, headers=H).json()["id"]
    for text in ("The system shall log every payment attempt.", "The system shall mask card numbers in reports."):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select REQ-001").check()
    page.get_by_label("Select REQ-002").check()
    page.get_by_role("button", name="Tag…").click()
    page.wait_for_selector(".modal")
    page.get_by_label("Compliance tag").fill("SOX")
    page.locator(".modal-foot").get_by_role("button", name="Apply").click()
    page.wait_for_selector("text=2 requirements updated")
    got = ctx.request.get(f"{BASE}/api/projects/{pid}/requirements").json()
    check(all(r["compliance_tags"] == ["SOX"] for r in got), "both requirements now carry the tag")
    for title in ("Export invoices", "Email receipts"):
        ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": title}, headers=H)
    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.wait_for_selector("text=Export invoices")
    page.get_by_role("button", name="List").click()
    boxes = page.locator("tbody input[type=checkbox]")
    boxes.nth(0).check()
    boxes.nth(1).check()
    page.get_by_role("button", name="Tag…").click()
    page.wait_for_selector(".modal")
    page.get_by_label("Compliance tag").fill("GDPR")
    page.locator(".modal-foot").get_by_role("button", name="Apply").click()
    page.wait_for_selector("text=2 stories updated")
    stories = ctx.request.get(f"{BASE}/api/projects/{pid}/stories").json()
    check(all(s["compliance_tags"] == ["GDPR"] for s in stories), "both stories now carry the tag")
    browser.close()
sys.exit(1 if failures else 0)
