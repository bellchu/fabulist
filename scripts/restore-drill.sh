#!/usr/bin/env bash
# Backup/restore drill, Docker only: seed a database through the real app, dump it the way the
# nightly CronJob does (pg_dump -Fc), restore into a FRESH database with the documented flags, then
# check row counts, app health, semantic search and baselines. Exits non-zero on any mismatch.
set -euo pipefail
cd "$(dirname "$0")/.."
net="fabulist-drill-$$"; img="pgvector/pgvector:pg16"
cleanup() { docker rm -f "$net-src" "$net-dst" "$net-app" "$net-app2" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; docker rmi "fabulist:$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$net" >/dev/null
pg() { docker run -d --rm --name "$net-$1" --network "$net" -e POSTGRES_USER=fabulist -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=fabulist "$img" >/dev/null
       until docker exec "$net-$1" pg_isready -q -h 127.0.0.1 -U fabulist -d fabulist; do sleep 1; done; }
counts() { docker exec "$1" psql -U fabulist -d fabulist -At -F' ' -c \
  "SELECT (SELECT count(*) FROM projects),(SELECT count(*) FROM documents),(SELECT count(*) FROM requirements),(SELECT count(*) FROM stories),(SELECT count(*) FROM chunks),(SELECT count(*) FROM history),(SELECT count(*) FROM baselines)"; }
app() { docker run -d --rm --name "$net-$1" --network "$net" -e DATABASE_URL="postgresql://fabulist:pw@$net-$2:5432/fabulist" -e SETTINGS_ENCRYPTION_KEY=drill "fabulist:$net" >/dev/null
        for _ in $(seq 1 30); do docker exec "$net-$1" python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8000/healthz')" 2>/dev/null && return; sleep 1; done; echo "app did not become healthy" >&2; exit 1; }
api() { docker exec -i "$net-$1" python - "$2" "$3" "${4:-}" <<'PY'
import json, sys, urllib.request
method, path, body = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request("http://127.0.0.1:8000" + path, method=method, data=body.encode() if body else None,
                             headers={"X-Requested-With": "fabulist", "Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
}
echo "== seed through the app"; docker build -q -t "fabulist:$net" . >/dev/null; pg src; app app src
pid=$(api app GET /api/projects "" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['id'] if d else '')")
[ -n "$pid" ] || pid=$(api app POST /api/projects '{"name":"Drill project","platform":"salesforce"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
api app POST "/api/projects/$pid/documents/text" '{"name":"brd.md","text":"# Orders\n## Capture\nThe system shall allow buyers to place orders online.\nThe system shall email order confirmations to buyers.\n"}' >/dev/null
for stage in requirements stories; do
  api app POST "/api/projects/$pid/runs" "{\"stage\":\"$stage\"}" >/dev/null
  for _ in $(seq 1 30); do api app GET "/api/projects/$pid/runs?limit=1" "" | grep -q '"status":"running"' || break; sleep 1; done
done
api app POST "/api/projects/$pid/baselines" '{"name":"Drill baseline","scope":"all"}' >/dev/null
before=$(counts "$net-src"); echo "source:   $before  (projects documents requirements stories passages history baselines)"
echo "== dump (as the CronJob does) and restore into a fresh database"
docker exec "$net-src" pg_dump -U fabulist -d fabulist -Fc -f /tmp/drill.dump
pg dst; docker exec "$net-src" cat /tmp/drill.dump | docker exec -i "$net-dst" pg_restore -U fabulist -d fabulist --clean --if-exists --exit-on-error
after=$(counts "$net-dst"); echo "restored: $after"
[ "$before" = "$after" ] || { echo "FAIL: row counts differ" >&2; exit 1; }
echo "== the app on the restored database"; app app2 dst
api app2 GET /api/projects "" | grep -q "Drill project" || { echo "FAIL: project missing" >&2; exit 1; }
api app2 POST "/api/projects/$pid/search" '{"query":"order confirmations"}' | grep -q "confirmations" || { echo "FAIL: semantic search returned nothing" >&2; exit 1; }
api app2 GET "/api/projects/$pid/baselines" "" | grep -q "Drill baseline" || { echo "FAIL: baseline missing" >&2; exit 1; }
echo "restore drill passed"
