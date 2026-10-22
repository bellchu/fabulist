#!/usr/bin/env bash
# Build the image on the cluster (Kaniko, linux/amd64), push it to the node's registry, and apply manifests.
#
# Required env:
#   FABULIST_HOST   ingress hostname, e.g. fabulist.example.com
#   DEPLOY_SSH      ssh target of the k8s node (user@host); kept for compatibility, no longer used for the image
# Optional env:
#   REGISTRY (default localhost:32000)  image name prefix used by the node
set -euo pipefail
cd "$(dirname "$0")/.."
: "${FABULIST_HOST:?set FABULIST_HOST}"
REGISTRY="${REGISTRY:-localhost:32000}"
# The image is built from the WORKING TREE, so anything uncommitted ships to production unreviewed and untested.
# Deploy only a clean checkout of the latest main (the commit that passed the gate). ALLOW_DIRTY_DEPLOY=1 overrides.
if [ -z "${ALLOW_DIRTY_DEPLOY:-}" ]; then
  branch="$(git rev-parse --abbrev-ref HEAD)"
  [ "$branch" = main ] || { echo "Refusing to deploy from branch $branch: merge to main and deploy from main (ALLOW_DIRTY_DEPLOY=1 overrides)." >&2; exit 1; }
  [ -z "$(git status --porcelain)" ] || { echo "Refusing to deploy: uncommitted changes would be built into the image (commit, or stash them)." >&2; git status --short >&2; exit 1; }
  if git remote get-url origin >/dev/null 2>&1 && git fetch -q origin main 2>/dev/null; then
    [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "Refusing to deploy: HEAD is not the latest origin/main (git pull --ff-only)." >&2; exit 1; }
  fi
fi
kubectl version --request-timeout=10s >/dev/null 2>&1 || { echo "Kubernetes API is unreachable (check the kube context or SSH tunnel)." >&2; exit 1; }
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
if [ -z "$(kubectl -n "$NS" get secret fabulist-app -o jsonpath='{.data.METRICS_TOKEN}')" ]; then
  kubectl -n "$NS" patch secret fabulist-app --type merge \
    -p "{\"stringData\":{\"METRICS_TOKEN\":\"$(openssl rand -hex 24)\"}}" >/dev/null
fi
kubectl apply -f deploy/k8s/postgres.yaml
kubectl apply -f deploy/k8s/networkpolicy.yaml
kubectl -n "$NS" rollout status statefulset/postgres --timeout=240s
kubectl apply -f deploy/k8s/backup.yaml

# Build on the cluster (Kaniko in a throwaway namespace, pushing to the node's registry): nothing runs on the developer's machine.
BUILD_NS="fabulist-build-${TAG}"
trap 'kubectl delete namespace "$BUILD_NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$BUILD_NS" >/dev/null
kubectl -n "$BUILD_NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: build}
spec:
  restartPolicy: Never
  hostNetwork: true  # reaches the registry on the node's localhost:32000
  volumes: [{name: ws, emptyDir: {}}]
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:debug
    command: [sleep, "1800"]
    volumeMounts: [{name: ws, mountPath: /workspace}]
YAML
kubectl -n "$BUILD_NS" wait --for=condition=Ready pod/build --timeout=300s >/dev/null
git ls-files -z | COPYFILE_DISABLE=1 tar --null -T - -cf - | kubectl -n "$BUILD_NS" exec -i build -- tar -xf - -C /workspace
kubectl -n "$BUILD_NS" exec build -- /kaniko/executor --context=dir:///workspace --dockerfile=/workspace/Dockerfile \
  --destination="$IMAGE" --insecure --build-arg APP_VERSION="$(git rev-parse --short HEAD)"

# Do not roll out an image with fixable HIGH/CRITICAL vulnerabilities (rebuilding picks up patched base layers).
# SKIP_IMAGE_SCAN=1 is the emergency override, for example when the scanner's database cannot be downloaded.
if [ -z "${SKIP_IMAGE_SCAN:-}" ]; then
  scripts/scan-image-k8s.sh "$IMAGE" || { echo "Refusing to deploy $IMAGE: image scan failed (SKIP_IMAGE_SCAN=1 overrides)." >&2; exit 1; }
fi

# Replacing the server fails the AI runs that are executing (a run cannot be resumed without redoing its AI work and cost), so give them
# time to finish: up to DEPLOY_WAIT_FOR_RUNS minutes (default 15; 0 skips).
scripts/wait-for-runs.sh "$NS"

# The running app logs in as a role that can read and write data but cannot change the schema or rewrite the audit tables. The original owner login
# becomes MIGRATION_DATABASE_URL: the app uses it for a moment at startup to upgrade the schema and grant the app role its rights. Done once, and only
# when the secret does not have it yet. To go back: put the MIGRATION_DATABASE_URL value into DATABASE_URL and delete the key (README, Operations).
if [ -z "$(kubectl -n "$NS" get secret fabulist-db -o jsonpath='{.data.MIGRATION_DATABASE_URL}')" ]; then
  owner_url=$(kubectl -n "$NS" get secret fabulist-db -o jsonpath='{.data.DATABASE_URL}' | base64 -d)
  app_pw=$(openssl rand -hex 24)
  kubectl -n "$NS" exec -i postgres-0 -- psql -q -U fabulist -d fabulist -v ON_ERROR_STOP=1 -v pw="$app_pw" -f - >/dev/null <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fabulist_app') THEN
    CREATE ROLE fabulist_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
ALTER ROLE fabulist_app PASSWORD :'pw';
SQL
  kubectl -n "$NS" patch secret fabulist-db --type merge -p "$(python3 -c '
import json, sys
print(json.dumps({"stringData": {"MIGRATION_DATABASE_URL": sys.argv[1], "DATABASE_URL": "postgresql://fabulist_app:%s@postgres.%s.svc.cluster.local:5432/fabulist" % (sys.argv[2], sys.argv[3])}}))' "$owner_url" "$app_pw" "$NS")" >/dev/null
  echo "The app now runs as the role fabulist_app (no schema changes, audit tables read and append only)."
fi

sed -e "s|IMAGE|${IMAGE}|" -e "s|FABULIST_HOST|${FABULIST_HOST}|" deploy/k8s/app.yaml | kubectl apply -f -
sed -e "s|IMAGE|${IMAGE}|" deploy/k8s/audit.yaml | kubectl apply -f -
kubectl -n "$NS" rollout status deploy/fabulist --timeout=240s
# Prometheus Operator / Grafana sidecar objects are optional: skip when the CRDs are absent.
if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  kubectl apply -f deploy/k8s/monitoring.yaml
fi
echo "Deployed ${IMAGE} at http://${FABULIST_HOST}"
echo "Basic-auth credentials: kubectl -n ${NS} get secret fabulist-app -o jsonpath='{.data}'"
