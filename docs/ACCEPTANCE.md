# Acceptance scenarios

What a business owner or a security reviewer can check before rollout, and which automated test proves each one on every change. The tests run on the cluster (`scripts/ship-k8s.sh` and `scripts/release-check-k8s.sh`); this page is the human-readable version. Each row can also be walked through by hand in a few minutes (the **Try a sample project** button gives you a BRD to start from).

| # | Scenario | What you should see | Proven by |
|---|---|---|---|
| 1 | Turn a BRD into reviewed requirements | Extraction produces atomic requirements with types, sources and quality checks; you approve, edit or exclude them | `tests/ui/review_flow.py`, `tests/test_pipeline.py` |
| 2 | Turn approved requirements into stories | Epics, features, stories with Gherkin criteria, linked to requirement IDs; coverage and gaps in the traceability matrix | `tests/test_api.py`, `tests/ui/incremental_stories.py` |
| 3 | Work on a thousand BRDs | BRDs are read in batches, lists draw 300 rows at a time, nothing falls over | `tests/ui/incremental_extraction.py`, `tests/ui/paged_tables.py`, load scenario `volume` |
| 4 | Twenty people submit twenty BRDs at once | All uploads are stored, runs queue fairly, the app stays responsive | load scenarios `burst`, `slowai` |
| 5 | The AI provider is flaky or slow | Runs still finish; throttling is visible to operators | load scenario `flakyai`, `tests/test_llm.py` |
| 6 | Keep a project confidential | Only chosen people and administrators can see or find it; everyone else gets "not found" | `tests/test_project_access.py`, `tests/test_global_search.py`, `tests/ui/project_access.py` |
| 7 | Roles | Viewers read and comment but cannot change; analysts work; only administrators administer | `tests/test_users.py`, `tests/ui/a11y.py` (viewer screens) |
| 8 | Sign-in, accounts and API tokens | OIDC sign-in with roles from Fabulist's own list; people change their own password, and a temporary one must be replaced before anything else works; the Users page shows last activity; personal tokens for automation | `tests/test_sso.py`, `tests/test_tokens.py`, `tests/test_change_password.py`, `tests/test_must_change_password.py`, `tests/test_last_active.py`, `tests/test_cookbook_flow.py` |
| 9 | Who changed what | Every edit, import, document, repository, AI run and project deletion is in an append-only trail, exportable as CSV | `tests/test_audit_completeness.py`, `tests/test_audit_runs.py`, `tests/test_append_only.py` |
| 10 | Who changed the configuration | Settings, providers, accounts, tokens, integrations and taxonomy changes are in the administrative trail; secrets never are | `tests/test_admin_audit.py` |
| 11 | Get data in and out | Requirements and stories import from CSV or Excel; everything exports (Excel, CSV, Jira, Azure, Gherkin, HTML); a whole project round-trips as an archive | `tests/test_requirements_import.py`, `tests/test_stories_import.py`, `tests/test_portability.py` |
| 12 | Freeze a sign-off | Baselines are read-only snapshots you can export and compare | `tests/ui/baselines.py` |
| 13 | Push to trackers | Jira, Azure DevOps, GitHub, Confluence and webhooks (with signed deliveries and a run-finished notification) | `tests/test_integrations.py`, `tests/test_run_notifications.py` |
| 14 | Survive a deploy | No dropped requests, queued runs resume, running runs are not killed by the new server | `tests/test_k8s_manifest.py`, `tests/test_run_ownership.py`, the live smoke test at the end of every deploy |
| 15 | Survive a bad day | A database outage does not restart the app; a backup restores completely, and production proves it every Sunday by test-restoring the newest dump (retention: 7 days plus five Sundays) | `tests/test_api.py` (outage), `scripts/restore-drill-k8s.sh`, `scripts/restore-check-job.sh`, `scripts/backup-prune-job.sh` |
| 16 | The app is hard to misuse | No malformed request makes it answer 500; hostile files and text stay inert | `tests/test_api_garbage.py`, `tests/ui/hostile_input.py`, `tests/test_security_headers.py` |
| 17 | Accessible to everyone | No serious WCAG A/AA problems in light, dark or forced colours; keyboard-only use; screen readers hear navigation | `tests/ui/a11y.py`, `tests/ui/keyboard.py`, `tests/ui/forced_colors.py`, `tests/ui/page_titles.py`, `scripts/live-smoke-k8s.sh` |
| 18 | Operators can see and be alerted | Metrics, dashboards, alerts with runbook rows, request ids for support | `tests/test_monitoring.py`, `tests/test_request_ids.py`, `tests/test_admin_system.py` |
| 19 | Supply chain | Pinned dependencies, weekly updates, image scan before every deploy, bill of materials | `tests/test_deploy_guard.py`, `scripts/scan-image-k8s.sh`, `scripts/sbom-k8s.sh` |

## What is not covered

The automated checks use synthetic files and a simulated AI provider, so they say nothing about the quality of the requirements a real model writes from your documents: review a sample of your own BRDs with the AI provider you will use before relying on it. Only one replica is supported (see `docs/ARCHITECTURE.md`). Real screen readers, Windows High Contrast and mobile devices have been checked through browser emulation, not on the devices.
