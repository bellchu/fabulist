#!/usr/bin/env bash
# Local dev server against the port-forwarded cluster DB (run scripts/dev-db.sh first).
# Uses a separate database (default fabulist_test) so live data is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
env_line="$(scripts/dev-env.sh)" || { echo "Cannot read database credentials: is the cluster reachable?" >&2; exit 1; }
eval "$env_line"
export DATABASE_URL="${DATABASE_URL%/fabulist}/${DEV_DB:-fabulist_test}"
export SETTINGS_ENCRYPTION_KEY="${SETTINGS_ENCRYPTION_KEY:-dev-only-key}"
exec "${PYTHON_BIN:-.venv/bin}/uvicorn" app.main:app --port "${PORT:-8765}" --reload
