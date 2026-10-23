"""WCAG 1.4.12: with the text-spacing overrides a reader may apply (line height 1.5, letter spacing .12em, word spacing .16em, paragraph spacing 2em),
nothing is cut off and the page does not scroll sideways. Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
SPACING = "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } p { margin-bottom: 2em !important; }"
CLIPPED = """[...document.querySelectorAll('button, .btn, .badge, .chip, .tab, th')].filter((e) => e.offsetParent && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible'
  && !e.closest('.stepper'))
  .slice(0, 5).map((e) => e.tagName + '.' + e.className + ' ' + (e.textContent || '').trim().slice(0, 30))"""
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1280, 360):
        ctx = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)
        pid = ctx.request.post(BASE + "/api/projects", data={"name": "Spacing %d" % width, "platform": "generic"}, headers=H).json()["id"]
        for text in ("The system shall export invoices to CSV on request.", "The system shall email order confirmations to buyers."):
            ctx.request.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": text, "compliance_tags": ["SOX"]}, headers=H)
        ctx.request.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Export invoices", "requirement_ids": ["REQ-001"]}, headers=H)
        page = ctx.new_page()
        for route in ("", f"p/{pid}/overview", f"p/{pid}/requirements", f"p/{pid}/stories", f"p/{pid}/trace", f"p/{pid}/export", "admin/settings"):
            page.evaluate("document.querySelectorAll('.page-head').forEach((e) => e.remove())")  # the old page's header would satisfy the wait below
            page.goto(f"{BASE}/#/{route}")
            page.wait_for_selector(".page-head")
            page.add_style_tag(content=SPACING)
            page.wait_for_timeout(400)
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            clipped = page.evaluate(CLIPPED)
            label = "%s at %dpx" % (route.replace(pid, "<project>") or "home", width)
            check(over <= 0 and not clipped, f"{label}: nothing cut off with wide text spacing ({over}px over, clipped {clipped})")
        ctx.close()
    browser.close()
sys.exit(1 if failures else 0)
