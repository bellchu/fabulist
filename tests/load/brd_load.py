"""Load scenarios for BRD intake, run against a live app (BASE_URL) with the offline AI provider, so nothing is spent on AI.

  burst   20 requesters submit 20 BRDs each at the same moment (one project per requester), then all start extraction
  volume  one project holding 1,000+ BRDs, then the everyday operations on it, with other users working meanwhile

Run with scripts/load-test-k8s.sh. Exit status is non-zero when a budget below is missed."""
import io
import os
import statistics
import sys
import threading
import time

import httpx

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
H = {"X-Requested-With": "fabulist"}
REQUESTERS = int(os.environ.get("REQUESTERS", 20))
BRDS_EACH = int(os.environ.get("BRDS_EACH", 20))
VOLUME_BRDS = int(os.environ.get("VOLUME_BRDS", 1100))
BRD_KB = int(os.environ.get("BRD_KB", 30))
VOLUME_KB = int(os.environ.get("VOLUME_KB", 10))
budget_misses = []
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")


def memory(label=""):
    """(resident MiB, peak MiB, limit MiB) of the app process, from its own /metrics; printed when a label is given."""
    text = httpx.get(BASE + "/metrics", headers={"Authorization": "Bearer " + METRICS_TOKEN}, timeout=30).text
    get = lambda name: int(next(line.split()[1] for line in text.splitlines() if line.startswith(name + " "))) // 1048576  # noqa: E731
    out = (get("fabulist_process_resident_memory_bytes"), get("fabulist_process_peak_resident_memory_bytes"), get("fabulist_memory_limit_bytes"))
    if label:
        print("  memory %-34s resident %5d MiB, peak %5d MiB, limit %d MiB" % (label, *out))
    return out


def brd_text(tag: str, kb: int) -> str:
    """A BRD-like document with unique sentences (so nothing deduplicates) of roughly `kb` kilobytes."""
    out, n, size = ["# Business requirements: %s\n" % tag], 0, 0
    while size < kb * 1024:
        n += 1
        if n % 6 == 1:
            out.append("\n## %d. Capability area %d for %s\n" % (n // 6 + 1, n // 6 + 1, tag))
        line = ("BR-%s-%03d: The system shall allow an authorised %s user to process request number %d, validate it against the business rules "
                "for area %d, record who changed what and when, and notify the requester within %d seconds." % (tag, n, ["buyer", "approver", "auditor", "planner"][n % 4], n, n % 17, 5 + n % 55))
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def docx_bytes(text: str) -> bytes:
    from docx import Document
    doc = Document()
    for line in text.splitlines():
        if line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.strip():
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class Timings:
    def __init__(self):
        self.lat, self.status, self.lock = {}, {}, threading.Lock()

    def add(self, label, seconds, status):
        with self.lock:
            self.lat.setdefault(label, []).append(seconds)
            self.status.setdefault(label, {}).setdefault(status, 0)
            self.status[label][status] += 1

    def report(self):
        print("%-26s %6s %8s %8s %8s %8s  %s" % ("request", "n", "p50", "p95", "p99", "max", "statuses"))
        for label, xs in sorted(self.lat.items()):
            xs = sorted(xs)
            q = lambda f: xs[min(len(xs) - 1, int(len(xs) * f))]  # noqa: E731
            print("%-26s %6d %7.2fs %7.2fs %7.2fs %7.2fs  %s" % (label, len(xs), statistics.median(xs), q(.95), q(.99), xs[-1], self.status[label]))


def timed(client, timings, label, method, url, **kw):
    t = time.time()
    try:
        r = client.request(method, url, **kw)
        timings.add(label, time.time() - t, r.status_code)
        return r
    except Exception as exc:  # noqa: BLE001 - a timeout or reset is a result, not a crash
        timings.add(label, time.time() - t, type(exc).__name__)
        return None


def monitor(stop, timings):
    """What a bystander experiences while the others work: a health probe and an ordinary read every half second."""
    with httpx.Client(base_url=BASE, headers=H, timeout=15) as c:
        while not stop.is_set():
            timed(c, timings, "bystander /healthz", "GET", "/healthz")
            timed(c, timings, "bystander GET projects", "GET", "/api/projects")
            time.sleep(0.5)


def budget(ok, what):
    print(("  ok   " if ok else "  MISS ") + what)
    if not ok:
        budget_misses.append(what)


def raise_section_limit():
    """20 BRDs of this size are ~460 sections, over the default 'Max sections per run' (200): without this an extraction is refused up front
    (a deliberate cost guard; see the estimate). An administrator running a large intake raises it, so the scenario does too."""
    with httpx.Client(base_url=BASE, headers=H, timeout=30) as c:
        r = c.put("/api/admin/settings", json={"max_chunks_per_run": 2000})
        print("administrator raised 'Max sections per run' to 2000 -> HTTP %s" % r.status_code)


def burst():
    print("\n== burst: %d requesters x %d BRDs (~%d KB each) at the same moment" % (REQUESTERS, BRDS_EACH, BRD_KB))
    raise_section_limit()
    timings, bystander, stop = Timings(), Timings(), threading.Event()
    barrier, results, errors = threading.Barrier(REQUESTERS + 1), {}, []

    def requester(i):
        tag = "R%02d" % i
        try:
            with httpx.Client(base_url=BASE, headers=H, timeout=120) as c:
                texts = [(("%s-brd-%02d" % (tag, k)), brd_text("%s-%02d" % (tag, k), BRD_KB)) for k in range(BRDS_EACH)]
                files = [(n + (".docx" if k % 4 == 3 else ".md"), docx_bytes(t) if k % 4 == 3 else t.encode()) for k, (n, t) in enumerate(texts)]
                pid = c.post("/api/projects", json={"name": "Load " + tag, "platform": "generic"}).json()["id"]
                barrier.wait()
                started = time.time()
                for name, data in files:
                    timed(c, timings, "upload BRD", "POST", f"/api/projects/{pid}/documents", files={"file": (name, data, "application/octet-stream")})
                uploaded = time.time() - started
                retries = 0
                while True:
                    r = timed(c, timings, "start extraction", "POST", f"/api/projects/{pid}/runs", json={"stage": "requirements", "scope": "all"})
                    if r is not None and r.status_code == 202:
                        break
                    retries += 1
                    time.sleep(3)  # the real Retry-After is 60 s; shortened so the test finishes
                waited = time.time() - started - uploaded
                while True:
                    runs = c.get(f"/api/projects/{pid}/runs").json()
                    if runs and runs[0]["status"] != "running":
                        break
                    time.sleep(1)
                results[i] = dict(pid=pid, uploaded=uploaded, retries=retries, waited_for_slot=waited, total=time.time() - started, status=runs[0]["status"], items=runs[0]["item_count"])
        except Exception as exc:  # noqa: BLE001
            errors.append("requester %d: %r" % (i, exc))
            try:
                barrier.abort()
            except Exception:  # noqa: BLE001
                pass

    threads = [threading.Thread(target=requester, args=(i,)) for i in range(REQUESTERS)]
    mon = threading.Thread(target=monitor, args=(stop, bystander))
    mon.start()
    for t in threads:
        t.start()
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass
    t0 = time.time()
    for t in threads:
        t.join()
    stop.set()
    mon.join()
    wall = time.time() - t0
    print("all requesters finished in %.0f s" % wall)
    timings.report()
    print()
    bystander.report()
    ups = [r["uploaded"] for r in results.values()]
    print("\nper requester: uploads of %d BRDs took %.0f-%.0f s (median %.0f s); extraction waited for a slot %.0f-%.0f s; retries total %d" % (
        BRDS_EACH, min(ups), max(ups), statistics.median(ups), min(r["waited_for_slot"] for r in results.values()), max(r["waited_for_slot"] for r in results.values()),
        sum(r["retries"] for r in results.values())))
    outcome = {}
    for r in results.values():
        key = "%s, %s requirements" % (r["status"], "some" if r["items"] else "no")
        outcome[key] = outcome.get(key, 0) + 1
    print("extraction outcomes: %s" % outcome)
    if any(r["status"] != "done" or not r["items"] for r in results.values()):
        with httpx.Client(base_url=BASE, headers=H, timeout=60) as c:
            bad = next(r for r in results.values() if r["status"] != "done" or not r["items"])
            print("example of a run that did not finish cleanly:", c.get(f"/api/projects/{bad['pid']}/runs").json()[0])
    with httpx.Client(base_url=BASE, headers=H, timeout=60) as c:
        wrong = [r["pid"] for r in results.values() if len(c.get(f"/api/projects/{r['pid']}/documents").json()) != BRDS_EACH]
    print("budgets:")
    budget(not errors, "no requester crashed %s" % errors[:2])
    budget(len(results) == REQUESTERS and all(r["status"] == "done" and r["items"] > 0 for r in results.values()), "every requester ended with a finished extraction that produced requirements")
    budget(not wrong, "every project holds exactly its own %d BRDs (%d wrong)" % (BRDS_EACH, len(wrong)))
    upload_codes = timings.status.get("upload BRD", {})
    budget(set(upload_codes) == {201}, "every upload succeeded %s" % upload_codes)
    hz = sorted(bystander.lat.get("bystander /healthz", [99]))
    budget(hz[-1] < 5, "the health probe never waited more than 5 s while the burst ran (max %.1f s; the pod's probe timeout is 1 s)" % hz[-1])
    budget(set(bystander.status.get("bystander /healthz", {})) <= {200}, "no health probe failed %s" % bystander.status.get("bystander /healthz"))
    _, peak, limit = memory("after the burst")
    budget(limit == 0 or peak < limit * 0.85, "memory peak stayed under 85%% of the container limit (peak %d MiB of %d MiB)" % (peak, limit))


def volume():
    print("\n== volume: one project with %d BRDs (~%d KB each)" % (VOLUME_BRDS, VOLUME_KB))
    timings, bystander, stop = Timings(), Timings(), threading.Event()
    with httpx.Client(base_url=BASE, headers=H, timeout=300) as c:
        pid = c.post("/api/projects", json={"name": "Volume", "platform": "generic"}).json()["id"]
    seeded, errors, lock = [0], [], threading.Lock()

    def seed(lo, hi):
        with httpx.Client(base_url=BASE, headers=H, timeout=300) as c:
            for k in range(lo, hi):
                r = timed(c, timings, "seed upload", "POST", f"/api/projects/{pid}/documents/text", json={"name": "BRD %04d" % k, "text": brd_text("V%04d" % k, VOLUME_KB)})
                with lock:
                    if r is not None and r.status_code == 201:
                        seeded[0] += 1
                    else:
                        errors.append(k)

    workers, per = 10, VOLUME_BRDS // 10
    t0 = time.time()
    ts = [threading.Thread(target=seed, args=(w * per, (w + 1) * per if w < workers - 1 else VOLUME_BRDS)) for w in range(workers)]
    mon = threading.Thread(target=monitor, args=(stop, bystander))
    mon.start()
    [t.start() for t in ts]
    [t.join() for t in ts]
    seed_s = time.time() - t0
    print("seeded %d BRDs with %d concurrent uploaders in %.0f s (%.1f BRDs/s)" % (seeded[0], workers, seed_s, seeded[0] / seed_s))
    memory("after storing the BRDs")
    ops = Timings()
    with httpx.Client(base_url=BASE, headers=H, timeout=300) as c:
        for _ in range(3):
            timed(c, ops, "GET documents (list)", "GET", f"/api/projects/{pid}/documents")
            timed(c, ops, "GET project", "GET", f"/api/projects/{pid}")
            timed(c, ops, "GET projects (home)", "GET", "/api/projects")
        memory("after listing documents / projects")
        for _ in range(3):
            timed(c, ops, "GET runs/estimate", "GET", f"/api/projects/{pid}/runs/estimate?stage=requirements&scope=all")
        memory("after the extraction estimates")
        for _ in range(3):
            timed(c, ops, "POST search", "POST", f"/api/projects/{pid}/search", json={"query": "approver processes request and notifies the requester"})
            timed(c, ops, "POST ask (offline)", "POST", f"/api/projects/{pid}/ask", json={"question": "Who is notified when a request is processed?"})
        memory("after search and ask")
        r = c.get(f"/api/projects/{pid}/documents")
        print("document list payload: %.0f KB for %d rows" % (len(r.content) / 1024, len(r.json())))
        t = time.time()
        a = c.get(f"/api/projects/{pid}/archive.json")
        print("project archive: %.1f MB in %.1f s (status %s)" % (len(a.content) / 1048576, time.time() - t, a.status_code))
        del a
        memory("after the project archive export")
        est = c.get(f"/api/projects/{pid}/runs/estimate?stage=requirements&scope=all").json()
        print("extraction estimate over %d BRDs: %s requests, over_limit=%s, limit=%s" % (VOLUME_BRDS, est.get("requests"), est.get("over_limit"), est.get("limit")))
        start = c.post(f"/api/projects/{pid}/runs", json={"stage": "requirements", "scope": "all"})
        print("start extraction on %d BRDs -> HTTP %s %s" % (VOLUME_BRDS, start.status_code, start.text[:140]))
        run_s = None
        if start.status_code == 202:
            t = time.time()
            while time.time() - t < 900:
                runs = c.get(f"/api/projects/{pid}/runs").json()
                if runs[0]["status"] != "running":
                    run_s = time.time() - t
                    print("extraction finished: %s, %s items in %.0f s" % (runs[0]["status"], runs[0]["item_count"], run_s))
                    break
                time.sleep(2)
    stop.set()
    mon.join()
    ops.report()
    print()
    bystander.report()
    print("budgets:")
    budget(seeded[0] == VOLUME_BRDS and not errors, "all %d BRDs were stored (%d failed)" % (VOLUME_BRDS, len(errors)))
    p95 = lambda label: sorted(ops.lat.get(label, [999]))[-1]  # noqa: E731  (3 samples: the max is the honest number)
    budget(p95("GET documents (list)") < 3, "listing %d documents answers within 3 s (%.2f s)" % (VOLUME_BRDS, p95("GET documents (list)")))
    budget(p95("GET projects (home)") < 2, "the project list answers within 2 s (%.2f s)" % p95("GET projects (home)"))
    budget(p95("POST search") < 3, "passage search over %d BRDs answers within 3 s (%.2f s)" % (VOLUME_BRDS, p95("POST search")))
    budget(max(bystander.lat.get("bystander /healthz", [99])) < 5, "the health probe never waited more than 5 s during the volume work")
    _, peak, limit = memory()
    budget(limit == 0 or peak < limit * 0.85, "memory peak stayed under 85%% of the container limit (peak %d MiB of %d MiB)" % (peak, limit))


if __name__ == "__main__":
    which = sys.argv[1:] or ["burst", "volume"]
    for name in which:
        {"burst": burst, "volume": volume}[name]()
    print("\n%s" % ("LOAD TEST OK" if not budget_misses else "LOAD TEST: %d budget(s) missed: %s" % (len(budget_misses), budget_misses)))
    sys.exit(1 if budget_misses else 0)
