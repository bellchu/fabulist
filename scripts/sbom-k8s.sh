#!/usr/bin/env bash
# Software bill of materials (CycloneDX JSON) of the Python packages the app ships, built on the cluster, not on the developer's machine.
# Procurement and security reviews ask for one. Usage: scripts/sbom-k8s.sh [output-file]   (default: sbom.cdx.json in the current directory)
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
OUT="${1:-sbom.cdx.json}"
NS="fabulist-sbom-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: sbom}
spec:
  restartPolicy: Never
  containers:
  - name: py
    image: ${PYTHON_IMAGE}
    command: [sleep, "900"]
    workingDir: /src
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/sbom --timeout=300s >/dev/null
kubectl -n "$NS" exec sbom -- mkdir -p /src
cat requirements.txt | kubectl -n "$NS" exec -i sbom -- sh -c 'cat > /src/requirements.txt'
kubectl -n "$NS" exec sbom -- sh -c 'pip install -q --root-user-action=ignore pip-audit >/dev/null 2>&1 && pip-audit -r requirements.txt -f cyclonedx-json -o /tmp/sbom.json >/dev/null 2>&1 || true; test -s /tmp/sbom.json'
kubectl -n "$NS" exec sbom -- cat /tmp/sbom.json > "$OUT"
python3 - "$OUT" <<'PY'
import json, sys
bom = json.load(open(sys.argv[1]))
print("SBOM: %s, %d components, written to %s" % (bom.get("specVersion", "?"), len(bom.get("components", [])), sys.argv[1]))
PY
