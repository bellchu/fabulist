"""AI runs queue when every slot is busy: the page shows the position, the run starts by itself and the page follows, and a waiting run can be
removed. The harness allows one run at a time; a local fake AI provider with a delay keeps the first run busy. Run with scripts/ui-check-k8s.sh."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
failures = []
REPLY = json.dumps([{"statement": "The system shall export invoices to CSV on request.", "type": "functional", "actors": ["Buyer"]}])


class FakeAI(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(1.0)  # a slow provider
        body = json.dumps({"choices": [{"message": {"content": REPLY}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


server = ThreadingHTTPServer(("127.0.0.1", 9100), FakeAI)
threading.Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    prov = api.post(BASE + "/api/admin/providers", data={"name": "Slow fake", "kind": "openai", "base_url": "http://127.0.0.1:9100/v1", "model": "m", "api_key": "k"}, headers=H).json()["id"]
    api.post(f"{BASE}/api/admin/providers/{prov}/default", data={}, headers=H)

    def project(name, sections):
        pid = api.post(BASE + "/api/projects", data={"name": name, "platform": "generic"}, headers=H).json()["id"]
        text = "\n\n".join("# Area %d\nThe system shall do thing number %d for the buyer." % (i, i) for i in range(sections))
        api.post(f"{BASE}/api/projects/{pid}/documents/text", data={"name": "brd.md", "text": text}, headers=H)
        return pid

    def start(pid):
        return api.post(f"{BASE}/api/projects/{pid}/runs", data={"stage": "requirements", "scope": "all"}, headers=H)

    def latest(pid):
        return api.get(f"{BASE}/api/projects/{pid}/runs?limit=1", headers=H).json()[0]

    first, second, third = project("First", 8), project("Second", 2), project("Third", 2)
    check(start(first).json()["status"] == "running", "the first run takes the only slot")
    queued = start(second)
    check(queued.status == 202 and queued.json()["status"] == "queued", f"the second run is accepted and queued (HTTP {queued.status})")
    start(third)

    page.goto(f"{BASE}/#/p/{second}/requirements")
    page.reload()
    page.wait_for_selector("text=Waiting for a free slot")
    check(page.get_by_text("Queued", exact=True).count() >= 1 and "position 1 of 2" in page.locator(".runbox").inner_text(), "the page shows Queued and the position in line")
    check(page.get_by_role("button", name="Queued…").is_disabled(), "the start button is disabled while queued")

    # the third run is removed from the line from its own page
    page.goto(f"{BASE}/#/p/{third}/requirements")
    page.reload()
    page.get_by_role("button", name="Remove from queue").click()
    page.wait_for_selector("text=Last run was cancelled")
    check(latest(third)["status"] == "cancelled", "Remove from queue cancels the waiting run")

    # the second run starts by itself and the page follows without a reload
    page.goto(f"{BASE}/#/p/{second}/requirements")
    page.reload()
    page.wait_for_selector("text=Waiting for a free slot")
    page.wait_for_selector("text=Running…", timeout=60000)
    check(True, "when the slot frees the page switches from Queued to Running by itself")
    page.wait_for_selector("text=Last run", timeout=60000)
    check(latest(second)["status"] == "done", "the queued run finishes")
    check(api.get(f"{BASE}/api/projects/{second}/requirements", headers=H).json() != [], "and produced requirements")
    server.shutdown()
    browser.close()
sys.exit(1 if failures else 0)
