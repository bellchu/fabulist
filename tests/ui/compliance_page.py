"""The Compliance page: each tag across projects, linked to the projects and to a search. Run with scripts/ui-check-k8s.sh."""
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
    names = []
    for n in ("Billing", "Portal"):
        pid = ctx.request.post(BASE + "/api/projects", data={"name": "Cmp " + n, "platform": "generic"}, headers=H).json()["id"]
        names.append(pid)
        ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall log every %s event for audit." % n.lower(), "compliance_tags": ["Corp-Policy-9"]}, headers=H)
    many = ctx.request.post(BASE + "/api/projects", data={"name": "Cmp Many", "platform": "generic"}, headers=H).json()["id"]
    ctx.request.post(f"{BASE}/api/projects/{many}/requirements", data={"statement": "The system shall keep an audit trail of every change.", "compliance_tags": ["Zeta-%d" % n for n in range(6)]}, headers=H)
    ctx.request.post(f"{BASE}/api/projects/{names[0]}/stories", data={"title": "Audit billing", "requirement_ids": ["REQ-001"]}, headers=H)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/")
    page.wait_for_selector(".project-card")
    page.get_by_role("link", name="Compliance", exact=True).click()
    page.wait_for_selector("text=Compliance coverage")
    row = page.locator("tr", has_text="Corp-Policy-9")
    check("1 of 2 – gaps" in row.inner_text(), "the tag shows 2 requirements, 1 covered: %r" % row.inner_text().replace("\n", " ")[:90])
    check(row.locator("a.chip").count() == 2, "and links to both projects")
    check("Compliance" in page.title(), "the tab title says where you are (%s)" % page.title())
    page.get_by_label("Filter tags or projects").fill("corp-policy")
    page.wait_for_function("document.querySelectorAll('tbody tr').length === 1")
    check("Corp-Policy-9" in page.locator("tbody").inner_text() and "Zeta-0" not in page.locator("tbody").inner_text(), "the filter narrows the table to the matching tag")
    page.get_by_label("Filter tags or projects").fill("no-such-thing")
    page.wait_for_selector("text=No tag or project matches.")
    page.get_by_label("Filter tags or projects").fill("")
    with page.expect_download() as got:
        page.get_by_role("button", name="Download CSV").click()
    check(got.value.url.endswith("/api/compliance.csv"), "the page downloads the same overview as a CSV (%s)" % got.value.url[-24:])
    row.get_by_role("link", name="Corp-Policy-9").first.click()
    page.wait_for_selector("text=Results for")
    check(page.locator("tr", has_text="REQ-001").count() >= 2, "the tag opens a search for everything carrying it")
    browser.close()
sys.exit(1 if failures else 0)
