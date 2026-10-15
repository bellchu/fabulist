"""Runtime configuration, read from the environment only."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_MB", 10) * 1024 * 1024
MAX_DOC_CHARS = _int("MAX_DOC_CHARS", 2_000_000)
CHUNK_CHARS = _int("CHUNK_CHARS", 6000)
MAX_CHUNKS_PER_RUN = _int("MAX_CHUNKS_PER_RUN", 200)

# "mock" works offline; "openai" covers any OpenAI-compatible API (OpenAI,
# DeepSeek, vLLM, ...); "anthropic" uses the Messages API.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock").lower()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT = _int("LLM_TIMEOUT_SECONDS", 120)
ALLOWED_PROVIDERS = [
    p.strip().lower()
    for p in os.environ.get("ALLOWED_PROVIDERS", "mock,openai,anthropic").split(",")
    if p.strip()
]

# Optional HTTP Basic protection for the whole app.
BASIC_USER = os.environ.get("APP_BASIC_USER", "")
BASIC_PASSWORD = os.environ.get("APP_BASIC_PASSWORD", "")

# Embeddings for RAG. "hash" is a deterministic offline fallback; "openai" calls an
# OpenAI-compatible /embeddings endpoint. EMBED_DIM must match the model's output.
EMBED_PROVIDER = os.environ.get("EMBED_PROVIDER", "hash").lower()
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", LLM_BASE_URL)
EMBED_API_KEY = os.environ.get("EMBED_API_KEY", LLM_API_KEY)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "")
EMBED_DIM = _int("EMBED_DIM", 256)
RAG_TOP_K = _int("RAG_TOP_K", 3)
