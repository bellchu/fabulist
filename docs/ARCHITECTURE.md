# Architecture

How Fabulist is put together, why, and what happens when something fails. Operations are in the README; this is for people who change it or review it.

## Shape

One FastAPI process (one pod, `replicas: 1`) serving a vanilla ES-module single-page app from `app/static`, backed by PostgreSQL with pgvector.

| Part | Where | Notes |
|---|---|---|
| HTTP API and guards | `app/main.py` | One middleware does sign-in, roles, throttling, metrics and logging; `project_guards` is an app-wide dependency (archived = read-only, private projects = 404 for non-members). |
| Projects, documents, runs, stories | `app/workspace.py` | Run admission, queue, incremental modes, exports. |
| AI pipeline | `app/pipeline.py`, `app/llm.py` | Sections in parallel, JSON repair, patient retries, per-host cooldown. |
| Search / retrieval | `app/embeddings.py` | pgvector; offline hashing embeddings when no provider is set. |
| Accounts, tokens | `app/users.py`, `app/sso.py` | scrypt passwords, SHA-256-hashed personal tokens, OIDC single sign-on, idle timeout. |
| Audit | `app/history.py`, `app/audit.py` | Per-project change history; administrative trail. Both append-only. |
| Admin | `app/admin.py`, `app/settings.py`, `app/integrations.py` | Providers, settings, taxonomy, trackers. Secrets encrypted with `SETTINGS_ENCRYPTION_KEY`. |
| Observability | `app/metrics.py`, `deploy/k8s/monitoring.yaml` | Prometheus metrics, alerts with runbook rows, Grafana dashboard. |

## Data flow

BRD (upload, text, repository) → parsed and split into sections (`parsing.py`) → **extraction run**: the AI proposes atomic requirements per section → people review (draft / approved / excluded) → **story run**: epics, features and INVEST stories with Gherkin criteria, linked to requirement IDs, with related BRD passages retrieved as context → refine, estimate, baseline → export or push to Jira, Azure DevOps, GitHub, Confluence, webhooks.

## Runs

A run is a row in `runs`. Admission is one atomic step under a Postgres advisory lock: one run per project, at most `MAX_CONCURRENT_RUNS` running, the rest `queued` (bounded by `MAX_QUEUED_RUNS`). A worker claims a run exclusively (`claimed`), so two servers during a rolling deploy cannot execute it twice. Background AI calls across all runs are capped (`MAX_RUN_AI_CALLS`). Results are written at the end in one transaction, so a failed or cancelled run changes nothing.

Large inputs are incremental: `documents` = all / new / reset and `stories_for` = all / uncovered / reset let a project of a thousand BRDs be read in batches without touching reviewed work.

## Decisions

- **One replica, in-process runs.** No broker to operate; the database is the queue. The cost is that a restart ends running runs (queued ones resume). Hence the zero-drop rollout, the deploy that waits for runs, and the restart alert.
- **Liveness is not readiness.** `/livez` never touches the database; `/healthz` does. A database outage must take the pod out of rotation, not restart it. A startup probe covers long schema upgrades.
- **Schema upgrades run at startup** (`db.init`, idempotent `IF NOT EXISTS` statements, backfills guarded so they run once). Upgrades are additive so the previous version keeps working during a rollout and a rollback needs no down-migration.
- **Strict CSP, no build step.** `script-src 'self'`, no inline script, no CDN: the UI works on locked-down networks and the API reference is served by the app itself.
- **Authorization on the server, deny by default.** Roles (admin, analyst, viewer) are checked per route in one place; the UI hides what you cannot do but is never the control. Tests assert every admin and token write route records an audit entry and every route group honours private projects.
- **Audit entries never hold secrets.** Settings changes store old and new values (none are secret); passwords and keys are recorded only as "changed".
- **Offline AI provider for tests and load tests**, so the whole battery runs without cost or network.

## Trust boundaries

| Boundary | Controls |
|---|---|
| Browser → app | Cloudflare / ingress TLS, HSTS, CSP, `X-Requested-With` on writes (CSRF), sign-in with throttling, idle timeout. |
| App → AI provider | Keys encrypted at rest and never returned; personal-data masking before text leaves; timeouts, bounded retries, per-user fair use of interactive calls. |
| App → trackers, webhooks | Only administrators set the URLs (https required), tokens encrypted, webhook payloads optionally signed (HMAC). Internal addresses are allowed on purpose: an on-premises Jira or an in-cluster model server is a normal setup, so protect the admin role accordingly. |
| App → BRD repositories | Anyone who can add a repository supplies the URL, so it is refused if it resolves to a private, loopback, link-local or cluster-internal address (SSRF guard, re-checked at every sync). |
| Uploads | Size limits, decompression-bomb limits, page limits, limited parse concurrency. |
| App → database | The app logs in as a role that can read and write data but not change the schema or update or delete the history and administrative trail (the cascade that removes a deleted project's history runs with the owner's rights); a separate owner login (`MIGRATION_DATABASE_URL`) is used at startup only, to upgrade the schema and grant those rights. Append-only triggers sit behind that. |
| Pod → cluster | Non-root, read-only root filesystem, no capabilities, no service-account token, NetworkPolicy (only the ingress and Prometheus reach the app; only this namespace reaches Postgres). |
| Supply chain | Pinned dependencies, weekly audit, image scan gate in `deploy.sh`, Dependabot. |

## Failure behaviour

| Failure | What happens |
|---|---|
| Database down | API answers 503 with `Retry-After`; readiness fails; the app reconnects by itself; running runs fail on their next write. |
| AI provider slow or throttling | Requests retry (up to `LLM_MAX_ATTEMPTS`, honouring `Retry-After`); a rate limit pauses every call to that host; `FabulistAIProviderThrottling` fires. Quota exhaustion is not retried. |
| Pod killed (OOM, node loss) | Executing runs are marked failed with a clear message; queued runs start again on the new pod; `FabulistRestarting` fires. |
| Deploy | New pod ready before the old one stops; the old one drains 8 s; the deploy waits for executing runs first. |
| Lost data | Nightly `pg_dump`; `scripts/restore-drill-k8s.sh` proves a restore end to end. |

## Known limits

At most `MAX_CONCURRENT_RUNS` (default 4) runs execute at once. The UI loads whole requirement lists (compressed, drawn 300 rows at a time) rather than paging on the server. Quality scoring costs about 3 s per heavy call at 50,000 requirements. Only synthetic files and providers are load-tested. See "Capacity and sizing" in the README.
