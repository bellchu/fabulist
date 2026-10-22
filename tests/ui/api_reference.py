"""The API reference at /docs works under the app's Content Security Policy (the browser enforces it here: nothing is bypassed), lists every
group, filters, and shows an operation's parameters and body fields. Run with scripts/ui-check-k8s.sh."""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})  # bypass_csp is deliberately NOT set
    ctx.add_init_script(urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode())  # injected outside the page's policy
    page = ctx.new_page()
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(f"{BASE}/docs")
    page.wait_for_selector(".docs-group")
    names = page.locator(".docs-group > summary").all_inner_texts()
    check(len(names) >= 8 and any(n.startswith("Runs") for n in names) and any(n.startswith("Requirements") for n in names), f"the groups are listed ({len(names)}: {[n.split(chr(10))[0] for n in names][:6]}...)")
    check("Bearer" in page.locator("#intro").inner_text() and "X-Requested-With" in page.locator("#intro").inner_text(), "the introduction explains authentication")
    font = page.evaluate("getComputedStyle(document.body).fontFamily")
    check("serif" not in font.replace("sans-serif", "") and ("Helvetica" in font or "Roboto" in font or "Segoe" in font), f"the page uses the app's sans-serif font stack ({font[:60]})")
    check(page.locator("#intro em").count() == 1 and "*" not in page.locator("#intro").inner_text(), "emphasis in the introduction is rendered, not shown as asterisks")
    total = page.locator(".docs-op").count()
    check(total > 80 and f"{total} operations" in page.locator("#count").inner_text(), f"every operation is rendered ({total})")

    page.get_by_label("Filter operations").fill("baselines compare")
    page.wait_for_function("document.querySelectorAll('.docs-group:not([hidden])').length === 1")
    shown = page.locator(".docs-op:not([hidden])").count()
    check(0 < shown <= 3 and "Baselines" in page.locator(".docs-group:not([hidden]) > summary").inner_text(), f"filtering narrows to the matching group and operations ({shown})")
    page.get_by_label("Filter operations").fill("POST /api/projects/{pid}/runs")
    page.wait_for_function("document.querySelectorAll('.docs-op:not([hidden])').length >= 1")
    op = page.locator(".docs-op:has(.docs-method.post):has(.docs-path:text-is('/api/projects/{pid}/runs'))")
    op.locator("summary").click()
    body = op.locator(".docs-body").inner_text()
    check("stories_for" in body and "uncovered" in body and "documents" in body, f"an operation shows its request body fields, with the allowed values ({body[:300]!r})")
    check("pid" in body and "responses" in body.lower() and "202" in body, "and its path parameter and responses")
    bad = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id + ': ' + v.nodes[0].html.slice(0, 80)))")
    check(not bad, f"no serious accessibility violations with an operation open {bad}")
    page.get_by_label("Filter operations").fill("")
    check(not problems, f"no console errors or Content Security Policy violations {problems[:2]}")
    browser.close()
sys.exit(1 if failures else 0)
