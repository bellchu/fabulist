"""Audit entries about the project itself say what they are about: "added document", "created project", not a bare "added". Run with scripts/ui-check-k8s.sh."""
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
    r = ctx.request
    pid = r.post(BASE + "/api/projects", data={"name": "Wording", "platform": "generic"}, headers=H).json()["id"]
    r.post(f"{BASE}/api/projects/{pid}/documents/text", data={"name": "brd.md", "text": "# A\nThe system shall export invoices to CSV on request.\n"}, headers=H)
    r.patch(f"{BASE}/api/projects/{pid}", data={"name": "Wording renamed"}, headers=H)
    r.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall log every export request."}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/p/{pid}/audit")
    page.wait_for_selector("text=5 entries".replace("5", "4"))
    text = page.locator(".content").inner_text()
    check("created project" in text, "the project's creation says what was created")
    check("added document" in text, "a BRD says it is a document")
    check("edited project" in text, "a rename says it is the project")
    check("created REQ-001" in text, "a requirement still shows its key")
    browser.close()
sys.exit(1 if failures else 0)
