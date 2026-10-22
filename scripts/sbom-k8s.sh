#!/usr/bin/env bash
# Software bill of materials (CycloneDX JSON), built on the cluster, not on the developer's machine. Procurement and security reviews ask for one.
# Usage: scripts/sbom-k8s.sh [output-file]            the app's pinned Python packages (default output: sbom.cdx.json in the current directory)
#        SBOM_SOURCE=image scripts/sbom-k8s.sh [file]  the image that is running: operating-system packages as well as Python (Trivy)
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
OUT="${1:-sbom.cdx.json}"
TRIVY_IMAGE="${TRIVY_IMAGE:-ghcr.io/aquasecurity/trivy:0.58.1}"
NS="fabulist-sbom-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
if [ "${SBOM_SOURCE:-packages}" = image ]; then
  IMAGE="$(kubectl -n fabulist get deploy fabulist -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "reading the packages of $IMAGE"
  kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: sbom}
spec:
  restartPolicy: Never
  hostNetwork: true
  containers:
  - name: trivy
    image: ${TRIVY_IMAGE}
    args: ["image", "--insecure", "--format", "cyclonedx", "--quiet", "--timeout", "10m", "${IMAGE}"]
YAML
  for _ in $(seq 1 120); do
    phase="$(kubectl -n "$NS" get pod sbom -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [ "$phase" = Succeeded ] || [ "$phase" = Failed ] && break
    sleep 5
  done
  [ "$phase" = Succeeded ] || { echo "Error: the image could not be read" >&2; exit 1; }
  kubectl -n "$NS" logs sbom 2>/dev/null | python3 -c 'import sys; t = sys.stdin.read(); sys.stdout.write(t[t.index("{"):])' > "$OUT"
  python3 - "$OUT" <<'PY'
import json, sys
bom = json.load(open(sys.argv[1]))
kinds = {}
for c in bom.get("components", []):
    kinds[c.get("type", "?")] = kinds.get(c.get("type", "?"), 0) + 1
print("SBOM: %s, %d components %s, written to %s" % (bom.get("specVersion", "?"), len(bom.get("components", [])), kinds, sys.argv[1]))
PY
  exit 0
fi
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
