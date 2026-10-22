#!/usr/bin/env bash
# Wait (up to MINUTES, default DEPLOY_WAIT_FOR_RUNS or 15; 0 = do not wait) until no AI run is executing on the app in namespace NS, so a
# deploy does not fail runs that cannot be resumed. Exits 0 either way; warns on stderr if runs are still executing when the time is up.
# Usage: scripts/wait-for-runs.sh [namespace] [minutes]
set -uo pipefail
NS="${1:-fabulist}"
minutes="${2:-${DEPLOY_WAIT_FOR_RUNS:-15}}"
running_runs() {
  kubectl -n "$NS" exec deploy/fabulist -- python -c "from app import db; print(db.one(\"SELECT count(*) AS n FROM runs WHERE status = 'running'\")['n'])" 2>/dev/null || echo 0
}
[ "$minutes" != 0 ] || exit 0
kubectl -n "$NS" get deploy/fabulist >/dev/null 2>&1 || exit 0  # first deploy: nothing is running yet
waited=0
while [ "$(running_runs)" != 0 ] && [ "$waited" -lt $((minutes * 60)) ]; do
  [ "$waited" = 0 ] && echo "AI runs are executing; waiting up to ${minutes} minutes for them to finish before replacing the server (DEPLOY_WAIT_FOR_RUNS=0 skips this)."
  sleep 20
  waited=$((waited + 20))
done
left="$(running_runs)"
if [ "$left" != 0 ]; then
  echo "WARNING: $left AI run(s) still executing after ${minutes} minutes; they will fail with 'Interrupted by server restart'." >&2
else
  [ "$waited" = 0 ] || echo "No AI runs are executing any more."
fi
exit 0
