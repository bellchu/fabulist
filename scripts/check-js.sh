#!/usr/bin/env bash
# Syntax-check the frontend ES modules with Node in a container (no local Node needed).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD/app/static/js:/js:ro" node:20-alpine sh -c \
  'set -e; mkdir /t; for f in /js/*.js; do cp "$f" "/t/$(basename "$f" .js).mjs"; done; for f in /t/*.mjs; do node --check "$f"; done; echo "js ok"'
