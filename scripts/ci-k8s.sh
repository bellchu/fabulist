#!/usr/bin/env bash
# Quality gate that runs on the cluster, not on the developer's machine: lint, tests against a throwaway
# pgvector database, frontend syntax check and dependency audit, in a temporary namespace that is always deleted.
# Needs only kubectl access (and git, tar). Sends the working tree (tracked + new, not ignored files).
set -euo pipefail
cd "$(dirname "$0")/.."
# Docker Hub intermittently refuses to pull on the node (digest/size validation); the AWS public mirror serves the same official image.
PYTHON_IMAGE="${PYTHON_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
NS="fabulist-ci-$(date +%s)-$$"
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
kubectl create namespace "$NS" >/dev/null
kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: ci}
spec:
  restartPolicy: Never
  volumes: [{name: src, emptyDir: {}}]
  containers:
  - name: db
    image: pgvector/pgvector:pg16
    env: [{name: POSTGRES_USER, value: fabulist}, {name: POSTGRES_PASSWORD, value: fabulist}, {name: POSTGRES_DB, value: fabulist_test}]
  - name: py
    image: ${PYTHON_IMAGE}
    command: [sleep, "3600"]
    workingDir: /src
    volumeMounts: [{name: src, mountPath: /src}]
    env: [{name: TEST_DATABASE_URL, value: "postgresql://fabulist:fabulist@127.0.0.1:5432/fabulist_test"}]
  - name: node
    image: node:20-alpine
    command: [sleep, "3600"]
    workingDir: /src
    volumeMounts: [{name: src, mountPath: /src}]
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/ci --timeout=300s >/dev/null
# TCP, not the socket: the image's temporary init server answers on the socket only, then restarts.
until kubectl -n "$NS" exec ci -c db -- pg_isready -q -h 127.0.0.1 -U fabulist -d fabulist_test; do sleep 1; done
git ls-files -z --cached --others --exclude-standard | COPYFILE_DISABLE=1 tar --null -T - -cf - | kubectl -n "$NS" exec -i ci -c py -- tar -xf - -C /src
echo "== lint + tests"
kubectl -n "$NS" exec ci -c py -- sh -c \
  'apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null && pip install -q --root-user-action=ignore -r requirements-dev.txt \
   && python -m pyflakes app tests && python -m pytest -q -p no:cacheprovider'
echo "== frontend"
kubectl -n "$NS" exec ci -c node -- sh -c \
  'set -e; mkdir /t; for f in app/static/js/*.js; do cp "$f" "/t/$(basename "$f" .js).mjs"; done; for f in /t/*.mjs; do node --check "$f"; done; echo "js ok"'
echo "== dependency audit"
kubectl -n "$NS" exec ci -c py -- sh -c 'pip install -q --root-user-action=ignore pip-audit && pip-audit -r requirements.txt'
echo "ci ok"
