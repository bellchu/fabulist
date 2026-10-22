#!/usr/bin/env bash
# Take the committed work on the current branch all the way: check it on the cluster, open the pull request, merge it, deploy it. It stops at the first
# failure, and nothing is merged unless the quality gate and the browser suite passed (there are no GitHub Actions: this is the check).
# Usage: FABULIST_HOST=fabulist.example.com scripts/ship-k8s.sh "pull request title" ["body"]
#        SKIP_DEPLOY=1 for a change that does not need a deploy (docs, tests, scripts); STAGES="gate" to run fewer checks (default: gate ui).
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-cluster.sh; require_cluster
title="${1:?usage: scripts/ship-k8s.sh \"pull request title\" [\"body\"]}"
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" != main ] || { echo "Error: you are on main; make a branch first." >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Error: uncommitted changes: commit them first (the check and the merge must be about the same thing)." >&2; exit 1; }
[ "${SKIP_DEPLOY:-}" = 1 ] || : "${FABULIST_HOST:?set FABULIST_HOST (or SKIP_DEPLOY=1 for a change that needs no deploy)}"

scripts/release-check-k8s.sh ${STAGES:-gate ui}   # exits non-zero if any stage failed: set -e stops here, before anything is published
git push -q -u origin "$branch"
url="$(gh pr create --title "$title" --body "${2:-Checked on the cluster with scripts/release-check-k8s.sh (${STAGES:-gate ui}): passed.}")"
echo "opened $url"
sleep 3  # the base branch can be 'modified' right after a push; a moment avoids a spurious refusal to merge
gh pr merge "$url" --squash
git checkout -q main
git pull -q --ff-only
echo "merged: $(git log --oneline | head -1)"
[ "${SKIP_DEPLOY:-}" = 1 ] || scripts/deploy.sh
