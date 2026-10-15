#!/usr/bin/env bash
# Prints an export line for DATABASE_URL pointing at the port-forwarded DB.
set -euo pipefail
url=$(kubectl -n fabulist get secret fabulist-db -o jsonpath='{.data.DATABASE_URL}' | base64 -d)
echo "export DATABASE_URL='${url/postgres.fabulist.svc.cluster.local:5432/127.0.0.1:5433}'"
