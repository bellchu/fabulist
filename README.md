# Fabulist

Turn business requirements documents (BRDs) into a reviewed, traceable agile backlog.
Built for enterprise application delivery (Salesforce, ERP, CRM) in medical product
distribution, where requirements vary by product class (cold chain, controlled,
serialized, kits, devices...).

## Workflow

The app follows common business-analysis practice: BRD → atomic requirements with stable
IDs → human review → epics, features and stories → requirements traceability matrix (RTM).

1. **Sources**: upload PDF, Word, Excel, CSV, Markdown or text BRDs, paste text, or connect a Git repository
   (HTTPS, optional read-only token, path filters). Repository sync records the commit SHA and
   adds, updates or removes documents as files change. Passages are embedded into
   Postgres/pgvector for semantic search and retrieval-augmented generation. **Ask the BRDs**
   answers questions from retrieved passages only, with numbered citations.
2. **Requirements**: the AI engine extracts atomic requirements (business, functional,
   non-functional, constraint, rule), keeping BRD IDs and source references. Each one is
   checked against ISO/IEC/IEEE 29148 quality characteristics: ambiguous terms, TBDs, weak
   modals, non-singular or unverifiable statements, and near-duplicates across BRDs. Analysts
   approve, edit or exclude them; **Refine with AI** rewrites a flagged requirement or splits a
   compound one, and never invents thresholds (missing values become notes for the business).
3. **Stories**: approved requirements become epics → features → user stories with Gherkin
   acceptance criteria, product-class variants and compliance tags. Each story is scored
   against INVEST and a Definition of Ready, and is shown on a story map or a list.
   **Refine with AI** proposes an improved version of a story that fixes its issues; you
   review a before/after comparison before applying it. **Estimate with AI** proposes relative
   story points with a rationale for each unestimated story. Long AI runs can be cancelled.
4. **Traceability**: a requirement-to-story matrix with coverage, gaps and orphan stories, plus
   product-class coverage: whether the stories linked to each requirement actually address the
   product classes it names (for example, a cold-chain requirement with no cold-chain criteria).
5. **Baselines**: freeze numbered, read-only snapshots of the backlog (for example a release
   sign-off), export them exactly as they were, and compare them with later versions.
   Projects are **archived** rather than deleted: an archived project leaves the list, becomes read-only
   (reads, search and Ask still work) and keeps its audit trail. Only an administrator can delete an
   archived project permanently.
6. **Review discussions**: comment threads on requirements and stories, with resolve/reopen and
   open-discussion badges. Viewers (business reviewers) can comment without edit rights.
   Editing is concurrency-safe: each requirement and story carries a revision. If two people open the same item and the first saves, the second is told it changed (their text stays in the editor) instead of silently overwriting. API clients can send `rev` for the same protection; without it a save always applies.
7. **Audit trail**: every manual edit, review decision, deletion, sync and AI run is recorded
   with who, when and field-level changes. The project's **Audit** tab lets reviewers filter by action and user and page through the whole trail; it also exports as CSV (oldest first).
8. **Export**: a stakeholder HTML report (preview in the browser, print to PDF), Excel (stories, requirements, RTM sheets), Jira CSV, Azure DevOps CSV,
   Markdown, CSV, JSON and Gherkin `.feature` test scenarios (tagged with story, requirement and
   compliance IDs for end-to-end traceability), or push directly to **Jira**, **Azure DevOps** or **GitHub Issues**, publish a **Confluence** page,
   or to any system through a signed JSON **webhook**. Tracker pushes are
   idempotent: epics and stories are linked to their issues, so pushing again updates them
   instead of creating duplicates.

## Admin dashboard

- **AI providers**: any OpenAI-compatible API (OpenAI, DeepSeek, Azure, vLLM, Ollama),
  Anthropic, or the offline heuristic. Once an endpoint and API key are entered, the dialog loads
  the provider's model list into a dropdown (custom IDs are still allowed). You can also test the
  connection and pick a default. API keys are encrypted at rest and never returned to the
  browser.
- **Generation settings**: temperature, output tokens, timeout, section size, run limits,
  retrieval depth, the embeddings provider and model, and personal-data masking.
- **Product taxonomy**: product classes with behaviour attributes (cold chain, controlled,
  serialized, ...) and detection keywords, plus attribute rules that become product-variant
  acceptance criteria. Supports CSV import (merge or replace) and export; see
  `samples/product-taxonomy-extension.csv`.
- **Integrations**: Jira (Cloud with email and API token, or Data Center with a PAT), Azure
  DevOps (organization URL and PAT), GitHub Issues (fine-grained token), Confluence (space key; one versioned page per project) and webhooks (optional
  HMAC secret), each with a connection test and a default target.
- **Users & roles**: named accounts (scrypt-hashed passwords) with admin, analyst or viewer
  roles enforced on the server. The deployment's bootstrap account is a break-glass admin.
- **Overview / Activity**: usage, token counts, failure rate and a log of every run.

## Configuration (environment)

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres with the pgvector extension (required) | - |
| `SETTINGS_ENCRYPTION_KEY` | encrypts provider API keys at rest (required to save keys) | - |
| `APP_BASIC_USER`, `APP_BASIC_PASSWORD` | bootstrap admin; enables sign-in for all users | off |
| `EMBED_DIM` | pgvector column size; must match the embeddings model | `256` |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | optional provider seeded on first start | - |
| `MAX_UPLOAD_MB`, `CHUNK_CHARS`, `MAX_CHUNKS_PER_RUN`, `RAG_TOP_K` | limits and defaults | 10, 6000, 200, 3 |
| `MAX_DOC_CHARS` | characters kept per pasted/parsed document; API bodies above `max(MAX_UPLOAD_MB, 4 x MAX_DOC_CHARS bytes) + 1 MiB` get `413` | 2000000 |
| `METRICS_TOKEN` | bearer token for `/metrics` (disabled when unset) | - |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URL` | single sign-on (Okta, Entra ID, Google, Keycloak, Salesforce...) | off |
| `OIDC_USERNAME_CLAIM`, `OIDC_SCOPES` | claim used as username; requested scopes | `email`; `openid email profile` |
| `OIDC_AUTO_PROVISION_ROLE` | role for first-time SSO users (empty = only pre-registered users) | empty |
| `PUSH_BUDGET_SECONDS` | a tracker push stops after this long and reports how many stories remain (push again to continue; nothing is duplicated). Keep it under the proxy timeout | `60` |
| `SESSION_IDLE_MINUTES` | sign SSO users out after this much inactivity (activity extends the session, up to its 8 h cap); `0` disables | `30` |
| `ALLOW_PRIVATE_REPO_HOSTS` | allow repository URLs on private networks (internal Git) | `false` |

Everything else is managed in the admin dashboard.

## Development

Requires Python 3.12 (the production runtime) or Docker.

```bash
scripts/dev-db.sh &              # port-forward the cluster Postgres to localhost:5433
scripts/dev-server.sh            # app on :8765 against a separate fabulist_test database
scripts/test.sh                  # lint + tests in a Python 3.12 container
scripts/ci.sh                    # full local gate: lint, tests, JS check, dependency audit, image build (Docker only)
```

Check dependencies for known vulnerabilities before releases:

```bash
docker run --rm -v "$PWD:/src" -w /src python:3.12-slim sh -c "pip install -q pip-audit && pip-audit -r requirements.txt"
```

`samples/order-to-cash-brd.md` is a small BRD for trying the workflow.

## Deploy to Kubernetes

`deploy/k8s` contains the namespace, a pgvector Postgres StatefulSet and the app.

```bash
FABULIST_HOST=fabulist.example.com DEPLOY_SSH=user@node scripts/deploy.sh
```

A nightly CronJob (`deploy/k8s/backup.yaml`) writes `pg_dump` backups to a separate volume and
keeps 7 days. To restore one:

```bash
kubectl -n fabulist exec -i postgres-0 -- pg_restore -U fabulist -d fabulist --clean --if-exists --exit-on-error < fabulist-YYYYMMDD-HHMMSS.dump
```

The flags matter: plain `--clean` restores the data into an empty database but prints dozens of "does not exist" errors and exits 1, so you cannot tell whether it worked. With `--if-exists --exit-on-error` a good restore is silent and exits 0, and it works both into a fresh database and over a populated one. `scripts/restore-drill.sh` proves this end to end with Docker only (seed through the app, dump like the CronJob, restore into a fresh database, then check row counts, app health, semantic search and baselines). Run it after changing the schema or the backup job.

The script generates the database password, the Basic-auth password and the encryption key
in-cluster on first deploy. They are never stored in the repository.

## Security

- Single sign-on through any OpenID Connect provider (authorization code with PKCE; signed,
  HttpOnly, SameSite session cookies; issuer, audience, expiry and nonce checks). Roles come from
  Fabulist's user list, so admins control access even with SSO.
- HTTP Basic sign-in with named users and roles remains available for API clients and as
  break-glass access. Repeated failed sign-ins are throttled per user and per client address.
- **API tokens** for automation: each named user can create personal tokens on the **Account** page
  (shown once, stored as a SHA-256 hash, 1-365 day lifetime, at most 20 each). Send
  `Authorization: Bearer fab_...`; the token acts with its owner's current role and stops working when it
  expires, is revoked, or the account is disabled or deleted. Admins can list and revoke any token
  (`/api/admin/tokens`). Bad tokens are throttled like bad passwords. The bootstrap admin cannot own tokens.
- Mutating API calls require an `X-Requested-With` header (CSRF), and responses carry a strict
  Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff` and `no-referrer`. API responses are `Cache-Control: no-store`, and HSTS is sent when the request arrived over HTTPS (`X-Forwarded-Proto`).
- API keys and tokens are encrypted at rest and never returned to the browser.
- Repository URLs must be HTTPS and must not resolve to private or cluster addresses (SSRF
  guard). Git runs with file and ssh protocols disabled, and tokens are passed via the
  environment, not process arguments.
- Personal identifiers are masked before text is sent to external AI providers (configurable).

## Operations

- **Logs**: one JSON line per request (`method`, `route`, `status`, `ms`, `user`) and per error,
  ready for Loki or any log pipeline.
- **Version**: the image carries its git revision (`APP_VERSION`, set by `scripts/deploy.sh`), reported as `version` by the authenticated `/api/meta`.
- **Metrics**: `/metrics` in Prometheus format, enabled by `METRICS_TOKEN` (bearer auth). It
  covers request rate, status and latency per route, AI runs by stage and outcome, LLM tokens,
  tracker pushes, and inventory gauges. `deploy/k8s/monitoring.yaml` adds a ServiceMonitor,
  alert rules and a Grafana dashboard for kube-prometheus-stack.

### Runbook

| Alert | Check |
|---|---|
| FabulistDown | `kubectl -n fabulist get pods`; app logs; `/healthz` fails when Postgres is unreachable, so check `postgres-0` too. |
| FabulistHighErrorRate | Filter logs for `status >= 500`; look for database errors or provider timeouts. |
| FabulistAIRunsFailing | Admin → Activity shows each run's error. Use Admin → AI providers → Test to verify the endpoint, key and model. |
| FabulistDependencyAudit | `kubectl -n fabulist logs job/<latest fabulist-dep-audit job>` lists packages and fixed versions; upgrade `requirements.txt`, run `scripts/test.sh`, redeploy. |
| FabulistBackupMissing | `kubectl -n fabulist get jobs` and the `fabulist-backup` job logs; make sure the backups volume has space. |

## Contributing

See `CONTRIBUTING.md` for the branch, test and release workflow, `SECURITY.md` for reporting vulnerabilities and `CHANGELOG.md` for notable changes.

## License

MIT
