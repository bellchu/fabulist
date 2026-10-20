#!/usr/bin/env bash
# Full local quality gate (no hosted CI): lint, tests against a throwaway pgvector database,
# frontend syntax check, dependency audit and image build. Needs only Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
name="fabulist-ci-pg-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$name" -e POSTGRES_USER=fabulist -e POSTGRES_PASSWORD=fabulist \
  -e POSTGRES_DB=fabulist_test pgvector/pgvector:pg16 >/dev/null
until docker exec "$name" pg_isready -q -U fabulist -d fabulist_test; do sleep 1; done
echo "== lint + tests"
docker run --rm --link "$name:db" -v "$PWD:/src" -w /src -v fabulist-pip-cache:/root/.cache/pip \
  -e TEST_DATABASE_URL=postgresql://fabulist:fabulist@db:5432/fabulist_test python:3.12-slim sh -c \
  'apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null && pip install -q -r requirements-dev.txt \
   && python -m pyflakes app tests && python -m pytest -q -p no:cacheprovider'
echo "== frontend"
scripts/check-js.sh
echo "== dependency audit"
docker run --rm -v "$PWD:/src" -w /src -v fabulist-pip-cache:/root/.cache/pip python:3.12-slim sh -c \
  'pip install -q pip-audit && pip-audit -r requirements.txt'
echo "== image build"
docker build -q -t fabulist:ci .
echo "ci ok"
