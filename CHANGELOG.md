# Changelog

Notable user-visible and operational changes, newest first.

## Unreleased

- Accessibility: tables that scroll sideways on narrow screens are keyboard-focusable (arrow keys scroll them) and labelled; the tab stop is removed when the table fits. Axe is clean on all 32 live pages (16 screens x desktop and phone).
- Grafana dashboard: a *Reliability and security* row (database up/down, failed sign-ins and token attempts, edit conflicts and database 503s). A test now fails if any alert rule or dashboard panel references a metric the app does not expose.
- Monitoring: `/metrics` keeps working during a database outage and reports `fabulist_database_up`; new counters for failed sign-ins/tokens, edit conflicts, database 503s and unpushed stories; new alerts `FabulistDatabaseUnavailable` (instead of a misleading "Fabulist is down") and `FabulistAuthFailureSpike`, with runbook entries.
- Confluence publishing: a page deleted in Confluence is created again on the next publish (it used to fail with 404 forever); one edit conflict (409) is retried with the fresh version; a title collision says to rename the project or the page.
- `scripts/deploy.sh` refuses to deploy from a branch other than `main`, with uncommitted or untracked changes, or when HEAD is not the latest `origin/main` (the image is built from the working tree).
- Webhooks: every delivery carries `X-Fabulist-Delivery` (stable across retries) and `X-Fabulist-Timestamp`, and with a secret `X-Fabulist-Signature-V2` signs `timestamp.body`, so receivers can reject replays and deduplicate; the body-only `X-Fabulist-Signature` is unchanged. Receiver recipe in the README.
- Git sync: the clone is pinned to the address that passed the private-network check and no longer follows HTTP redirects (a public host could previously redirect it to an internal service, or change its DNS answer between check and clone); a clone that grows past `MAX_REPO_MB` (500) is stopped; the clone timeout is `REPO_CLONE_TIMEOUT` (60 s, was 120 s, which exceeded the proxy timeout); a blocked redirect says to use the canonical URL.
- Uploads: DOCX/XLSX that would expand beyond `MAX_UNZIPPED_MB` (decompression bombs) or contain thousands of parts are refused before parsing; PDFs over `MAX_PDF_PAGES` are refused and extraction stops once enough text is read; password-protected, scanned and damaged files get a clear message; truncation (long documents, 5,000+ spreadsheet rows, 50+ sheets, long pasted text) is announced in the document text instead of silent.
- Database outages: API calls and `/healthz` return a retryable 503 (JSON message, `Retry-After`) instead of a plain 500; requests wait at most `DB_POOL_TIMEOUT` (10 s, was 30 s) for a connection; the pool validates connections when handing them out, so a database restart no longer fails the next requests (3 of 15 failed before, 0 after), and TCP keepalives detect a partitioned connection.
- Concurrent edits: requirements and stories carry a `rev`; saving from a stale editor returns 409 ("changed by someone else") instead of overwriting, and a bulk status change also makes open editors stale. Clients that send no `rev` behave as before. Additive schema change (`rev` columns with default 1), so rolling back is safe.
- Fix: the product-taxonomy cache ignored invalidation for the first 30 s after a machine boot (a new or edited product class could be missed on a freshly started node).
- Backups: the documented restore command now uses `--clean --if-exists --exit-on-error` (the old one exited 1 with 67 errors on a fresh database); `scripts/restore-drill.sh` verifies dump and restore end to end.
- Fix: going back from a project that was still loading left that project's name in the Projects breadcrumb.
- Project list: from 8 projects a filter bar (name/description, platform) and a sort (newest, name, most left to review) appear; the choice is kept while navigating.
- AI run failures name the section or story group that failed and state that nothing was saved (e.g. "Section 12 of 40 (Returns): provider returned HTTP 503. Nothing was saved; existing results were kept.").
- Tracker push: stops at a time budget (`PUSH_BUDGET_SECONDS`) and reports how many stories remain instead of timing out at the proxy; wrong credentials, permissions or URL stop the push with one actionable message instead of N identical failures; results and errors stay visible in the card and are announced to screen readers; the button says *Publish page* for Confluence.
- Baselines on phones: the table fits, with a one-line contents summary under each name and the Export/Compare actions in reach.
- Accessibility: dialog and side-panel footer buttons now meet WCAG AA contrast (4.43:1 → above 4.5:1). Links inside sentences are underlined (WCAG 1.4.1). Axe-core (WCAG 2.0/2.1 A and AA) is clean on 24 screens, keyboard-only use of the token flow verified.
- Phone layouts: Requirements, Stories (list), Sources, Product taxonomy and Activity tables keep only the essential columns so the content and row actions fit without sideways scrolling; header buttons and attribute rules wrap and stack.
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
