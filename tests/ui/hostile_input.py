"""Stored markup in user-entered text must render as text. The browser runs with the CSP bypassed, so output escaping is the only
defence being tested. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
PAYLOAD = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    page.on("dialog", lambda d: (errors.append("dialog: " + d.message), d.dismiss()))
    pid = api.post(BASE + "/api/projects", data={"name": "P " + PAYLOAD, "platform": "generic", "description": PAYLOAD}, headers=H).json()["id"]
    req = api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall show " + PAYLOAD, "epic": PAYLOAD, "section": PAYLOAD,
                                                                   "notes": PAYLOAD, "status": "approved"}, headers=H).json()
    api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "S " + PAYLOAD, "persona": PAYLOAD, "narrative": PAYLOAD, "epic": PAYLOAD, "feature": PAYLOAD,
                                                         "acceptance_criteria": [PAYLOAD], "requirement_ids": [req["key"]], "status": "approved"}, headers=H)
    api.post(f"{BASE}/api/projects/{pid}/comments", data={"item_id": req["id"], "body": PAYLOAD}, headers=H)
    api.post(f"{BASE}/api/projects/{pid}/baselines", data={"name": "B " + PAYLOAD, "note": PAYLOAD}, headers=H)

    def clean(label):
        page.wait_for_timeout(500)
        injected = page.evaluate("document.querySelectorAll('img[src=\"x\"], #app script').length")
        check(injected == 0 and page.evaluate("window.__pwned") is None, f"{label}: markup is shown as text, nothing executes")

    for tab in ("overview", "sources", "requirements", "stories", "trace", "audit", "export"):
        page.goto(f"{BASE}/#/p/{pid}/{tab}")
        page.reload()
        page.wait_for_timeout(800)
        clean(f"{tab} page")
        if tab in ("requirements", "stories"):
            check("<img src=x" in page.inner_text("#app"), f"{tab} page: the stored text is visible literally (the check is not vacuous)")
    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.wait_for_selector("tr.clickable")
    page.locator("tr.clickable").first.click()
    page.wait_for_selector(".drawer")
    clean("requirement drawer (fields, discussion, history)")
    check("<img" in page.locator(".drawer textarea").first.input_value(), "the statement field holds the literal text")
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/#/")
    page.reload()
    page.wait_for_timeout(800)
    clean("project list")
    # the HTML report is a standalone document: markup must be escaped there too
    report = api.get(f"{BASE}/api/projects/{pid}/export?format=html").text()
    check("<img src=x" not in report and "&lt;img src=x" in report, "the HTML stakeholder report escapes user text")
    check(not errors, f"no page errors or dialogs {errors}")
    browser.close()
sys.exit(1 if failures else 0)
