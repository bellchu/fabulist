"""Reading a large intake in batches: BRDs show as New, 'New BRDs only' reads as many as fit the section limit and adds to the requirements,
reviewed ones are untouched, and 'start over' still warns before it replaces anything. Run with scripts/ui-check-k8s.sh."""
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
    api, page = ctx.request, ctx.new_page()
    api.put(f"{BASE}/api/admin/settings", data={"max_chunks_per_run": 5}, headers=H)
    pid = api.post(BASE + "/api/projects", data={"name": "Intake", "platform": "generic"}, headers=H).json()["id"]
    for n in range(5):  # two sections each: a limit of 5 fits two BRDs per run
        text = "\n\n".join("# BRD %d part %d\nThe system shall handle case %d-%d for the buyer and record it." % (n, i, n, i) for i in range(2))
        api.post(f"{BASE}/api/projects/{pid}/documents/text", data={"name": "brd-%d.md" % n, "text": text}, headers=H)

    page.goto(f"{BASE}/#/p/{pid}/sources")
    page.reload()
    page.wait_for_selector("text=brd-0.md")
    check(page.get_by_text("New", exact=True).count() == 5, "Sources: all five BRDs are marked New")

    def runs():
        return api.get(f"{BASE}/api/projects/{pid}/runs?limit=50", headers=H).json()

    def finished_runs(count):
        """Wait until `count` runs exist and the newest is done (the page's own text flickers; the API does not)."""
        for _ in range(150):
            r = runs()
            if len(r) >= count and r[0]["status"] not in ("running", "queued"):
                return r
            page.wait_for_timeout(200)
        raise AssertionError("run %d did not finish" % count)

    def requirements_tab():
        page.goto(f"{BASE}/#/p/{pid}/requirements")
        page.reload()
        page.wait_for_selector(".runbox")

    requirements_tab()
    select = page.get_by_label("BRDs to read")
    check(select.locator("option").count() == 1 and "New BRDs only (5)" in select.inner_text(), "first run: the only choice is the new BRDs (nothing to start over)")
    page.get_by_role("button", name="Extract new BRDs").click()
    finished_runs(1)
    page.reload()
    page.wait_for_selector("text=Last run", timeout=30000)
    reqs = api.get(f"{BASE}/api/projects/{pid}/requirements", headers=H).json()
    check(len(reqs) == 4, f"the first run read two BRDs (4 sections) and added {len(reqs)} requirements")
    check("New BRDs only (3)" in page.get_by_label("BRDs to read").inner_text(), "the page now counts 3 BRDs still new")

    api.patch(f"{BASE}/api/projects/{pid}/requirements/{reqs[0]['id']}", data={"status": "approved", "notes": "Signed off."}, headers=H)
    for n in (2, 3):
        page.get_by_role("button", name="Extract new BRDs").click()
        finished_runs(n)
        page.reload()
        page.wait_for_selector("text=Last run")
    after = api.get(f"{BASE}/api/projects/{pid}/requirements", headers=H).json()
    check(len(after) == 10 and [r["key"] for r in after] == ["REQ-%03d" % i for i in range(1, 11)], f"three runs read all five BRDs: {len(after)} requirements, numbering continues")
    kept = next(r for r in after if r["id"] == reqs[0]["id"])
    check(kept["status"] == "approved" and kept["notes"] == "Signed off.", "the reviewed requirement was not touched by the later runs")
    check(page.get_by_label("BRDs to read").locator("option").count() == 1 and "start over" in page.get_by_label("BRDs to read").inner_text(), "nothing is new any more: only 'start over' remains")

    page.goto(f"{BASE}/#/p/{pid}/sources")
    page.reload()
    page.wait_for_selector("text=brd-0.md")
    check(page.get_by_text("New", exact=True).count() == 0, "Sources: no BRD is marked New any more")

    requirements_tab()
    page.get_by_role("button", name="Re-extract").click()
    page.wait_for_selector("text=replaces all requirements")
    check(page.get_by_text("including your edits").count() == 1, "starting over warns that edits are replaced")
    check(page.get_by_text("reads 2 of the 5 BRDs").count() == 1 and page.get_by_text("3 more will be left").count() == 1, "starting over says it reads a batch and how many remain")
    page.get_by_role("button", name="Cancel").click()
    check(len(api.get(f"{BASE}/api/projects/{pid}/requirements", headers=H).json()) == 10, "cancelling the warning changed nothing")
    page.get_by_role("button", name="Re-extract").click()
    page.wait_for_selector(".modal")
    page.locator(".modal").get_by_role("button", name="Re-extract").click()
    finished_runs(4)
    check(len(api.get(f"{BASE}/api/projects/{pid}/requirements", headers=H).json()) == 4, "start over replaced the requirements with the first batch (2 BRDs)")
    page.reload()
    page.wait_for_selector(".runbox")
    check("New BRDs only (3)" in page.get_by_label("BRDs to read").inner_text(), "and the other three BRDs are New again")
    api.put(f"{BASE}/api/admin/settings", data={"max_chunks_per_run": 200}, headers=H)
    browser.close()
sys.exit(1 if failures else 0)
