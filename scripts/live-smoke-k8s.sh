#!/usr/bin/env bash
# Read-only smoke test of the running installation, from the cluster (a browser pod in a temporary namespace): every page loads without script errors,
# failed requests or serious accessibility problems, in light and dark. Writes nothing. Usage: FABULIST_HOST=fabulist.example.com scripts/live-smoke-k8s.sh
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
: "${FABULIST_HOST:?set FABULIST_HOST to the address the app is served on}"
NS="fabulist-smoke-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
# The sign-in details go to the pod through a secret in the temporary namespace (never printed, deleted with it).
kubectl -n "$NS" create secret generic creds \
  --from-literal=BASIC_USER="$(kubectl -n fabulist get secret fabulist-app -o jsonpath='{.data.APP_BASIC_USER}' | base64 -d)" \
  --from-literal=BASIC_PASSWORD="$(kubectl -n fabulist get secret fabulist-app -o jsonpath='{.data.APP_BASIC_PASSWORD}' | base64 -d)" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: smoke}
spec:
  restartPolicy: Never
  containers:
  - name: browser
    image: mcr.microsoft.com/playwright/python:v1.49.0-noble
    command: [sleep, "1800"]
    env: [{name: BASE_URL, value: "https://${FABULIST_HOST}"}]
    envFrom: [{secretRef: {name: creds}}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/smoke --timeout=420s >/dev/null
kubectl -n "$NS" exec smoke -- sh -c 'pip install -q playwright==1.49.0 >/dev/null 2>&1; mkdir -p /s'
kubectl -n "$NS" exec -i smoke -- sh -c 'cat > /s/live_smoke.py' < tests/live_smoke.py
kubectl -n "$NS" exec smoke -- python -u /s/live_smoke.py
