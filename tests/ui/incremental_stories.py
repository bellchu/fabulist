"""Writing stories for new requirements only: the run panel offers requirements without stories (adds, in batches that fit the limit), the
reviewed stories are untouched, and 'start over' still warns before it replaces anything. Run with scripts/ui-check-k8s.sh."""
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
    api.put(f"{BASE}/api/admin/settings", data={"max_chunks_per_run": 2}, headers=H)
    pid = api.post(BASE + "/api/projects", data={"name": "Backlog", "platform": "generic"}, headers=H).json()["id"]
    for n in range(3):  # three feature areas = three groups; a limit of 2 fits two groups per run
        api.post(f"{BASE}/api/projects/{pid}/requirements", data={"statement": "The system shall support capability %d for the buyer." % n, "epic": "Orders",
                                                                  "section": "Area %d" % n, "status": "approved"}, headers=H)

    def runs():
        return api.get(f"{BASE}/api/projects/{pid}/runs?limit=50", headers=H).json()

    def stories():
        return api.get(f"{BASE}/api/projects/{pid}/stories", headers=H).json()

    def finished_runs(count):
        for _ in range(150):
            r = runs()
            if len(r) >= count and r[0]["status"] not in ("running", "queued"):
                return r
            page.wait_for_timeout(200)
        raise AssertionError("run %d did not finish" % count)

    def stories_tab():
        page.goto(f"{BASE}/#/p/{pid}/stories")
        page.reload()
        page.wait_for_selector(".runbox")

    stories_tab()
    select = page.get_by_label("Requirements to write stories for")
    check(select.locator("option").count() == 1 and "without stories (3)" in select.inner_text(), "first run: the only choice is the requirements without stories")
    page.get_by_role("button", name="Generate for new requirements").click()
    finished_runs(1)
    page.reload()
    page.wait_for_selector(".runbox")
    first = stories()
    check(len(first) == 2, f"the first run wrote stories for two groups (the limit): {len(first)} stories")
    check("without stories (1)" in page.get_by_label("Requirements to write stories for").inner_text(), "the page now counts 1 requirement still without a story")

    api.patch(f"{BASE}/api/projects/{pid}/stories/{first[0]['id']}", data={"status": "approved", "notes": "Signed off."}, headers=H)
    page.get_by_role("button", name="Generate for new requirements").click()
    finished_runs(2)
    page.reload()
    page.wait_for_selector(".runbox")
    after = stories()
    check(len(after) == 3 and [s["key"] for s in after] == ["US-001", "US-002", "US-003"], f"the second run added the last story, numbering continues ({len(after)})")
    kept = next(s for s in after if s["id"] == first[0]["id"])
    check(kept["status"] == "approved" and kept["notes"] == "Signed off.", "the reviewed story was not touched")
    select = page.get_by_label("Requirements to write stories for")
    check(select.locator("option").count() == 1 and "start over" in select.inner_text(), "everything is covered: only 'start over' remains")

    # starting over works within the same limit (2 groups of the 3): the first batch replaces everything, the rest have no story
    page.get_by_role("button", name="Regenerate").click()
    page.wait_for_selector(".modal")
    check(page.get_by_text("replaces the current stories").count() == 1 or page.get_by_text("Regenerating replaces").count() == 1, "starting over warns that stories are replaced")
    check(page.get_by_text("writes stories for 2 of the 3 requirements").count() == 1 and page.get_by_text("1 more will be left").count() == 1, "and says it writes a batch and how many remain")
    page.get_by_role("button", name="Cancel").click()
    check(len(stories()) == 3, "cancelling the warning changed nothing")
    page.get_by_role("button", name="Regenerate").click()
    page.wait_for_selector(".modal")
    page.locator(".modal").get_by_role("button", name="Regenerate").click()
    finished_runs(3)
    page.reload()
    page.wait_for_selector(".runbox")
    check(len(stories()) == 2 and "without stories (1)" in page.get_by_label("Requirements to write stories for").inner_text(), "start over replaced the stories with the first batch (2) and one requirement has none again")
    api.put(f"{BASE}/api/admin/settings", data={"max_chunks_per_run": 200}, headers=H)
    browser.close()
sys.exit(1 if failures else 0)
