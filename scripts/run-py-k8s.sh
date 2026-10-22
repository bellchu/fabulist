#!/usr/bin/env bash
# Run a Python script against the working tree on the cluster (profiling, experiments), not on the developer's machine.
# Usage: scripts/run-py-k8s.sh path/to/script.py [args...]    The app's requirements are installed; the repo is at /src (cwd).
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$#" -ge 1 ] || { echo "usage: $0 script.py [args...]" >&2; exit 2; }
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
script="$1"; shift
NS="fabulist-py-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: py}
spec:
  restartPolicy: Never
  containers:
  - name: py
    image: ${PYTHON_IMAGE}
    command: [sleep, "3600"]
    workingDir: /src
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/py --timeout=300s >/dev/null
kubectl -n "$NS" exec py -- mkdir -p /src
git ls-files -z --cached --others --exclude-standard | COPYFILE_DISABLE=1 tar --null -T - -cf - 2>/dev/null | kubectl -n "$NS" exec -i py -- tar -xf - -C /src 2>/dev/null
kubectl -n "$NS" exec py -- sh -c 'pip install -q --root-user-action=ignore -r requirements.txt 2>&1 | tail -1'
kubectl -n "$NS" exec py -- env PYTHONPATH=/src python -u "/src/$script" "$@"
