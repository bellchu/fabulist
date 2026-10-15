#!/usr/bin/env bash
# Port-forward the in-cluster Postgres to localhost:5433 for local dev/tests.
# Usage:  scripts/dev-db.sh &   then:  eval "$(scripts/dev-env.sh)"
set -euo pipefail
exec kubectl -n fabulist port-forward svc/postgres 5433:5432
