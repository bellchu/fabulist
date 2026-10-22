#!/usr/bin/env bash
# Scan a container image for known HIGH/CRITICAL vulnerabilities (OS packages and Python) on the cluster, not on the developer's machine.
# Default: the image the app is running now. Usage: scripts/scan-image-k8s.sh [image]   (exit 1 when something fixable is found)
set -euo pipefail
IMAGE="${1:-$(kubectl -n fabulist get deploy fabulist -o jsonpath='{.spec.template.spec.containers[0].image}')}"
TRIVY_IMAGE="${TRIVY_IMAGE:-ghcr.io/aquasecurity/trivy:0.58.1}"
NS="fabulist-scan-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
echo "scanning $IMAGE"
# hostNetwork: reaches the node registry at localhost:32000. Unfixed findings are ignored: there is nothing to act on.
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: scan}
spec:
  restartPolicy: Never
  hostNetwork: true
  containers:
  - name: trivy
    image: ${TRIVY_IMAGE}
    args: ["image", "--insecure", "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", "--format", "table", "--timeout", "10m", "${IMAGE}"]
YAML
phase=""
for _ in $(seq 1 120); do
  phase="$(kubectl -n "$NS" get pod scan -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  [ "$phase" = Succeeded ] || [ "$phase" = Failed ] && break
  sleep 8
done
kubectl -n "$NS" logs scan 2>&1 | grep -vE '^\s*$|INFO|WARN' || true
[ "$phase" = Succeeded ] && echo "image scan ok" || { echo "image scan found fixable HIGH/CRITICAL vulnerabilities (or did not finish)" >&2; exit 1; }
