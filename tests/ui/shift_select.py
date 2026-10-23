"""Shift-click ticks a run of rows between the last one ticked and this one. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Shift select", "platform": "generic"}, headers=H).json()["id"]
    for n in range(6):
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support scannable capability number %d." % n}, headers=H)
        ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Capability story %d" % n}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select REQ-002").check()
    page.get_by_label("Select REQ-005").click(modifiers=["Shift"])
    page.wait_for_selector("text=4 selected")
    ticked = [page.get_by_label(f"Select REQ-00{n}").is_checked() for n in range(1, 7)]
    check(ticked == [False, True, True, True, True, False], "shift-click ticks the run from REQ-002 to REQ-005 (%s)" % ticked)
    page.get_by_label("Select REQ-004").click(modifiers=["Shift"])
    page.wait_for_selector("text=2 selected")
    check(not page.get_by_label("Select REQ-004").is_checked() and not page.get_by_label("Select REQ-005").is_checked() and page.get_by_label("Select REQ-003").is_checked(),
          "shift-unticking clears the run back to the last row clicked")
    page.goto(f"{BASE}/#/p/{pid}/stories")
    page.get_by_role("button", name="List").click()
    page.wait_for_selector("tr.clickable")
    page.get_by_label("Select US-001").check()
    page.get_by_label("Select US-003").click(modifiers=["Shift"])
    page.wait_for_selector("text=3 selected")
    check(True, "the stories list does the same")
    browser.close()
sys.exit(1 if failures else 0)
