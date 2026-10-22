#!/usr/bin/env bash
# Browser checks on the cluster, not on the developer's machine: starts the app from the working tree against a
# throwaway pgvector database in a temporary namespace and runs each tests/ui/*.py (Playwright, headless Chromium)
# against it. The namespace is always deleted. Usage: scripts/ui-check-k8s.sh [tests/ui/one.py ...]
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
# Docker Hub intermittently refuses to pull on the node (digest/size validation); the AWS public mirror serves the same official image.
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
scripts_to_run=("$@"); [ "${#scripts_to_run[@]}" -gt 0 ] || scripts_to_run=(tests/ui/*.py)
NS="fabulist-ui-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: ui}
spec:
  restartPolicy: Never
  volumes: [{name: src, emptyDir: {}}]
  containers:
  - name: db
    image: pgvector/pgvector:pg16
    env: [{name: POSTGRES_USER, value: f}, {name: POSTGRES_PASSWORD, value: f}, {name: POSTGRES_DB, value: f}]
  - name: app
    image: ${PYTHON_IMAGE}
    command: [sleep, "3600"]
    workingDir: /src
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: DATABASE_URL, value: "postgresql://f:f@127.0.0.1:5432/f"}, {name: SETTINGS_ENCRYPTION_KEY, value: ui-check}, {name: MAX_CONCURRENT_RUNS, value: "1"}]  # one slot, so a second run visibly queues
  - name: browser
    image: mcr.microsoft.com/playwright/python:v1.49.0-noble
    command: [sleep, "3600"]
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: BASE_URL, value: "http://127.0.0.1:8000"}, {name: AUTH_URL, value: "http://127.0.0.1:8001"}, {name: ADMIN_PASSWORD, value: ui-check-admin-pass}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/ui --timeout=420s >/dev/null
until kubectl -n "$NS" exec ui -c db -- pg_isready -q -h 127.0.0.1 -U f -d f; do sleep 1; done
git ls-files -z --cached --others --exclude-standard | COPYFILE_DISABLE=1 tar --null -T - -cf - | kubectl -n "$NS" exec -i ui -c app -- tar -xf - -C /src
kubectl -n "$NS" exec ui -c app -- sh -c 'pip install -q --root-user-action=ignore -r requirements.txt && (nohup uvicorn app.main:app --port 8000 >/tmp/app.log 2>&1 &) && until python -c "import sys,urllib.request as u;u.urlopen(sys.argv[1])" http://127.0.0.1:8000/healthz 2>/dev/null; do sleep 1; done && (APP_BASIC_USER=admin APP_BASIC_PASSWORD=ui-check-admin-pass nohup uvicorn app.main:app --port 8001 >/tmp/app-auth.log 2>&1 &)'  # 8001: same database, sign-in required (for named-account flows)
kubectl -n "$NS" exec ui -c browser -- sh -c 'pip install -q playwright==1.49.0 >/dev/null 2>&1; for i in $(seq 1 60); do python -c "import urllib.request as u;u.urlopen(\"$BASE_URL/healthz\");u.urlopen(\"$AUTH_URL/healthz\")" 2>/dev/null && exit 0; sleep 1; done; echo "app did not start" >&2; exit 1'
for f in "${scripts_to_run[@]}"; do
  echo "== $f"
  kubectl -n "$NS" exec ui -c browser -- python -u "/src/$f"
done
echo "ui ok"
