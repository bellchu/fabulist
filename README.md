# Fabulist

Turn business requirements documents (BRDs) into a reviewed, traceable agile backlog.
Built for enterprise application delivery (Salesforce, ERP, CRM), first for medical product
distribution, where requirements vary by product class (cold chain, controlled,
serialized, kits, devices...). The business domain the AI is told about and the product
classes are settings (Admin), so other industries can use it: set the domain, then replace
the product taxonomy with your own classes (CSV import).

**Contents:** [Workflow](#workflow) · [Admin dashboard](#admin-dashboard) · [Configuration](#configuration-environment) · [Development](#development) · [API](#api) ([cookbook](#automation-cookbook)) · [Who can see a project](#who-can-see-a-project) · [Deploy to Kubernetes](#deploy-to-kubernetes) · [Security](#security) · [Operations](#operations) ([capacity](#capacity-and-sizing), [runbook](#runbook)) · [Architecture](#architecture) · [Contributing](#contributing) · [License](#license). Also: [architecture notes](docs/ARCHITECTURE.md), [acceptance scenarios](docs/ACCEPTANCE.md), [changelog](CHANGELOG.md), [security policy](SECURITY.md).

## Workflow

The app follows common business-analysis practice: BRD → atomic requirements with stable
IDs → human review → epics, features and stories → requirements traceability matrix (RTM).

1. **Sources**: upload PDF, Word, Excel, CSV, Markdown or text BRDs, paste text, or connect a Git repository
   (HTTPS, optional read-only token, path filters). Repository sync records the commit SHA and
   adds, updates or removes documents as files change. Passages are embedded into
   Postgres/pgvector for semantic search and retrieval-augmented generation. **Ask the BRDs**
   answers questions from retrieved passages only, with numbered citations.
   The search box at the top finds projects by name and, across every project you can open, requirements and
   stories by their words or compliance tag (`GET /api/search`; searching SOX lists everything tagged SOX); archived projects and private projects you are not part of are
   left out.
2. **Requirements**: the AI engine extracts atomic requirements (business, functional,
   non-functional, constraint, rule), keeping BRD IDs and source references. Each one is
   checked against ISO/IEC/IEEE 29148 quality characteristics: ambiguous terms, TBDs, weak
   modals, non-singular or unverifiable statements, and near-duplicates across BRDs. Analysts
   approve, edit or exclude them; **Refine with AI** rewrites a flagged requirement or splits a
   compound one, and never invents thresholds (missing values become notes for the business).
   Requirements that already exist in a spreadsheet are added with **Import file** (CSV or Excel, up to
   2,000 rows; see `samples/requirements-import.csv`): a column called Requirement or Statement is needed, and
   Type, Epic, Section, Source ID, Actors and Status are used when present. Everything is checked first, repeats
   are skipped, and Fabulist's own Requirements export imports back.
   Tick several rows to work on them together: approve, exclude, **Assign…**, **Set…** (epic, section or type for
   requirements; epic, feature or priority for stories), **Tag…** (add or remove a compliance tag; the project's own
   spelling is reused) and, for requirements, **Merge…** (fold duplicates into the one you keep; the others are
   excluded with a pointer, never deleted). **Split…** in a requirement's drawer divides one that says several things.
   The list filters by compliance tag, and the project overview shows each tag's coverage by stories. Press `?` for the
   keyboard shortcuts.
3. **Stories**: approved requirements become epics → features → user stories with Gherkin
   acceptance criteria, product-class variants and compliance tags. Each story is scored
   against INVEST and a Definition of Ready, and is shown on a story map or a list.
   **Refine with AI** proposes an improved version of a story that fixes its issues; you
   review a before/after comparison before applying it. **Estimate with AI** proposes relative
   story points with a rationale for each unestimated story. Long AI runs can be cancelled.
   Stories that already exist elsewhere (a Jira CSV export, another Fabulist's Stories export) come in with **Import file**
   on the Stories tab (CSV or Excel; a Title or Summary column is needed; checked first, repeats skipped; compliance tags, product scope
   and open questions come across too).
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
6. **Dividing the review**: requirements and stories can be assigned to a person (a picker of the named accounts, or free text on an
   installation without accounts; **Assign…** does many at once). Each list filters by assignee, the project list opens with **Waiting for you** for
   the draft items assigned to you across projects, and an optional webhook subscription (`item.assigned`) lets a chat bridge tell the person.
   Every requirement and story has an address (`#/p/<project>/requirements/REQ-002`) that opens it: the keys in search results and the audit trail
   are links, and the drawers have a **Copy link** button.
7. **Review discussions**: comment threads on requirements and stories, with resolve/reopen and
   open-discussion badges. Viewers (business reviewers) can comment without edit rights.
   Editing is concurrency-safe: each requirement and story carries a revision. If two people open the same item and the first saves, the second is told it changed (their text stays in the editor) instead of silently overwriting. API clients can send `rev` for the same protection; without it a save always applies.
8. **Audit trail**: every manual edit, review decision, deletion, sync and AI run is recorded
   with who, when and field-level changes. The project's **Audit** tab lets reviewers filter by action and user and page through the whole trail; it also exports as CSV (oldest first).
   **Portability**: *Project settings > Download archive* exports a whole project (documents, requirements, stories with
   their keys, statuses and order, baselines, comments and the audit history) as one versioned JSON file, and the
   **Import** button on the project list restores it as a new project (never over an existing one; the name gets
   *(imported)*). Imported items go through the same validation as manual edits, nothing is written unless the whole file
   is valid, and the audit trail gets an *imported* entry stating that the earlier entries come from the archive and are
   not verified here. AI run logs, repository connections and tracker links stay behind (they belong to the installation).
9. **Export**: a stakeholder HTML report (preview in the browser, print to PDF; it states when it was generated and its scope, shows a status breakdown, has page numbers and repeating table headers, and ends with a review-and-approval block; baseline exports also state the version and who froze it), Excel (stories, requirements, RTM sheets), Jira CSV, Azure DevOps CSV,
   Markdown, CSV (including a traceability matrix and a per-tag **compliance matrix** for auditors), JSON and Gherkin `.feature` test scenarios (tagged with story, requirement and
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

A webhook that ticks "Also tell this webhook when work is assigned to someone" also receives `event: item.assigned` (`project`, `assignee`, `assigned_by`, and `items`: `kind`, `key`, `title`, `previous`; one event per assignment or bulk assignment; clearing an assignee sends nothing), which a chat bridge can turn into a message to that person. A webhook that ticks "Also tell this webhook when an AI run finishes" also receives `event: run.finished` with `project` (`id`, `name`) and `run` (`id`, `stage`, `status`, `items`, `error`, `started_by`, `tokens_in`, `tokens_out`, `duration_ms`); dispatch on the `event` field.

`X-Fabulist-Signature` (HMAC of the body alone) is still sent for receivers written before V2, but it cannot detect replays.

## Admin dashboard

- **AI providers**: any OpenAI-compatible API (OpenAI, DeepSeek, Azure, vLLM, Ollama),
  Anthropic, or the offline heuristic. Once an endpoint and API key are entered, the dialog loads
  the provider's model list into a dropdown (custom IDs are still allowed). You can also test the
  connection and pick a default. API keys are encrypted at rest and never returned to the
  browser.
- **Generation settings**: the business domain the AI is told it is writing for (default: medical product distribution; change it for any other industry), temperature, output tokens, timeout, section size, run limits,
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
- **Administrative changes** (Activity page; `GET /api/admin/audit`, `/api/admin/audit.csv`): an append-only
  trail of who changed generation settings (old and new value), providers, accounts (role,
  disable, password reset, delete), integrations and the taxonomy. Passwords and API keys are
  never written to it, only that they were replaced. A test fails if a new admin write route
  does not record.

## Configuration (environment)

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres with the pgvector extension (required). The login the running app uses | - |
| `MIGRATION_DATABASE_URL` | optional second login, the schema owner: creates and upgrades the tables at startup and grants `DATABASE_URL`'s role the right to read and write data, but not to change the schema or to update or delete the history and administrative trail. Unset: one login does both. `scripts/deploy.sh` sets this up on Kubernetes (role `fabulist_app`) | unset |
| `SETTINGS_ENCRYPTION_KEY` | encrypts provider API keys at rest (required to save keys) | - |
| `APP_BASIC_USER`, `APP_BASIC_PASSWORD` | bootstrap admin; enables sign-in for all users | off |
| `EMBED_DIM` | pgvector column size; must match the embeddings model | `256` |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | optional provider seeded on first start | - |
| `MAX_UPLOAD_MB`, `CHUNK_CHARS`, `MAX_CHUNKS_PER_RUN`, `RAG_TOP_K` | limits and defaults | 10, 6000, 200, 3 |
| `MAX_UNZIPPED_MB`, `MAX_PDF_PAGES` | DOCX/XLSX may expand to at most this many MB (decompression-bomb guard; the upload limit counts compressed bytes), and PDFs may have at most this many pages | `200`, `500` |
| `REPO_CLONE_TIMEOUT`, `MAX_REPO_MB` | seconds a Git sync may spend cloning (keep under the proxy timeout), and the size a clone may grow to before it is stopped | `60`, `500` |
| `MAX_ARCHIVE_MB` | size of a project archive that may be imported | `50` |
| `SUPPORT_CONTACT` | where people are told to turn when something goes wrong (a helpdesk address, a chat channel): shown in the page footer and in the reference given for an unexpected error. Plain text, up to 200 characters | unset ("your administrator") |
| `ORIGIN_SECRET_REPORT_ONLY` | with `ORIGIN_SECRET` set, count what would be refused (`fabulist_origin_refused_total{mode="report"}`) and refuse nothing: switch the secret on this way, add the proxy rule, watch the counter stop growing for the proxy's traffic, then remove this setting to enforce | off |
| `ORIGIN_SECRET` | optional shared secret the proxy adds to every request as the header `X-Origin-Secret`; when set, anything without it gets `403` (health probes and the metrics scrape excepted), so a client that reaches the origin directly cannot forge the headers the per-address limits rely on. See Security | unset |
| `TRUSTED_PROXY_HOPS`, `TRUST_CF_CONNECTING_IP` | which address is the client's, for sign-in throttling and the rate limit: Cloudflare's `CF-Connecting-IP` when `TRUST_CF_CONNECTING_IP` is on (set `0` if the origin can be reached without Cloudflare, since anyone could then send that header), otherwise the address that many proxies from the right in `X-Forwarded-For` (what is left of it is the client's own claim) | `1`, `1` |
| `RATE_LIMIT_PER_MINUTE` | API requests one person (or one address, when nobody is signed in) may make per minute, with a burst of the same size; beyond it `429` with `Retry-After`. `0` turns it off. Counted in `fabulist_rate_limited_total`. Generous on purpose: the browser polls running work | `600` |
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
scripts/release-check-k8s.sh        # the whole battery on the cluster (gate, browser suite, load scenarios, image scan, restore drill) and one summary: is this build ready?
scripts/live-smoke-k8s.sh               # read-only smoke test of the running site: every page, light and dark, no script errors, no serious accessibility problems (FABULIST_HOST=...)
scripts/run-py-k8s.sh script.py        # run a Python script against the working tree on the cluster (profiling, one-off checks)
scripts/ship-k8s.sh "title"          # check the committed branch on the cluster (gate + browser suite), then PR, merge and deploy; stops at the first failure (SKIP_DEPLOY=1 for docs/tests)
scripts/sbom-k8s.sh                 # software bill of materials (CycloneDX JSON) of the app's Python packages; SBOM_SOURCE=image adds the image's operating-system packages (for procurement and security reviews)
scripts/scan-image-k8s.sh           # Trivy scan of the deployed image for fixable HIGH/CRITICAL vulnerabilities (cluster)
scripts/ci.sh                    # full local gate: lint, tests, JS check, dependency audit, image build (Docker only)
```

Check dependencies for known vulnerabilities before releases:

```bash
docker run --rm -v "$PWD:/src" -w /src python:3.12-slim sh -c "pip install -q pip-audit && pip-audit -r requirements.txt"
```

The same small BRD ships inside the app (`app/samples/order-to-cash-brd.md`): the **Try a sample project** button on an empty project list creates a project holding it (`POST /api/projects/sample`), and extraction works offline with no AI provider.

## API

Everything the web app does is available over HTTP. The reference is built into the app at `/docs` (same sign-in; it works offline and under the Content Security Policy, unlike FastAPI's stock page, which loads its scripts from a public CDN), and the machine-readable description is at `/openapi.json`. Authenticate with a personal API token (`Authorization: Bearer fab_...`, created on the Account page), Basic credentials or a single-sign-on session; changes made with Basic credentials or a session also need `X-Requested-With: fabulist`.

### Automation cookbook

A script that takes a BRD to a backlog (shell and `curl`; `$TOKEN` is a personal API token, `$HOST` the address of the app):

```sh
api() { curl -fsS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }

status() { api "$HOST/api/projects/$PID/runs?limit=1" | jq -r '.[0].status'; }
PID=$(api -X POST "$HOST/api/projects" -d '{"name":"Order to cash","platform":"salesforce"}' | jq -r .id)
curl -fsS -H "Authorization: Bearer $TOKEN" -F "file=@brd.docx" "$HOST/api/projects/$PID/documents"
api -X POST "$HOST/api/projects/$PID/runs" -d '{"stage":"requirements"}'            # 202: runs, or waits in the queue
while [ "$(status)" = running ] || [ "$(status)" = queued ]; do sleep 10; done
api -X POST "$HOST/api/projects/$PID/requirements/bulk" -d "{\"ids\":$(api "$HOST/api/projects/$PID/requirements" | jq '[.[].id]'),\"status\":\"approved\"}"
api -X POST "$HOST/api/projects/$PID/runs" -d '{"stage":"stories"}'
sleep 2; while [ "$(status)" = running ] || [ "$(status)" = queued ]; do sleep 10; done
curl -fsS -H "Authorization: Bearer $TOKEN" "$HOST/api/projects/$PID/export?format=xlsx" -o backlog.xlsx
```

Existing requirements go in with `POST /api/projects/{id}/requirements/import-file` (multipart, `.csv` or `.xlsx`). A `429` carries `Retry-After` (seconds): wait that long and repeat the call. A `503` means the database is briefly unavailable and also carries `Retry-After`. To hear when a run ends instead of polling, add a webhook integration with "Also tell this webhook when an AI run finishes".

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

What `scripts/deploy.sh` does, in order: refuses to run unless you are on a clean, up-to-date `main`; creates the secrets once (never overwriting);
applies Postgres, the network policy and the backup jobs; builds the image on the cluster; **scans it** and stops on a fixable HIGH or CRITICAL
vulnerability (`SKIP_IMAGE_SCAN=1` overrides); **waits for AI runs that are executing** (`DEPLOY_WAIT_FOR_RUNS` minutes, default 15); creates, once, the
limited database role `fabulist_app` that the app runs as (the owner login moves to `MIGRATION_DATABASE_URL`); rolls the app out with no downtime
(the new pod must be ready before the old one stops); applies the monitoring rules; and finishes with a **read-only smoke test of the live site**
(`SKIP_SMOKE=1` skips it; a problem gives a failing exit status and the roll-back command). `scripts/ship-k8s.sh` wraps all of it with the checks and
the pull request.

A nightly CronJob (`deploy/k8s/backup.yaml`) writes `pg_dump` backups to a separate volume and
keeps every dump of the last 7 days and the Sunday dump of each of the last 5 weeks. To restore one:

```bash
kubectl -n fabulist exec -i postgres-0 -- pg_restore -U fabulist -d fabulist --clean --if-exists --exit-on-error < fabulist-YYYYMMDD-HHMMSS.dump
```

The flags matter: plain `--clean` restores the data into an empty database but prints dozens of "does not exist" errors and exits 1, so you cannot tell whether it worked. With `--if-exists --exit-on-error` a good restore is silent and exits 0, and it works both into a fresh database and over a populated one. `scripts/restore-drill-k8s.sh` proves this end to end on the cluster (and `scripts/restore-drill.sh` with local Docker) (seed through the app, dump like the CronJob, restore into a fresh database, then check row counts, app health, semantic search and baselines). Run it after changing the schema or the backup job.

The script generates the database password, the Basic-auth password and the encryption key
in-cluster on first deploy. They are never stored in the repository.

## Security

- Single sign-on through any OpenID Connect provider (authorization code with PKCE; signed,
  HttpOnly, SameSite session cookies; issuer, audience, expiry and nonce checks). Roles come from
  Fabulist's user list, so admins control access even with SSO.
- HTTP Basic sign-in with named users and roles remains available for API clients and as
  break-glass access. Repeated failed sign-ins are throttled per user and per client address.
  Named users change their own password on the **Account** page (`POST /api/me/password`: current password required, 12 characters minimum). A password an
  administrator chose can be made temporary (tick the box when adding the user or resetting the password): until its owner replaces it, nothing else works.
  The Users page shows each account's last activity, for access reviews.
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
- **Keep the origin behind your proxy.** Throttling and the rate limit tell clients apart by address, taken from `CF-Connecting-IP` behind Cloudflare (`TRUST_CF_CONNECTING_IP`) or from `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`). Those headers are only as trustworthy as the path: if the ingress also answers requests that did not come through the proxy, a client can send its own and dodge the per-address limits (per-user limits and sign-in itself are not affected). Check from outside with `curl -H 'Host: <your host>' https://<origin address>/healthz`: a `200` means the origin is directly reachable. Fix it at the edge: allow only Cloudflare's published address ranges to reach the ingress (firewall or ingress allow-list), or use authenticated origin pulls. **Check first that the ingress sees real client addresses**: put a `traefik/whoami` behind it and request it directly; if `X-Forwarded-For` shows a private address (a NAT or load balancer in front of the node, as on the reference cluster, where every direct request arrived as `172.25.x.x`), an allow-list of Cloudflare's ranges at the ingress would refuse everybody, Cloudflare included, and `ORIGIN_SECRET` (below) is the option that works. Or, with no change to the network, set `ORIGIN_SECRET`: put a long random value in the `fabulist-app` secret under that key, add a Cloudflare *Modify Request Header* rule that sets `X-Origin-Secret` to the same value, and the app then answers `403` to every request that did not pass through Cloudflare (`fabulist_origin_refused_total` counts them). Set the rule first, then the secret, or the site refuses everyone. Or roll it out without risk: set `ORIGIN_SECRET_REPORT_ONLY=1` together with the secret, add the Cloudflare rule, and watch `fabulist_origin_refused_total{mode="report"}` (Grafana panel *Refused requests*): when it stops growing for your normal traffic (only direct probes remain), remove `ORIGIN_SECRET_REPORT_ONLY` and the app enforces.

## Operations

- **Logs**: one JSON line per request (`method`, `route`, `status`, `ms`, `user`) and per error,
  ready for Loki or any log pipeline.
- **Version**: the image carries its git revision (`APP_VERSION`, set by `scripts/deploy.sh`), reported as `version` by the authenticated `/api/meta`.
- **Metrics**: `/metrics` in Prometheus format, enabled by `METRICS_TOKEN` (bearer auth). It
  covers request rate, status and latency per route, AI runs by stage and outcome, LLM tokens,
  tracker pushes, and inventory gauges. It also reports `fabulist_database_up` (the scrape keeps working during a database outage), failed sign-ins and token attempts by kind, edit conflicts, 503s from an unreachable database, and stories left unpushed by the time limit. `deploy/k8s/monitoring.yaml` adds a ServiceMonitor,
  alert rules and a Grafana dashboard for kube-prometheus-stack.

### Capacity and sizing

Measured on the cluster with `scripts/load-test-k8s.sh` (offline AI provider, one app pod with 1 GiB of memory, deliberately less than the 1.5 GiB production pod, and the default settings above). Re-run it after changing limits or the pod's resources.

| Scenario | Result |
|---|---|
| 20 requesters each submitting 20 BRDs (~30 KB) at the same moment | all 400 stored in about 25 s; the health probe never waits more than 0.25 s; memory peak about 530 MiB |
| 20 runs started at the same instant against an AI provider that takes 0.25 s per request | 4 run, 16 queue, none refused; all finish in about 31 s; never more than 4 running |
| 20 requesters uploading a 6 MB Word file at the same moment | all stored in about 40 s (two are read at a time); memory peak 580-640 MiB (measured over several releases; it grew with the extra audit and ownership bookkeeping, still under the 1 GiB the test uses) |
| one project with 1,100 BRDs (up to 165 MB of text) | storing 85 BRDs/s; listing documents 0.04 s; passage search 10 ms; project export streams in 0.3 s with a 210 MiB peak; read into requirements in 44 batched runs |
| 6 runs of 46 sections against an AI provider that fails 25% of requests (rate limits, server errors, non-JSON replies) | all 6 finish; with three quick attempts per request only 4 did, and a run of hundreds of sections would almost never finish |
| 300 projects | home list, project summary, metrics scrape and admin overview about 10-20 ms each (the home list, with its activity column, 20 ms) |
| search across projects (`GET /api/search`) with 50,600 requirements | 0.08 s for a common word, 0.07 s when nothing matches (every requirement read); no index needed at this size |
| one project with 50,600 requirements | no crash (it ran the pod out of memory before near-duplicate detection was bounded); 3-5 s per heavy call, 372 MiB peak |

What to turn when it is not enough: more concurrent AI runs (`MAX_CONCURRENT_RUNS`, bounded by what the AI provider allows) and more background calls (`MAX_RUN_AI_CALLS`, with the *AI requests in parallel per run* generation setting); more documents read at once (`MAX_CONCURRENT_PARSES`, about 100 MB each for a very large file) only together with a larger memory limit in `deploy/k8s/app.yaml`. With a real AI provider a run is as long as the provider is slow: 46 sections at 30 s each is about 23 minutes sequentially; the default of 4 sections in parallel, under the cap of 8 calls across all runs, cuts that to a few minutes per run and about an hour for a burst of 20. A run is limited to *Max sections per run* (default 200); larger intakes are read in batches ("New BRDs only") and large backlogs get their stories in batches ("Requirements without stories").

### Runbook

A person who hits an unexpected error sees a reference (also the `X-Request-ID` response header). Search the JSON logs for that value (`request_id`): the access line has the route, status and user, and the error line has the stack trace.

| Alert | Check |
|---|---|
| FabulistDown | `kubectl -n fabulist get pods`; app logs; `/healthz` (readiness) returns 503 when Postgres is unreachable, which only takes the pod out of rotation (`/livez`, the liveness probe, does not look at the database, so an outage never restarts the app and its AI runs); API calls then return a 503 with `Retry-After`, so check `postgres-0` too. The app reconnects by itself when the database returns. |
| FabulistDatabaseUnavailable | The app is up but `fabulist_database_up` is 0 and users get 503s. `kubectl -n fabulist get pods` and `describe pod postgres-0`; check its volume and logs. No app restart is needed: it reconnects by itself. |
| FabulistAuthFailureSpike | Filter the JSON logs for `failed sign-in` and look at `ip` and `user`. Repeated failures are throttled per user and per address; disable or rotate the targeted account and block the source at the edge if it persists. |
| FabulistHighErrorRate | Filter logs for `status >= 500`; look for database errors or provider timeouts. |
| FabulistRunQueueBacklog | AI runs queued or refused. Runs beyond `MAX_CONCURRENT_RUNS` wait in a queue (`fabulist_queued_runs`; alert `FabulistRunQueueBacklog` when more than 20 wait for 30 minutes). Only a full queue is refused with 429 (`fabulist_runs_total{status="rejected"}`). Raise `MAX_CONCURRENT_RUNS` if the node and the AI provider can take more. |
| FabulistAIProviderThrottling | The AI provider is rate-limiting or failing requests; runs still finish (requests are retried, honouring `Retry-After`) but slowly. Check the provider's status page and your quota; lower `MAX_RUN_AI_CALLS` or the *AI requests in parallel per run* generation setting. `fabulist_llm_retries_total` shows the reason. |
| FabulistMemoryNearLimit | The app is over 85% of its memory limit and will be killed (OOM) if it grows. Look for very large projects or exports in progress and for many large uploads at once (`MAX_CONCURRENT_PARSES`); raise the pod's memory limit in `deploy/k8s/app.yaml` if the load is legitimate. The Grafana *Capacity* row shows resident, peak and limit. |
| FabulistAIRunsFailing | Admin → Activity shows each run's error. A run whose provider stopped answering is marked *stuck* after `STALE_RUN_MINUTES` and Cancel takes effect within one provider timeout. Use Admin → AI providers → Test to verify the endpoint, key and model. |
| FabulistDependencyAudit | `kubectl -n fabulist logs job/<latest fabulist-dep-audit job>` lists packages and fixed versions; upgrade `requirements.txt`, run `scripts/test.sh`, redeploy. |
| (database roles) | The app runs as `fabulist_app`, which cannot change the schema; the schema owner's login is `MIGRATION_DATABASE_URL` in the `fabulist-db` secret and is used at startup only. To undo the split (for example to roll back to an image older than this one), copy that value into `DATABASE_URL`, delete the `MIGRATION_DATABASE_URL` key and restart the app. To see who owns what: `kubectl -n fabulist exec postgres-0 -- psql -U fabulist -d fabulist -c '\dp history'`. |
| FabulistDatabaseLarge | `fabulist_database_size_bytes` is over 3.5 GB while the Postgres volume is 5 GiB (`deploy/k8s/postgres.yaml`), and the backups volume (5 GiB, seven nightly dumps) fills before it does. Admin > Overview shows the size. Expand both volumes (the storage class must allow it), or export and delete projects nobody needs (delete only after archiving; the deletion is recorded in the administrative trail). Change the 3.5e9 threshold in `deploy/k8s/monitoring.yaml` when you change the volume size. |
| FabulistRateLimiting | Requests are answered `429` because someone went over `RATE_LIMIT_PER_MINUTE` for ten minutes. In the access logs (`status` 429, with `user` or the address) find who; a script in a retry loop is the usual cause (fix it; it should wait for `Retry-After`). If the limit is simply too low for a legitimate integration, raise it. |
| FabulistOriginRefusals | Only when `ORIGIN_SECRET` is set. Requests without the right `X-Origin-Secret` get `403`: either the Cloudflare rule that adds the header was removed or changed (the site is then refusing everybody: fix the rule or unset the secret), or something is reaching the origin directly (probing: nothing to do but watch, they are refused). |
| FabulistRestarting | The app restarted twice or more in an hour; each restart fails the runs that were executing. `kubectl -n fabulist describe pod -l app=fabulist` shows the last state (`OOMKilled` means the memory limit: see FabulistMemoryNearLimit) and `kubectl -n fabulist logs deploy/fabulist --previous` the last words. Repeated liveness failures mean the process hangs: look for a stuck thread pool in the logs. |
| FabulistRestoreCheckFailed | The Sunday test restore failed: the newest nightly dump could not be restored into a scratch database (or there was no recent dump). `kubectl -n fabulist logs job/<latest fabulist-restore-check job>` says why (no backup, too old, a restore error, missing tables or triggers). Treat it as seriously as a missing backup: take one by hand (`kubectl -n fabulist create job --from=cronjob/fabulist-backup manual-1`), run the check by hand (`kubectl -n fabulist create job --from=cronjob/fabulist-restore-check check-1`) and read the output. The job only reads the backups volume; it never connects to the real database. |
| FabulistBackupMissing | `kubectl -n fabulist get jobs` and the `fabulist-backup` job logs; make sure the backups volume has space. |

## Architecture

[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) lists what to check before rollout and which automated test proves each point. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the pieces fit, the design decisions and why, the trust boundaries and what happens when something fails.

## Contributing

See `CONTRIBUTING.md` for the branch, test and release workflow, `SECURITY.md` for reporting vulnerabilities and `CHANGELOG.md` for notable changes.

## License

MIT
