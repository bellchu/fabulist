# Fabulist

Turn business requirements documents (BRDs) into a reviewed, traceable agile backlog.
Built for enterprise application delivery (Salesforce, ERP, CRM) in medical product
distribution, where requirements vary by product class (cold chain, controlled,
serialized, kits, devices...).

## Workflow

The app follows common business-analysis practice: BRD → atomic requirements with stable
IDs → human review → epics, features and stories → requirements traceability matrix (RTM).

1. **Sources**: upload PDF / DOCX / MD / TXT BRDs or paste text. Passages are embedded into
   Postgres/pgvector for semantic search and retrieval-augmented generation.
2. **Requirements**: the AI engine extracts atomic requirements (business, functional,
   non-functional, constraint, rule), keeping BRD IDs and source references. Each one is
   checked against ISO/IEC/IEEE 29148 quality characteristics: ambiguous terms, TBDs, weak
   modals, non-singular or unverifiable statements. Analysts approve, edit or exclude them.
3. **Stories**: approved requirements become epics → features → user stories with Gherkin
   acceptance criteria, product-class variants and compliance tags. Each story is scored
   against INVEST and a Definition of Ready, and is shown on a story map or a list.
4. **Traceability**: a requirement-to-story matrix with coverage, gaps and orphan stories.
5. **Export**: Excel (stories, requirements, RTM sheets), Jira CSV, Azure DevOps CSV,
   Markdown, CSV and JSON.

## Admin dashboard

- **AI providers**: any OpenAI-compatible API (OpenAI, DeepSeek, Azure, vLLM, Ollama),
  Anthropic, or the offline heuristic. You can set the endpoint, model and API key, test the
  connection and pick a default. API keys are encrypted at rest and never returned to the
  browser.
- **Generation settings**: temperature, output tokens, timeout, section size, run limits,
  retrieval depth, the embeddings provider and model, and personal-data masking.
- **Overview / Activity**: usage, token counts, failure rate and a log of every run.

## Configuration (environment)

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres with the pgvector extension (required) | - |
| `SETTINGS_ENCRYPTION_KEY` | encrypts provider API keys at rest (required to save keys) | - |
| `APP_BASIC_USER`, `APP_BASIC_PASSWORD` | optional HTTP Basic protection | off |
| `EMBED_DIM` | pgvector column size; must match the embeddings model | `256` |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | optional provider seeded on first start | - |
| `MAX_UPLOAD_MB`, `CHUNK_CHARS`, `MAX_CHUNKS_PER_RUN`, `RAG_TOP_K` | limits and defaults | 10, 6000, 200, 3 |

Everything else is managed in the admin dashboard.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
scripts/dev-db.sh &              # port-forward the cluster Postgres to localhost:5433
scripts/dev-server.sh            # app on :8765 against a separate fabulist_test database
TEST_DATABASE_URL=postgresql://... .venv/bin/pytest
```

`samples/order-to-cash-brd.md` is a small BRD for trying the workflow.

## Deploy to Kubernetes

`deploy/k8s` contains the namespace, a pgvector Postgres StatefulSet and the app.

```bash
FABULIST_HOST=fabulist.example.com DEPLOY_SSH=user@node scripts/deploy.sh
```

The script generates the database password, the Basic-auth password and the encryption key
in-cluster on first deploy. They are never stored in the repository.

## License

MIT
