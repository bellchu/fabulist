# Changelog

Notable user-visible and operational changes, newest first.

## Unreleased

- Admin: all API tokens listed on Users & roles (owner, expiry, last used) with revoke.
- Fix: the Archived view stacked under the normal project list instead of replacing it; on phones the Account and Users tables hide secondary columns so Revoke/Disable stay on screen.
- Personal API tokens (`Authorization: Bearer fab_...`) for scripts and integrations, managed on the new Account page; admin list/revoke API. Additive schema change (`api_tokens`), so rolling back is safe.
- SSO sessions end after 30 minutes of inactivity (`SESSION_IDLE_MINUTES`, `0` disables); activity slides the idle window but never extends the 8 h absolute lifetime. Existing sessions stay valid until their next request refreshes them.
- Projects are archived (read-only, restorable, audit trail kept) instead of deleted; permanent deletion is admin-only and requires an archived project. **Behavior change:** `DELETE /api/projects/{id}` now returns 409 for an active project and 403 for non-admins. Additive schema change (`projects.archived`), so rolling back to the previous image is safe.
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
