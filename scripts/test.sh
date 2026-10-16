#!/usr/bin/env bash
# Run lint and tests in a Python 3.12 container (same runtime as production) against the
# port-forwarded test database (scripts/dev-db.sh must be running).
set -euo pipefail
cd "$(dirname "$0")/.."
eval "$(scripts/dev-env.sh)"
TEST_DATABASE_URL="${DATABASE_URL%/fabulist}/fabulist_test"
TEST_DATABASE_URL="${TEST_DATABASE_URL/127.0.0.1/host.docker.internal}"
exec docker run --rm -v "$PWD:/src" -w /src -v fabulist-pip-cache:/root/.cache/pip \
  -e TEST_DATABASE_URL="$TEST_DATABASE_URL" python:3.12-slim sh -c \
  'apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null && pip install -q -r requirements-dev.txt \
   && python -m pyflakes app tests && python -m pytest -q -p no:cacheprovider "$@"' -- "$@"
