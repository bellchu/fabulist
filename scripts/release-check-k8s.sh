#!/usr/bin/env bash
# Everything that decides whether a build is ready, on the cluster, in one go: the quality gate, the browser suite, the load scenarios and the
# vulnerability scan of the image that is running, and the backup/restore drill. Prints one summary; exit status 1 if any stage failed (the later stages still run).
# Usage: scripts/release-check-k8s.sh [stage ...]   stages: gate ui load scan restore live (default: all but live, which needs FABULIST_HOST)   env: LOAD_SCENARIOS (default below)
set -uo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
stages=("$@"); [ "${#stages[@]}" -gt 0 ] || stages=(gate ui load scan restore)
LOAD_SCENARIOS="${LOAD_SCENARIOS:-burst slowai flakyai volume bigfiles manyprojects}"
declare -a names results seconds
run() {  # name command...
  local name="$1"; shift
  local start=$SECONDS
  echo "== $name"
  if "$@"; then results+=("pass"); else results+=("FAIL"); fi
  names+=("$name"); seconds+=("$((SECONDS - start))")
}
for stage in "${stages[@]}"; do
  case "$stage" in
    gate) run "quality gate (lint, tests, JS, audit)" scripts/ci-k8s.sh ;;
    ui)   run "browser suite" scripts/ui-check-k8s.sh ;;
    load) run "load scenarios ($LOAD_SCENARIOS)" scripts/load-test-k8s.sh $LOAD_SCENARIOS ;;
    scan) run "image vulnerability scan" scripts/scan-image-k8s.sh ;;
    restore) run "backup and restore drill" scripts/restore-drill-k8s.sh ;;
    live) run "live smoke test (read-only)" scripts/live-smoke-k8s.sh ;;
    *) echo "unknown stage: $stage" >&2; exit 2 ;;
  esac
done
echo
echo "== release check: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
status=0
for i in "${!names[@]}"; do
  printf "  %-6s %5ss  %s\n" "${results[$i]}" "${seconds[$i]}" "${names[$i]}"
  [ "${results[$i]}" = pass ] || status=1
done
[ "$status" = 0 ] && echo "ready" || echo "NOT ready: see the stage output above"
exit $status
