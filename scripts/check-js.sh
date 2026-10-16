#!/usr/bin/env bash
# Syntax-check the frontend ES modules with Node in a container (no local Node needed).
# Retries once: Docker Desktop/OrbStack file sharing can briefly serve a file that was just rewritten.
set -uo pipefail
cd "$(dirname "$0")/.."
check() {
  docker run --rm -v "$PWD/app/static/js:/js:ro" node:20-alpine sh -c \
    'set -e; mkdir /t; for f in /js/*.js; do cp "$f" "/t/$(basename "$f" .js).mjs"; done; for f in /t/*.mjs; do node --check "$f"; done'
}
if check >/dev/null 2>&1 || { sleep 2; check; }; then echo "js ok"; else exit 1; fi
