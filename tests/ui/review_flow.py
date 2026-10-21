"""Review at volume: saving keeps the reader's place, Approve & next / Prev / Next / shortcuts step through items,
the drawer footer fits a phone. Run with scripts/ui-check-k8s.sh (needs a running app and Playwright)."""
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))
    pid = api.post(BASE + "/api/projects", data={"name": "Review flow", "platform": "generic"}, headers=H).json()["id"]
    for i in range(120):
        api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability %d." % i, "section": "S%d" % (i // 20)}, headers=H)
        api.post(f"{BASE}/api/projects/{pid}/stories", data={"title": "Story %d" % i, "persona": "buyer", "narrative": "As a buyer, I want %d, so that value." % i,
                                                          "acceptance_criteria": ["Given a, when b, then c"], "epic": "E", "feature": "F"}, headers=H)
    scroll_y = lambda: page.evaluate("Math.round(window.scrollY)")
    drawer_key = lambda: page.locator(".drawer .muted.small").first.inner_text().split(" ·")[0]

    def wait_key_change(old):
        page.wait_for_function("(k)=>document.querySelector('.drawer .muted.small')?.innerText.split(' ·')[0]!==k", arg=old)

    for noun, tab, row_sel in (("requirement", "requirements", "tr.clickable"), ("story", "stories", "tr.clickable")):
        page.goto(f"{BASE}/#/p/{pid}/{tab}")
        page.reload()
        if tab == "stories":
            page.get_by_role("button", name="List").click()
        page.wait_for_selector(row_sel)
        row = page.locator(row_sel).nth(70)
        row.scroll_into_view_if_needed()
        y0 = scroll_y()
        row.click()
        page.wait_for_selector(".drawer")
        first = drawer_key()
        page.get_by_role("button", name="Approve & next").click()
        wait_key_change(first)
        second = drawer_key()
        check(second != first, f"{noun}: Approve & next opens the next item ({first} -> {second})")
        page.get_by_role("button", name=f"Previous {noun}").click()
        page.wait_for_function("(k)=>document.querySelector('.drawer .muted.small')?.innerText.split(' ·')[0]===k", arg=first)
        check(drawer_key() == first, f"{noun}: Prev returns to {first}")
        page.wait_for_function("document.activeElement.closest('.drawer')")  # the drawer moves focus into itself shortly after opening
        page.keyboard.press("Alt+ArrowRight")
        wait_key_change(first)
        check(drawer_key() == second, f"{noun}: Alt+Right steps forward from the close button")
        page.locator(".drawer textarea").first.fill("edited text")
        page.get_by_role("button", name=f"Next {noun}").click()
        check(page.get_by_role("button", name="Discard").count() == 1, f"{noun}: unsaved edits prompt before navigating")
        page.get_by_role("button", name="Cancel").click()
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".drawer", state="detached")
        check(abs(scroll_y() - y0) < 5, f"{noun}: scroll position kept after saving ({y0} -> {scroll_y()})")
        check(page.locator("tbody .badge.green").count() >= 1, f"{noun}: list shows the approval without a reload")

    # phone width: the drawer footer must fit
    phone = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
    mp = phone.new_page()
    mp.goto(f"{BASE}/#/p/{pid}/requirements")
    mp.reload()
    mp.wait_for_selector("tr.clickable")
    mp.locator("tr.clickable").nth(1).click()
    mp.wait_for_selector(".drawer-foot")
    over = mp.evaluate("(()=>{const f=document.querySelector('.drawer-foot');return f.scrollWidth-f.clientWidth})()")
    check(over <= 0, f"phone: drawer footer fits ({over}px overflow)")
    check(not errors, f"no page errors {errors}")
    browser.close()
sys.exit(1 if failures else 0)
