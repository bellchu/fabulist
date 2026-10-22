"""Windows High Contrast (forced colors) removes background colours: anything that says "selected", "current" or "how full" only by colour
vanishes. Selected options, bars, steps and badges must still be told apart. Run with scripts/ui-check-k8s.sh."""
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


STYLE = "([sel, props]) => { const el = document.querySelector(sel); const s = getComputedStyle(el); return props.map((p) => s.getPropertyValue(p)); }"

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, forced_colors="active", bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    pid = api.post(BASE + "/api/projects", data={"name": "High contrast", "platform": "generic"}, headers=H).json()["id"]
    api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall export invoices to CSV.", "status": "approved"}, headers=H)

    page.goto(BASE + "/#/admin/settings")
    page.wait_for_selector(".seg button.on")
    on, off = page.evaluate(STYLE, [".seg button.on", ["background-color", "color"]]), page.evaluate(STYLE, [".seg button:not(.on)", ["background-color", "color"]])
    check(on != off, "a selected option looks different from the others (%s vs %s)" % (on, off))

    page.goto(BASE + "/#/")
    page.wait_for_selector(".project-card .meter > span")
    fill = page.evaluate(STYLE, [".project-card .meter > span", ["background-color"]])
    track = page.evaluate(STYLE, [".project-card .meter", ["background-color", "border-top-width"]])
    check(fill[0] != track[0], "a progress bar's fill is visible against its track (%s on %s)" % (fill[0], track[0]))
    check(track[1] != "0px", "and the track has an outline")

    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.wait_for_selector(".tab.active")
    act = page.evaluate(STYLE, [".tab.active", ["border-bottom-width", "border-bottom-color"]])
    idle = page.evaluate(STYLE, [".tab:not(.active)", ["border-bottom-width", "border-bottom-color"]])
    check(act != idle, "the current tab is marked by more than colour (%s vs %s)" % (act, idle))
    cur = page.evaluate(STYLE, [".step.current", ["background-color"]])
    nxt = page.evaluate(STYLE, [".step:not(.current):not(.done)", ["background-color"]])
    check(cur != nxt, "the current step in the path is distinguishable (%s vs %s)" % (cur, nxt))
    badge = page.evaluate(STYLE, [".badge", ["border-top-width"]])
    check(badge[0] != "0px", "badges have an outline, so they are shapes rather than coloured text")
    browser.close()
sys.exit(1 if failures else 0)
