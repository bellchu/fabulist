"""Read-only smoke test of a running installation (the live one): every page loads without script errors, failed requests or serious accessibility
problems, in light and dark. Writes nothing. Run with scripts/live-smoke-k8s.sh."""
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE_URL"].rstrip("/")
AUTH = {"username": os.environ["BASIC_USER"], "password": os.environ["BASIC_PASSWORD"]} if os.environ.get("BASIC_USER") else None
AXE = urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode()
problems = []


def note(where, what):
    problems.append("%s: %s" % (where, what))
    print("FAIL %s: %s" % (where, what))


with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme, http_credentials=AUTH, bypass_csp=True)
        page = ctx.new_page()
        where = [""]
        page.on("pageerror", lambda e: note(where[0], "script error " + str(e)[:150]))
        page.on("console", lambda m: m.type == "error" and "favicon" not in m.text and note(where[0], "console error " + m.text[:150]))
        page.on("response", lambda r: r.status >= 400 and note(where[0], "HTTP %d %s" % (r.status, r.url[len(BASE):][:80])))
        page.goto(BASE + "/#/")
        page.wait_for_selector(".global-search")
        projects = page.evaluate("fetch('/api/projects').then(r => r.json())")
        pages = ["#/", "#/account", "#/search/the"] + ["#/admin/" + t for t in ("overview", "providers", "settings", "taxonomy", "integrations", "users", "activity")]
        for project in projects[:3]:
            pages += ["#/p/%s/%s" % (project["id"], tab) for tab in ("overview", "sources", "requirements", "stories", "trace", "audit", "export")]
        pages.append("/docs")
        for target in pages:
            where[0] = "%s %s" % (scheme, target)
            page.goto(BASE + ("/" + target if target.startswith("#") else target))
            page.wait_for_timeout(900)
            page.add_script_tag(content=AXE) if not page.evaluate("typeof axe !== 'undefined'") else None
            bad = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id + ' x' + v.nodes.length))")
            if bad:
                note(where[0], "accessibility " + ", ".join(bad))
            else:
                print("ok   %s" % where[0])
        ctx.close()
    browser.close()
print("\nLIVE SMOKE OK" if not problems else "\nLIVE SMOKE: %d problem(s)" % len(problems))
sys.exit(1 if problems else 0)
