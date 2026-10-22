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
   Before a large run (20 or more AI requests) the app says how many requests it will make, with a time and token
   estimate learned from your own finished runs, and asks for confirmation; a run over the configured limit is refused up
   front with what to change.
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
   **Portability**: *Project settings > Download archive* exports a whole project (documents, requirements, stories with
   their keys, statuses and order, baselines, comments and the audit history) as one versioned JSON file, and the
   **Import** button on the project list restores it as a new project (never over an existing one; the name gets
   *(imported)*). Imported items go through the same validation as manual edits, nothing is written unless the whole file
   is valid, and the audit trail gets an *imported* entry stating that the earlier entries come from the archive and are
   not verified here. AI run logs, repository connections and tracker links stay behind (they belong to the installation).
8. **Export**: a stakeholder HTML report (preview in the browser, print to PDF; it states when it was generated and its scope, shows a status breakdown, has page numbers and repeating table headers, and ends with a review-and-approval block; baseline exports also state the version and who froze it), Excel (stories, requirements, RTM sheets), Jira CSV, Azure DevOps CSV,
   Markdown, CSV, JSON and Gherkin `.feature` test scenarios (tagged with story, requirement and
   compliance IDs for end-to-end traceability), or push directly to **Jira**, **Azure DevOps** or **GitHub Issues**, publish a **Confluence** page,
   or to any system through a signed JSON **webhook**. Tracker pushes are
   idempotent: epics and stories are linked to their issues, so pushing again updates them
   instead of creating duplicates.

### Receiving webhooks

Each delivery is one JSON `POST` with `X-Fabulist-Delivery` (unique per delivery, the same on retries: drop duplicates),
`X-Fabulist-Timestamp` (Unix seconds) and, when a secret is set, `X-Fabulist-Signature-V2: sha256=<hex>`, the HMAC-SHA256 of
`"<timestamp>.<raw body>"`. Verify the signature in constant time and reject timestamps older than your tolerance (5 minutes
is typical), so a captured request cannot be replayed:

```python
import hashlib, hmac, time

def verify(secret: str, body: bytes, headers, tolerance=300) -> bool:
    ts = headers["X-Fabulist-Timestamp"]
    if abs(time.time() - int(ts)) > tolerance:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-Fabulist-Signature-V2"])
```

`X-Fabulist-Signature` (HMAC of the body alone) is still sent for receivers written before V2, but it cannot detect replays.

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
| `MAX_UNZIPPED_MB`, `MAX_PDF_PAGES` | DOCX/XLSX may expand to at most this many MB (decompression-bomb guard; the upload limit counts compressed bytes), and PDFs may have at most this many pages | `200`, `500` |
| `REPO_CLONE_TIMEOUT`, `MAX_REPO_MB` | seconds a Git sync may spend cloning (keep under the proxy timeout), and the size a clone may grow to before it is stopped | `60`, `500` |
| `MAX_ARCHIVE_MB` | size of a project archive that may be imported | `50` |
| `MAX_INTERACTIVE_PER_USER` | of those, how many one person may have running at once, so one person cannot take the whole pool and lock everyone else out (they get `429` with `Retry-After`) | `3` |
| `MAX_INTERACTIVE_AI_CALLS` | AI calls made inside a request (Refine, Estimate, Ask the BRDs, provider test) in flight at once; beyond that the request is refused immediately with `429` instead of holding a web thread | `8` |
| `MALLOC_ARENA_MAX` | set to `2` in the image (not a Fabulist setting): without it glibc kept every thread's freed memory and 20 large uploads ran the pod out of memory | `2` |
| `MAX_CONCURRENT_RUNS` | AI runs in progress at once on this server; they run in their own pool so they can never starve the web requests. A run started beyond the limit is queued (see `MAX_QUEUED_RUNS`) | `4` |
| `MAX_QUEUED_RUNS` | Runs allowed to wait for a free slot, oldest first; a start beyond this gets `429` with `Retry-After`. Runs still waiting when the server restarts (for example during a deploy) are started again by the new server, in order; a run that was already executing is failed with a clear message | `50` |
| `STALE_RUN_MINUTES` | an AI run with no progress for this long is marked stuck and stops blocking its project (starting a new run recovers it) | `15` |
| `MAX_DOC_CHARS` | characters kept per pasted/parsed document; API bodies above `max(MAX_UPLOAD_MB, 4 x MAX_DOC_CHARS bytes) + 1 MiB` get `413` | 2000000 |
| `METRICS_TOKEN` | bearer token for `/metrics` (disabled when unset) | - |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URL` | single sign-on (Okta, Entra ID, Google, Keycloak, Salesforce...) | off |
| `OIDC_USERNAME_CLAIM`, `OIDC_SCOPES` | claim used as username; requested scopes | `email`; `openid email profile` |
| `OIDC_AUTO_PROVISION_ROLE` | role for first-time SSO users (empty = only pre-registered users) | empty |
| `DB_POOL_MAX`, `DB_POOL_TIMEOUT` | database connections per process, and seconds a request waits for one before getting a retryable 503 | `20`, `10` |
| `MAX_CONCURRENT_PARSES`, `PARSE_WAIT_SECONDS` | documents read and indexed at once (a large Word or PDF file takes tens of MB and seconds of CPU); the rest wait, and get `503` with `Retry-After` after this many seconds | `2`, `60` |
| `LLM_TIMEOUT_SECONDS` | default timeout for one AI provider request (the Generation settings page overrides it) | `120` |
| `LLM_MAX_ATTEMPTS` | tries per AI request on rate limits, server errors and network failures; the wait between tries is the provider's `Retry-After`, else 1.5 s doubling up to a minute (a large run makes hundreds of requests, so a few quick tries failed whole runs), and a rate limit pauses every call to that provider | `6` |
| `MAX_RUN_AI_CALLS` | background AI calls in flight at once across all runs (each run asks `ai_concurrency` sections at a time, a generation setting, default 4) | `8` |
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
scripts/ci-k8s.sh                # the same gate (minus image build) in a temporary cluster namespace; needs only kubectl
scripts/ui-check-k8s.sh             # browser regression checks (tests/ui) in a temporary cluster namespace
scripts/load-test-k8s.sh            # load scenarios (tests/load): 20 requesters x 20 BRDs at once; a project with 1,000+ BRDs (cluster, offline AI provider)
scripts/release-check-k8s.sh        # the whole battery on the cluster (gate, browser suite, load scenarios, image scan) and one summary: is this build ready?
scripts/scan-image-k8s.sh           # Trivy scan of the deployed image for fixable HIGH/CRITICAL vulnerabilities (cluster)
scripts/ci.sh                    # full local gate: lint, tests, JS check, dependency audit, image build (Docker only)
```

Check dependencies for known vulnerabilities before releases:

```bash
docker run --rm -v "$PWD:/src" -w /src python:3.12-slim sh -c "pip install -q pip-audit && pip-audit -r requirements.txt"
```

`samples/order-to-cash-brd.md` is a small BRD for trying the workflow.

## API

Everything the web app does is available over HTTP. The reference is built into the app at `/docs` (same sign-in; it works offline and under the Content Security Policy, unlike FastAPI's stock page, which loads its scripts from a public CDN), and the machine-readable description is at `/openapi.json`. Authenticate with a personal API token (`Authorization: Bearer fab_...`, created on the Account page), Basic credentials or a single-sign-on session; changes made with Basic credentials or a session also need `X-Requested-With: fabulist`.

## Who can see a project

By default every signed-in person sees every project. With named accounts, a project can instead be **private**: choose "Only people I choose" when creating it, or under Edit > Who can see this project. Only the people chosen, and administrators, can then see it, list it, open anything inside it, export it or search it; everyone else gets "not found" (the project does not announce that it exists). People keep their own role, so a chosen viewer is still read-only, and the person making the change always stays in. The check is one guard on every route that names a project, so routes added later are covered too. Changes to who can see a project appear in its audit trail. `GET/PUT /api/projects/{id}/access` and `GET /api/people` expose it to scripts.

## Deploy to Kubernetes

`deploy/k8s` contains the namespace, a pgvector Postgres StatefulSet, the app and its network policy.

```bash
FABULIST_HOST=fabulist.example.com scripts/deploy.sh
```

Needs only `kubectl` access: the image is built **on the cluster** (Kaniko in a throwaway namespace, pushed to the
node registry at `localhost:32000`, override with `REGISTRY`), so no Docker is needed on your machine.
The app pod runs with a read-only root filesystem (writable `/tmp` only), no Linux capabilities and the default
seccomp profile. `networkpolicy.yaml` lets only the ingress controller and Prometheus reach the app and only the
namespace's own pods reach Postgres (Calico or another policy-enforcing CNI is required for it to take effect).

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
  guard). The clone connects to the address that passed the check (no second DNS lookup) and never follows HTTP redirects, so a public host cannot bounce it to an internal one. Git runs with file and ssh protocols disabled, and tokens are passed via the
  environment, not process arguments.
- Personal identifiers are masked before text is sent to external AI providers (configurable).

## Operations

- **Logs**: one JSON line per request (`method`, `route`, `status`, `ms`, `user`) and per error,
  ready for Loki or any log pipeline.
- **Version**: the image carries its git revision (`APP_VERSION`, set by `scripts/deploy.sh`), reported as `version` by the authenticated `/api/meta`.
- **Metrics**: `/metrics` in Prometheus format, enabled by `METRICS_TOKEN` (bearer auth). It
  covers request rate, status and latency per route, AI runs by stage and outcome, LLM tokens,
  tracker pushes, and inventory gauges. It also reports `fabulist_database_up` (the scrape keeps working during a database outage), failed sign-ins and token attempts by kind, edit conflicts, 503s from an unreachable database, and stories left unpushed by the time limit. `deploy/k8s/monitoring.yaml` adds a ServiceMonitor,
  alert rules and a Grafana dashboard for kube-prometheus-stack.

### Capacity and sizing

Measured on the cluster with `scripts/load-test-k8s.sh` (offline AI provider, one app pod with 1 GiB of memory and the default settings above). Re-run it after changing limits or the pod's resources.

| Scenario | Result |
|---|---|
| 20 requesters each submitting 20 BRDs (~30 KB) at the same moment | all 400 stored in about 25 s; the health probe never waits more than 0.25 s; memory peak about 530 MiB |
| 20 runs started at the same instant against an AI provider that takes 0.25 s per request | 4 run, 16 queue, none refused; all finish in about 31 s; never more than 4 running |
| 20 requesters uploading a 6 MB Word file at the same moment | all stored in about 40 s (two are read at a time); memory peak about 460 MiB |
| one project with 1,100 BRDs (up to 165 MB of text) | storing 85 BRDs/s; listing documents 0.04 s; passage search 10 ms; project export streams in 0.3 s with a 210 MiB peak; read into requirements in 44 batched runs |
| 6 runs of 46 sections against an AI provider that fails 25% of requests (rate limits, server errors, non-JSON replies) | all 6 finish; with three quick attempts per request only 4 did, and a run of hundreds of sections would almost never finish |
| 300 projects | home list, project summary, metrics scrape and admin overview about 10 ms each |
| one project with 50,600 requirements | no crash (it ran the pod out of memory before near-duplicate detection was bounded); 3-5 s per heavy call, 372 MiB peak |

What to turn when it is not enough: more concurrent AI runs (`MAX_CONCURRENT_RUNS`, bounded by what the AI provider allows) and more background calls (`MAX_RUN_AI_CALLS`, with the *AI requests in parallel per run* generation setting); more documents read at once (`MAX_CONCURRENT_PARSES`, about 100 MB each for a very large file) only together with a larger memory limit in `deploy/k8s/app.yaml`. With a real AI provider a run is as long as the provider is slow: 46 sections at 30 s each is about 23 minutes sequentially; the default of 4 sections in parallel, under the cap of 8 calls across all runs, cuts that to a few minutes per run and about an hour for a burst of 20. A run is limited to *Max sections per run* (default 200); larger intakes are read in batches ("New BRDs only") and large backlogs get their stories in batches ("Requirements without stories").

### Runbook

| Alert | Check |
|---|---|
| FabulistDown | `kubectl -n fabulist get pods`; app logs; `/healthz` returns 503 when Postgres is unreachable (API calls then return a 503 with `Retry-After`), so check `postgres-0` too. The app reconnects by itself when the database returns. |
| FabulistDatabaseUnavailable | The app is up but `fabulist_database_up` is 0 and users get 503s. `kubectl -n fabulist get pods` and `describe pod postgres-0`; check its volume and logs. No app restart is needed: it reconnects by itself. |
| FabulistAuthFailureSpike | Filter the JSON logs for `failed sign-in` and look at `ip` and `user`. Repeated failures are throttled per user and per address; disable or rotate the targeted account and block the source at the edge if it persists. |
| FabulistHighErrorRate | Filter logs for `status >= 500`; look for database errors or provider timeouts. |
| (AI runs queued or refused) | Runs beyond `MAX_CONCURRENT_RUNS` wait in a queue (`fabulist_queued_runs`; alert `FabulistRunQueueBacklog` when more than 20 wait for 30 minutes). Only a full queue is refused with 429 (`fabulist_runs_total{status="rejected"}`). Raise `MAX_CONCURRENT_RUNS` if the node and the AI provider can take more. |
| FabulistAIProviderThrottling | The AI provider is rate-limiting or failing requests; runs still finish (requests are retried, honouring `Retry-After`) but slowly. Check the provider's status page and your quota; lower `MAX_RUN_AI_CALLS` or the *AI requests in parallel per run* generation setting. `fabulist_llm_retries_total` shows the reason. |
| FabulistMemoryNearLimit | The app is over 85% of its memory limit and will be killed (OOM) if it grows. Look for very large projects or exports in progress and for many large uploads at once (`MAX_CONCURRENT_PARSES`); raise the pod's memory limit in `deploy/k8s/app.yaml` if the load is legitimate. The Grafana *Capacity* row shows resident, peak and limit. |
| FabulistAIRunsFailing | Admin → Activity shows each run's error. A run whose provider stopped answering is marked *stuck* after `STALE_RUN_MINUTES` and Cancel takes effect within one provider timeout. Use Admin → AI providers → Test to verify the endpoint, key and model. |
| FabulistDependencyAudit | `kubectl -n fabulist logs job/<latest fabulist-dep-audit job>` lists packages and fixed versions; upgrade `requirements.txt`, run `scripts/test.sh`, redeploy. |
| FabulistBackupMissing | `kubectl -n fabulist get jobs` and the `fabulist-backup` job logs; make sure the backups volume has space. |

## Contributing

See `CONTRIBUTING.md` for the branch, test and release workflow, `SECURITY.md` for reporting vulnerabilities and `CHANGELOG.md` for notable changes.

## License

MIT
