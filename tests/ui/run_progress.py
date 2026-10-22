"""A running AI run shows how far along it is and roughly how long is left, from a recorded start time, so it survives a reload. A slow fake AI
provider keeps the run going long enough to watch. Run with scripts/ui-check-k8s.sh."""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
failures = []
REPLY = json.dumps([{"statement": "The system shall export invoices to CSV on request.", "type": "functional", "actors": ["Buyer"]}])


class FakeAI(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(1.2)
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


server = ThreadingHTTPServer(("127.0.0.1", 9101), FakeAI)
threading.Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    api, page = ctx.request, ctx.new_page()
    was_default = next((x["id"] for x in api.get(BASE + "/api/admin/providers", headers=H).json()["providers"] if x["is_default"]), None)  # put back at the end: later tests share this database
    prov = api.post(BASE + "/api/admin/providers", data={"name": "Slow fake", "kind": "openai", "base_url": "http://127.0.0.1:9101/v1", "model": "m", "api_key": "k"}, headers=H).json()["id"]
    api.post(f"{BASE}/api/admin/providers/{prov}/default", data={}, headers=H)
    api.put(f"{BASE}/api/admin/settings", data={"ai_concurrency": 1, "max_chunks_per_run": 200}, headers=H)  # one section at a time: a steady, watchable pace
    pid = api.post(BASE + "/api/projects", data={"name": "Progress", "platform": "generic"}, headers=H).json()["id"]
    text = "\n\n".join("# Area %d\nThe system shall do thing number %d for the buyer." % (i, i) for i in range(14))
    api.post(f"{BASE}/api/projects/{pid}/documents/text", data={"name": "brd.md", "text": text}, headers=H)
    api.post(f"{BASE}/api/projects/{pid}/runs", data={"stage": "requirements", "scope": "all", "documents": "new"}, headers=H)

    page.goto(f"{BASE}/#/p/{pid}/requirements")
    page.reload()
    page.wait_for_selector("#run-progress")
    bar = page.locator("#run-bar")
    page.wait_for_function("document.querySelector('#run-bar') && !document.querySelector('#run-bar').hidden", timeout=30000)
    first = int(bar.get_attribute("aria-valuenow"))
    check(bar.get_attribute("role") == "progressbar" and bar.get_attribute("aria-label"), "a real progress bar (role=progressbar, with a name) replaces the endless animation")
    page.wait_for_function("document.querySelector('#run-pace') && document.querySelector('#run-pace').innerText.includes('left')", timeout=40000)
    pace = page.locator("#run-pace").inner_text()
    check(" of 14 done" in pace and "left" in pace, f"it says how many are done and roughly how long is left ({pace.strip()!r})")
    page.add_script_tag(content=urllib.request.urlopen("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js", timeout=30).read().decode())
    bad = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id))")
    check(not bad, f"the running state has no serious accessibility violations {bad}")
    later = int(page.locator("#run-bar").get_attribute("aria-valuenow"))
    check(later > first, f"the bar moves as the run goes on ({first}% -> {later}%)")

    page.reload()  # the start time is recorded on the server, so a reload still knows the pace
    page.wait_for_function("document.querySelector('#run-pace') && document.querySelector('#run-pace').innerText.includes('left')", timeout=30000)
    check(True, "after a reload the estimate is still there")
    page.wait_for_selector("text=Last run", timeout=60000)
    check(True, "and the panel goes back to the finished summary when the run ends")
    server.shutdown()
    if was_default:
        api.post(f"{BASE}/api/admin/providers/{was_default}/default", data={}, headers=H)
    api.put(f"{BASE}/api/admin/settings", data={"ai_concurrency": 4, "max_chunks_per_run": 200}, headers=H)
    browser.close()
sys.exit(1 if failures else 0)
