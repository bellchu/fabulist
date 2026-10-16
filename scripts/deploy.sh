#!/usr/bin/env bash
# Build the image for linux/amd64, load it onto the cluster node, and apply manifests.
#
# Required env:
#   FABULIST_HOST   ingress hostname, e.g. fabulist.example.com
#   DEPLOY_SSH      ssh target of the k8s node (user@host) used to import the image
# Optional env:
#   REGISTRY (default localhost:32000)  image name prefix used by the node
set -euo pipefail
cd "$(dirname "$0")/.."
: "${FABULIST_HOST:?set FABULIST_HOST}"
: "${DEPLOY_SSH:?set DEPLOY_SSH}"
REGISTRY="${REGISTRY:-localhost:32000}"
docker info >/dev/null 2>&1 || { echo "Docker daemon is not running; start it and retry." >&2; exit 1; }
TAG="$(date +%Y%m%d%H%M%S)"
IMAGE="${REGISTRY}/fabulist:${TAG}"
NS=fabulist

kubectl apply -f deploy/k8s/namespace.yaml

# Secrets are created once, outside git. Existing values are never overwritten.
if ! kubectl -n "$NS" get secret fabulist-db >/dev/null 2>&1; then
  pw=$(openssl rand -hex 24)
  kubectl -n "$NS" create secret generic fabulist-db \
    --from-literal=POSTGRES_PASSWORD="$pw" \
    --from-literal=DATABASE_URL="postgresql://fabulist:${pw}@postgres.${NS}.svc.cluster.local:5432/fabulist" >/dev/null
fi
if ! kubectl -n "$NS" get secret fabulist-app >/dev/null 2>&1; then
  kubectl -n "$NS" create secret generic fabulist-app \
    --from-literal=APP_BASIC_USER=admin \
    --from-literal=APP_BASIC_PASSWORD="$(openssl rand -hex 16)" >/dev/null
fi
# Encrypts AI provider API keys saved from the admin dashboard. Never rotate casually:
# keys stored with the old value can no longer be decrypted.
if [ -z "$(kubectl -n "$NS" get secret fabulist-app -o jsonpath='{.data.SETTINGS_ENCRYPTION_KEY}')" ]; then
  kubectl -n "$NS" patch secret fabulist-app --type merge \
    -p "{\"stringData\":{\"SETTINGS_ENCRYPTION_KEY\":\"$(openssl rand -hex 32)\"}}" >/dev/null
fi
kubectl apply -f deploy/k8s/postgres.yaml
kubectl -n "$NS" rollout status statefulset/postgres --timeout=240s
kubectl apply -f deploy/k8s/backup.yaml

docker buildx build --platform linux/amd64 -t "$IMAGE" --load .
docker save "$IMAGE" | ssh "$DEPLOY_SSH" "sudo microk8s ctr image import -"

sed -e "s|IMAGE|${IMAGE}|" -e "s|FABULIST_HOST|${FABULIST_HOST}|" deploy/k8s/app.yaml | kubectl apply -f -
kubectl -n "$NS" rollout status deploy/fabulist --timeout=240s
echo "Deployed ${IMAGE} at http://${FABULIST_HOST}"
echo "Basic-auth credentials: kubectl -n ${NS} get secret fabulist-app -o jsonpath='{.data}'"
