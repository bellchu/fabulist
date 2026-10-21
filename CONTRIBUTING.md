# Contributing

## Workflow

1. Branch from `main` (`feat/…`, `fix/…`, `ux/…`, `chore/…`, `docs/…`).
2. Behavior changes come with tests; a bug gets a regression test that fails first.
3. Run the full gate before opening a PR (there is no hosted CI):

   ```bash
   scripts/ci.sh    # lint, tests on a throwaway pgvector DB, JS check, pip-audit, image build
   ```

4. One feature or fix per PR, squash-merged into `main`. Use conventional commit titles
   (`feat:`, `fix:`, `ux:`, `chore:`, `docs:`), and state what was verified in the PR body.
5. UI changes: check desktop (1440 px) and mobile (390 px) widths and keep keyboard focus,
   contrast and screen-reader labels intact.

## Releasing

`scripts/deploy.sh` builds the image from the current commit (its short SHA is reported by the
authenticated `/api/meta` as `version`) and rolls it out. Deploy from `main` after merging, then
check `/healthz` and `/api/meta`. Rollback: redeploy the previous commit; migrations are additive
(`ADD COLUMN IF NOT EXISTS`), so an older image keeps working against a newer schema.

## Backups

Change the schema or the backup job? Run `scripts/restore-drill.sh` (Docker only): it proves a dump restores into a fresh database and the app works on it.

## Conventions

- Match the surrounding code; reuse existing helpers; no new dependency without discussion.
- Secrets and configuration come from the environment, never the repository.
- Update the README when behavior, configuration or operations change, and `CHANGELOG.md` for
  user-visible changes.
