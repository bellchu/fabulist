"""Bootstrap configuration from the environment.

Runtime-tunable values (AI providers, generation parameters) live in the database
and are managed from the admin dashboard; the env values below are only defaults
and infrastructure settings.
"""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Git revision baked into the image at build time (see Dockerfile / scripts/deploy.sh).
VERSION = os.environ.get("APP_VERSION", "dev")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_MB", 10) * 1024 * 1024
MAX_DOC_CHARS = _int("MAX_DOC_CHARS", 2_000_000)
MAX_UNZIPPED_MB = _int("MAX_UNZIPPED_MB", 200)  # DOCX/XLSX are ZIPs: cap what they may expand to (decompression bombs)
MAX_PDF_PAGES = _int("MAX_PDF_PAGES", 500)
MAX_INTERACTIVE_AI_CALLS = _int("MAX_INTERACTIVE_AI_CALLS", 8)  # AI calls made inside a request (refine, estimate, Ask) in flight at once
MAX_RUN_AI_CALLS = _int("MAX_RUN_AI_CALLS", 8)  # background AI calls in flight at once across all runs (each run asks several sections at a time)
MAX_QUEUED_RUNS = _int("MAX_QUEUED_RUNS", 50)  # runs allowed to wait for a free slot; beyond this the server says it is full (429)
MAX_CONCURRENT_RUNS = _int("MAX_CONCURRENT_RUNS", 4)  # AI runs in progress at once (they run in their own pool, never in the request threads)
STALE_RUN_MINUTES = _int("STALE_RUN_MINUTES", 15)  # a run with no progress for this long is marked stuck and stops blocking its project
REPO_CLONE_TIMEOUT = _int("REPO_CLONE_TIMEOUT", 60)  # seconds; keep under the proxy timeout (~100 s)
MAX_REPO_MB = _int("MAX_REPO_MB", 500)  # a clone that grows past this is killed


MAX_ARCHIVE_MB = _int("MAX_ARCHIVE_MB", 50)  # size of a project archive that may be imported


def max_body_bytes(path: str = "") -> int:
    """Largest accepted API request body: an upload, or pasted text (up to 4 bytes per character), plus form overhead."""
    if path == "/api/projects/import":
        return MAX_ARCHIVE_MB * 1024 * 1024
    return max(MAX_UPLOAD_BYTES, MAX_DOC_CHARS * 4) + 1024 * 1024

# Defaults for admin-tunable generation settings.
CHUNK_CHARS = _int("CHUNK_CHARS", 6000)
MAX_CHUNKS_PER_RUN = _int("MAX_CHUNKS_PER_RUN", 200)
RAG_TOP_K = _int("RAG_TOP_K", 3)

# Optional provider seeded on first start (e.g. LLM_PROVIDER=openai). Afterwards
# providers are managed in the admin dashboard.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock").lower()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT = _int("LLM_TIMEOUT_SECONDS", 120)

# pgvector column size; must match the embedding model configured in admin.
EMBED_DIM = _int("EMBED_DIM", 256)

# Encrypts provider API keys at rest. Required to store keys from the admin UI.
SETTINGS_KEY = os.environ.get("SETTINGS_ENCRYPTION_KEY", "")

# Optional HTTP Basic protection for the whole app.
BASIC_USER = os.environ.get("APP_BASIC_USER", "")
BASIC_PASSWORD = os.environ.get("APP_BASIC_PASSWORD", "")

# Bearer token Prometheus uses to scrape /metrics; the endpoint is disabled when unset.
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")

# Repository URLs that resolve to private/internal addresses are refused unless this is set
# (e.g. for an internal Git server). Guards against server-side request forgery.
ALLOW_PRIVATE_REPO_HOSTS = os.environ.get("ALLOW_PRIVATE_REPO_HOSTS", "").lower() in ("1", "true", "yes")

# OpenID Connect single sign-on (optional). Enabled when issuer, client id and redirect URL are set.
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URL = os.environ.get("OIDC_REDIRECT_URL", "")  # e.g. https://fabulist.example.com/auth/callback
OIDC_SCOPES = os.environ.get("OIDC_SCOPES", "openid email profile")
OIDC_USERNAME_CLAIM = os.environ.get("OIDC_USERNAME_CLAIM", "email")
# Role for SSO users not yet in Fabulist's user list; empty = only pre-registered users may sign in.
OIDC_AUTO_PROVISION_ROLE = os.environ.get("OIDC_AUTO_PROVISION_ROLE", "")
PUSH_BUDGET_SECONDS = _int("PUSH_BUDGET_SECONDS", 60)  # a push stops after this long and reports what is left (the edge proxy times out at ~100 s)
SESSION_IDLE_MINUTES = _int("SESSION_IDLE_MINUTES", 30)  # sign out after this much inactivity; 0 disables
