#!/usr/bin/env bash
# Load test on the cluster, not on the developer's machine. Runs the app from the working tree with the production pod's limits
# and probes (1 GiB memory, the same liveness probe) against a throwaway pgvector database and the offline AI provider (no AI spend),
# then runs tests/load/brd_load.py scenarios from a separate load-generator container. Always deletes its namespace.
# Usage: scripts/load-test-k8s.sh [burst] [slowai] [volume] [bigproject] [bigfiles] [manyprojects] [flakyai]     (default: the first three)   env: REQUESTERS BRDS_EACH VOLUME_BRDS BRD_KB VOLUME_KB LLM_LATENCY SLOW_BRDS_EACH BIG_BRDS MAX_BATCHES
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
scenarios=("$@"); [ "${#scenarios[@]}" -gt 0 ] || scenarios=(burst slowai volume)
NS="fabulist-load-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: load}
spec:
  restartPolicy: Always
  volumes: [{name: src, emptyDir: {}}]
  containers:
  - name: db
    image: pgvector/pgvector:pg16
    env: [{name: POSTGRES_USER, value: f}, {name: POSTGRES_PASSWORD, value: f}, {name: POSTGRES_DB, value: f}]
  - name: app
    image: ${PYTHON_IMAGE}
    workingDir: /src
    command: [sh, -c, 'while [ ! -f /src/.ready ]; do sleep 1; done; pip install -q --root-user-action=ignore -r requirements.txt && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log']
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: DATABASE_URL, value: "postgresql://f:f@127.0.0.1:5432/f"}, {name: SETTINGS_ENCRYPTION_KEY, value: load-test}, {name: PYTHONDONTWRITEBYTECODE, value: "1"}, {name: METRICS_TOKEN, value: load-metrics}, {name: RATE_LIMIT_PER_MINUTE, value: "0"}, {name: MALLOC_ARENA_MAX, value: "${MALLOC_ARENA_MAX:-2}"}]
    resources: {requests: {cpu: 100m, memory: 256Mi}, limits: {memory: 1Gi}}   # deliberately smaller than deploy/k8s/app.yaml (1.5 GiB): what passes here has headroom in production
    startupProbe: {httpGet: {path: /healthz, port: 8000}, periodSeconds: 5, failureThreshold: 90}
    readinessProbe: {httpGet: {path: /healthz, port: 8000}, periodSeconds: 10}
    livenessProbe: {httpGet: {path: /healthz, port: 8000}, periodSeconds: 20}   # default 1 s timeout, 3 failures: the kubelet restarts a blocked app
  - name: loadgen
    image: ${PYTHON_IMAGE}
    command: [sleep, "7200"]
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: BASE_URL, value: "http://127.0.0.1:8000"}, {name: METRICS_TOKEN, value: load-metrics}, {name: REQUESTERS, value: "${REQUESTERS:-20}"}, {name: BRDS_EACH, value: "${BRDS_EACH:-20}"}, {name: VOLUME_BRDS, value: "${VOLUME_BRDS:-1100}"}, {name: BRD_KB, value: "${BRD_KB:-30}"}, {name: VOLUME_KB, value: "${VOLUME_KB:-10}"}, {name: LLM_LATENCY, value: "${LLM_LATENCY:-0.25}"}, {name: SLOW_BRDS_EACH, value: "${SLOW_BRDS_EACH:-2}"}, {name: BIG_BRDS, value: "${BIG_BRDS:-300}"}, {name: MAX_BATCHES, value: "${MAX_BATCHES:-60}"}, {name: FILE_MB, value: "${FILE_MB:-6}"}, {name: MANY_PROJECTS, value: "${MANY_PROJECTS:-300}"}, {name: FLAKY, value: "${FLAKY:-0.25}"}, {name: FLAKY_RUNS, value: "${FLAKY_RUNS:-6}"}, {name: FLAKY_BRDS_EACH, value: "${FLAKY_BRDS_EACH:-2}"}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/load --timeout=300s >/dev/null || true
until kubectl -n "$NS" exec load -c db -- pg_isready -q -h 127.0.0.1 -U f -d f 2>/dev/null; do sleep 1; done
git ls-files -z --cached --others --exclude-standard | COPYFILE_DISABLE=1 tar --null -T - -cf - 2>/dev/null | kubectl -n "$NS" exec -i load -c loadgen -- tar -xf - -C /src 2>/dev/null
kubectl -n "$NS" exec load -c loadgen -- sh -c 'touch /src/.ready; pip install -q --root-user-action=ignore httpx==0.28.1 python-docx 2>&1 | tail -1'
for _ in $(seq 1 120); do kubectl -n "$NS" exec load -c loadgen -- python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8000/healthz')" 2>/dev/null && break; sleep 3; done
status=0
for s in "${scenarios[@]}"; do
  kubectl -n "$NS" exec load -c loadgen -- sh -c "cd /src && python -u tests/load/brd_load.py $s" || status=1
  sleep 15  # the kubelet records a restart a moment after the process dies
  peak="$(kubectl -n "$NS" exec load -c app -- sh -c 'cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0' 2>/dev/null || echo 0)"
  restarts="$(kubectl -n "$NS" get pod load -o jsonpath='{.status.containerStatuses[?(@.name=="app")].restartCount}')"
  echo "app memory peak so far: $((peak / 1048576)) MiB of 1024 MiB | app container restarts: $restarts"
  if [ "${restarts:-0}" != 0 ]; then
    echo "!! the app container was restarted: last termination reason: $(kubectl -n "$NS" get pod load -o jsonpath='{.status.containerStatuses[?(@.name=="app")].lastState.terminated.reason}')" >&2
    kubectl -n "$NS" logs load -c app --previous --tail=8 2>&1 | cut -c1-200 >&2 || true
    status=1
  fi
done
exit $status
