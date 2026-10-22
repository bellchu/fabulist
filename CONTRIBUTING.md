# Contributing

## Workflow

1. Branch from `main` (`feat/…`, `fix/…`, `ux/…`, `chore/…`, `docs/…`).
2. Behavior changes come with tests; a bug gets a regression test that fails first.
3. Run the full gate before opening a PR (there is no hosted CI):

   ```bash
   scripts/ci-k8s.sh   # runs on the cluster (kubectl only): lint, tests on a throwaway pgvector DB, JS check, pip-audit
   scripts/ci.sh       # same plus the image build, on a local Docker daemon
   scripts/ui-check-k8s.sh   # browser checks (tests/ui/*.py, Playwright) against the app, on the cluster
   ```

4. One feature or fix per PR, squash-merged into `main`. Use conventional commit titles
   (`feat:`, `fix:`, `ux:`, `chore:`, `docs:`), and state what was verified in the PR body.
5. UI changes: check desktop (1440 px) and mobile (390 px) widths and keep keyboard focus,
   contrast and screen-reader labels intact.

## Releasing

`scripts/deploy.sh` builds the image on the cluster from the files git tracks in your checkout, so it refuses to run unless you are on a clean, up-to-date `main` (merge first; `ALLOW_DIRTY_DEPLOY=1` is an emergency override that ships whatever is on disk). Never chain a deploy after a command that can fail with `;` (use `&&`). The image carries the commit's short SHA (reported by the authenticated `/api/meta` as `version`) and
is rolled out. After deploying, check `/healthz`, `/api/meta` and that the UI loads. Rollback: redeploy the previous commit; migrations are additive
(`ADD COLUMN IF NOT EXISTS`), so an older image keeps working against a newer schema.

## Backups

Change the schema or the backup job? Run `scripts/restore-drill.sh` (Docker only): it proves a dump restores into a fresh database and the app works on it.

## Conventions

- Match the surrounding code; reuse existing helpers; no new dependency without discussion.
- Secrets and configuration come from the environment, never the repository.
- Update the README when behavior, configuration or operations change, and `CHANGELOG.md` for
  user-visible changes.

## Repository settings

`main` blocks force-pushes and deletion and requires a linear history (squash-merge PRs; no merge commits). Merged branches are deleted
automatically, and Dependabot vulnerability alerts, secret scanning with push protection, and private vulnerability reporting are on.
Reviews and status checks are not required by GitHub because the quality gate runs locally (`scripts/ci-k8s.sh`, `scripts/ui-check-k8s.sh`), not in Actions.
