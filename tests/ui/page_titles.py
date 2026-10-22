"""Every screen has its own page title (WCAG 2.4.2): tabs, history and screen readers say where you are. Run with scripts/ui-check-k8s.sh."""
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
    pid = ctx.request.post(BASE + "/api/projects", data={"name": "Title check", "platform": "generic"}, headers=H).json()["id"]
    page = ctx.new_page()

    def title_after(hash_, waits):
        page.goto(BASE + "/" + hash_)
        page.wait_for_selector(waits)
        page.wait_for_timeout(200)
        return page.title()

    seen = {
        "home": title_after("#/", ".global-search"),
        "project overview": title_after(f"#/p/{pid}/overview", ".page-head h1:has-text('Title check')"),
        "project requirements": title_after(f"#/p/{pid}/requirements", ".tab.active:has-text('Requirements')"),
        "project audit": title_after(f"#/p/{pid}/audit", ".tab.active:has-text('Audit')"),
        "admin settings": title_after("#/admin/settings", "h1:has-text('Generation settings')"),
        "account": title_after("#/account", "h1"),
        "search": title_after("#/search/title", "text=Results for"),
    }
    for name, title in seen.items():
        check(title.endswith("Fabulist") and title != "Fabulist", "%s has a title of its own: %r" % (name, title))
    check("Title check" in seen["project requirements"] and "Requirements" in seen["project requirements"], "a project tab names the tab and the project")
    check(seen["project requirements"] != seen["project audit"], "two tabs of one project differ")
    check("Generation settings" in seen["admin settings"], "an admin screen names itself")
    check(len(set(seen.values())) == len(seen), "no two of these screens share a title")

    # a screen reader hears where a navigation led (a polite live region outside the part of the page that is rebuilt)
    page.goto(BASE + "/#/")
    page.wait_for_selector(".global-search")
    page.wait_for_timeout(300)
    live = page.locator("#route-announcer")
    check(live.get_attribute("aria-live") == "polite" and live.get_attribute("aria-atomic") == "true", "there is a polite live region")
    check(live.inner_text() == page.title() == "Projects · Fabulist", "it announces the new screen (%r)" % live.inner_text())
    page.evaluate("location.hash = '#/account'")
    page.wait_for_selector("h1:has-text('admin'), h1")
    page.wait_for_timeout(400)
    check(live.inner_text() == "Account · Fabulist", "and the next one after a navigation (%r)" % live.inner_text())
    page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")  # a redraw of the same screen is not a new announcement
    page.wait_for_timeout(400)
    check(live.inner_text() == "Account · Fabulist", "a redraw of the same screen says nothing new")
    browser.close()
sys.exit(1 if failures else 0)
