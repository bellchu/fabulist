# Changelog

Notable user-visible and operational changes, newest first.

## Unreleased

- API requests with a body larger than the configured limit are rejected with 413 before parsing.
- Audit tab: filter the trail by action and user, page through all entries, export CSV.
- Fix: a product class added by an admin could be ignored for up to 30 s after a concurrent
  read (taxonomy cache race).
- Overview: bulk actions collapse into one row in Recent changes; clearer header label; tidier
  layout on narrow screens.
- API responses are `Cache-Control: no-store`; HSTS is sent behind HTTPS.
- `/api/meta` reports the build `version` (git revision).
- Audit trail CSV export per project.
- `scripts/ci.sh`: local quality gate (lint, tests, JS check, dependency audit, image build).
