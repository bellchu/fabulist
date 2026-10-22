#!/usr/bin/env bash
# Backup/restore drill on the cluster, not on the developer's machine: seed a database through the real app, dump it the way the
# nightly CronJob does (pg_dump -Fc), restore into a FRESH database with the documented flags, then check row counts, app health,
# semantic search, baselines and the administrative trail on the restored copy. Temporary namespace, always deleted.
# Exits non-zero on any mismatch. (scripts/restore-drill.sh does the same with local Docker.)
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
NS="fabulist-drill-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: drill}
spec:
  restartPolicy: Never
  volumes: [{name: src, emptyDir: {}}, {name: dump, emptyDir: {}}]
  containers:
  - name: source
    image: pgvector/pgvector:pg16
    volumeMounts: [{name: dump, mountPath: /dump}]
    env: [{name: POSTGRES_USER, value: f}, {name: POSTGRES_PASSWORD, value: f}, {name: POSTGRES_DB, value: f}]
  - name: restored
    image: pgvector/pgvector:pg16
    volumeMounts: [{name: dump, mountPath: /dump}]
    env: [{name: PGPORT, value: "5433"}, {name: POSTGRES_USER, value: f}, {name: POSTGRES_PASSWORD, value: f}, {name: POSTGRES_DB, value: f}]
  - name: checker
    image: pgvector/pgvector:pg16
    command: [sleep, "1800"]
    volumeMounts: [{name: dump, mountPath: /backups}]
  - name: app
    image: ${PYTHON_IMAGE}
    command: [sleep, "3600"]
    workingDir: /src
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: SETTINGS_ENCRYPTION_KEY, value: drill}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/drill --timeout=420s >/dev/null
sql() { kubectl -n "$NS" exec drill -c "$1" -- psql -h 127.0.0.1 -p "$2" -U f -d f -At -F' ' -c "$3"; }
for c in "source 5432" "restored 5433"; do set -- $c; until kubectl -n "$NS" exec drill -c "$1" -- pg_isready -q -h 127.0.0.1 -p "$2" -U f -d f; do sleep 1; done; done
git ls-files -z --cached --others --exclude-standard | COPYFILE_DISABLE=1 tar --null -T - -cf - | kubectl -n "$NS" exec -i drill -c app -- tar -xf - -C /src
kubectl -n "$NS" exec drill -c app -- sh -c 'pip install -q --root-user-action=ignore -r requirements.txt'
start_app() {  # name port database-port
  kubectl -n "$NS" exec drill -c app -- sh -c "DATABASE_URL=postgresql://f:f@127.0.0.1:$3/f nohup uvicorn app.main:app --port $2 >/tmp/$1.log 2>&1 &"
  kubectl -n "$NS" exec drill -c app -- sh -c "for i in \$(seq 1 60); do python -c \"import urllib.request as u;u.urlopen('http://127.0.0.1:$2/healthz')\" 2>/dev/null && exit 0; sleep 1; done; echo 'app did not become healthy' >&2; exit 1"
}
drill() { kubectl -n "$NS" exec -i drill -c app -- python - "$@"; }  # the python on stdin gets: base-url, step

echo "== seed through the app"
start_app seed 8000 5432
drill http://127.0.0.1:8000 seed <<'PY'
import json, sys, time, urllib.request
base, step = sys.argv[1], sys.argv[2]
def api(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Requested-With": "fabulist", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read().decode() or "null")
def wait(pid):
    for _ in range(60):
        if api("GET", "/api/projects/%s/runs?limit=1" % pid)[0]["status"] not in ("running", "queued"):
            return
        time.sleep(1)
    raise SystemExit("run did not finish")
if step == "seed":
    pid = api("POST", "/api/projects", {"name": "Drill project", "platform": "salesforce"})["id"]
    api("POST", "/api/projects/%s/documents/text" % pid, {"name": "brd.md", "text": "# Orders\n## Capture\nThe system shall allow buyers to place orders online.\nThe system shall email order confirmations to buyers.\n"})
    for stage in ("requirements", "stories"):
        api("POST", "/api/projects/%s/runs" % pid, {"stage": stage}); wait(pid)
    api("POST", "/api/projects/%s/baselines" % pid, {"name": "Drill baseline", "scope": "all"})
    api("PUT", "/api/admin/settings", {"temperature": 0.4})
    open("/tmp/pid", "w").write(pid)
PY
COUNTS="SELECT (SELECT count(*) FROM projects),(SELECT count(*) FROM documents),(SELECT count(*) FROM requirements),(SELECT count(*) FROM stories),(SELECT count(*) FROM chunks),(SELECT count(*) FROM history),(SELECT count(*) FROM baselines),(SELECT count(*) FROM admin_audit),(SELECT count(*) FROM settings)"
before=$(sql source 5432 "$COUNTS"); echo "source:   $before  (projects documents requirements stories passages history baselines admin_audit settings)"
echo "== dump (as the CronJob does) and restore into a fresh database"
kubectl -n "$NS" exec drill -c source -- pg_dump -h 127.0.0.1 -p 5432 -U f -d f -Fc -f /dump/fabulist-drill.dump
kubectl -n "$NS" exec drill -c restored -- pg_restore -h 127.0.0.1 -p 5433 -U f -d f --clean --if-exists --exit-on-error /dump/fabulist-drill.dump
after=$(sql restored 5433 "$COUNTS"); echo "restored: $after"
[ "$before" = "$after" ] || { echo "FAIL: row counts differ" >&2; exit 1; }
echo "== the app on the restored database"
start_app restored 8001 5433
kubectl -n "$NS" exec -i drill -c app -- python - <<'PY'
import json, urllib.request
base = "http://127.0.0.1:8001"
def api(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Requested-With": "fabulist", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read().decode() or "null")
def fail(why):
    raise SystemExit("FAIL: " + why)
projects = api("GET", "/api/projects")
if [p["name"] for p in projects] != ["Drill project"]: fail("project missing")
pid = projects[0]["id"]
if not any("confirmations" in str(c) for c in api("POST", "/api/projects/%s/search" % pid, {"query": "order confirmations"})): fail("semantic search returned nothing")
if "Drill baseline" not in str(api("GET", "/api/projects/%s/baselines" % pid)): fail("baseline missing")
if not any(e["action"] == "settings changed" for e in api("GET", "/api/admin/audit")): fail("administrative trail missing")
PY
echo "== the weekly restore-check job script, against this dump (as the CronJob runs it)"
kubectl -n "$NS" exec -i drill -c checker -- sh -c 'cat > /tmp/restore-check-job.sh' < scripts/restore-check-job.sh
kubectl -n "$NS" exec drill -c checker -- env BACKUP_DIR=/backups MAX_AGE_SECONDS=3600 SCRATCH_PORT=5434 sh /tmp/restore-check-job.sh
echo "== backup retention: 7 days of dumps and the Sunday of each of the last 5 weeks"
kubectl -n "$NS" exec -i drill -c checker -- sh -c 'cat > /tmp/backup-prune-job.sh' < scripts/backup-prune-job.sh
kubectl -n "$NS" exec drill -c checker -- sh -c '
  mkdir -p /tmp/prune && cd /tmp/prune
  today=$(date -u +%s)
  mk() { d=$(date -u -d "@$((today - $1 * 86400))" +%Y%m%d); touch -d "@$((today - $1 * 86400))" "fabulist-$d-023000.dump"; }
  for n in $(seq 0 60); do mk $n; done   # one dump for each of the last 61 days
  before=$(ls | wc -l)
  BACKUP_DIR=/tmp/prune sh /tmp/backup-prune-job.sh >/dev/null
  left=$(ls | wc -l)
  recent=$(for n in 0 1 2 3 4 5 6 7; do d=$(date -u -d "@$((today - n * 86400))" +%Y%m%d); ls fabulist-$d-* 2>/dev/null; done | wc -l)
  [ "$recent" = 8 ] || { echo "FAIL: dumps of the last week were pruned ($recent of 8 left)" >&2; exit 1; }
  sundays=$(for n in $(seq 8 35); do d=$(date -u -d "@$((today - n * 86400))" +%u); [ "$d" = 7 ] && echo x; done | wc -l)
  [ "$left" = "$((8 + sundays))" ] || { echo "FAIL: $left dumps left; expected the last 8 days plus $sundays Sundays" >&2; exit 1; }
  old=$(ls | while read f; do d=${f#fabulist-}; d=${d%%-*}; s=$(date -u -d "$d" +%u); echo $s; done | sort -u)
  # everything older than a week that is left was made on a Sunday, and nothing is older than 35 days
  for f in $(ls); do d=${f#fabulist-}; d=${d%%-*}; age=$(( (today - $(stat -c %Y $f)) / 86400 )); [ "$age" -le 7 ] || [ "$(date -u -d "$d" +%u)" = 7 ] || { echo "FAIL: $f is $age days old and not a Sunday dump" >&2; exit 1; }; [ "$age" -le 35 ] || { echo "FAIL: $f is older than 35 days" >&2; exit 1; }; done
  echo "retention ok: $before dumps, $left kept (last week all kept, older ones only Sundays up to 35 days)"
'
echo "restore drill passed"
