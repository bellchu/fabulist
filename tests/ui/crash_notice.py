"""A bug in the page itself is not silent: an error nobody caught shows a short notice (once in a while), and harmless browser noise does not.
Run with scripts/ui-check-k8s.sh."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(viewport={"width": 1100, "height": 800}, bypass_csp=True).new_page()
    page.on("pageerror", lambda e: None)  # the errors below are on purpose
    page.goto(BASE + "/#/")
    page.wait_for_selector(".global-search")
    toasts = lambda: page.locator(".toast").count()  # noqa: E731

    page.evaluate("setTimeout(() => Promise.reject(new Error('nobody caught this')), 0)")
    page.wait_for_selector(".toast.error")
    check("went wrong" in page.locator(".toast.error").first.inner_text(), "an unhandled promise rejection shows a notice")
    check("nobody caught this" not in page.locator(".toast").first.inner_text(), "without the technical message")

    page.evaluate("setTimeout(() => { throw new Error('a second one') }, 0)")
    page.wait_for_timeout(400)
    check(toasts() == 1, "a second error straight after does not pile up notices")

    page.wait_for_timeout(5200)
    page.evaluate("window.dispatchEvent(new ErrorEvent('error', {message: 'ResizeObserver loop completed with undelivered notifications.'}))")
    page.wait_for_timeout(300)
    check(page.locator(".toast.error").count() <= 1 and "ResizeObserver" not in page.locator("body").inner_text(), "ResizeObserver noise is ignored")
    page.wait_for_timeout(6500)  # the first notice has gone
    page.evaluate("setTimeout(() => { throw new Error('after the quiet period') }, 0)")
    page.wait_for_selector(".toast.error")
    check(True, "after a quiet period a new error is reported again")
    browser.close()
sys.exit(1 if failures else 0)
