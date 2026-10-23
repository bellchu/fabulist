"""Embeddings + chunk storage/retrieval for RAG over a project's BRD documents."""
import hashlib
import math
import re
from typing import List

import httpx

from . import config, db, llm, settings

_TOKEN = re.compile(r"[a-z0-9]{2,}")
INDEX_BATCH = 100  # chunks embedded and inserted at a time


def _hash_embed(text: str) -> List[float]:
    """Deterministic bag-of-words hashing embedding. Offline fallback, not semantic."""
    v = [0.0] * config.EMBED_DIM
    for tok in _TOKEN.findall(text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % config.EMBED_DIM] += 1.0 if (h >> 64) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


EMBED_ATTEMPTS = 3


def _post_batch(url: str, headers: dict, model: str, batch: List[str]) -> List[List[float]]:
    """One /embeddings request, tried up to EMBED_ATTEMPTS times when the provider hiccups (network trouble, 429, 5xx); anything else is final."""
    last = "unknown error"
    for attempt in range(EMBED_ATTEMPTS):
        try:
            resp = httpx.post(url, headers=headers, timeout=60, json={"model": model, "input": batch})
        except httpx.HTTPError as exc:
            last = "%s contacting the embeddings provider" % type(exc).__name__
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last = "the embeddings provider answered HTTP %d" % resp.status_code
            elif resp.status_code >= 400:
                raise RuntimeError("The embeddings provider refused the request (HTTP %d): check the model name and the key" % resp.status_code)
            else:
                return [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]
        if attempt < EMBED_ATTEMPTS - 1:
            llm._sleep(1.5 * (2 ** attempt))
    raise RuntimeError("Could not get embeddings after %d attempts: %s" % (EMBED_ATTEMPTS, last))


def _remote_embed(texts: List[str], provider_id: str, model: str) -> List[List[float]]:
    profile = settings.get_provider(provider_id)
    if not model:
        raise RuntimeError("Embedding model is not configured")
    url = profile["base_url"].rstrip("/") + "/embeddings"
    headers = {"Authorization": "Bearer " + profile["api_key"]} if profile["api_key"] else {}
    out: List[List[float]] = []
    for i in range(0, len(texts), 64):
        out.extend(_post_batch(url, headers, model, texts[i:i + 64]))
    for e in out:
        if len(e) != config.EMBED_DIM:
            raise RuntimeError("Embedding size %d != EMBED_DIM %d" % (len(e), config.EMBED_DIM))
    return out


def embed(texts: List[str]) -> List[List[float]]:
    s = settings.get_settings()
    if s["embed_provider_id"]:
        return _remote_embed(texts, s["embed_provider_id"], s["embed_model"])
    return [_hash_embed(t) for t in texts]


def split_text(text: str, size: int = 1200) -> List[str]:
    """Paragraph-aware splitter for retrieval chunks."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        cur = (cur + "\n\n" + para).strip() if cur else para
    if cur:
        out.append(cur)
    return out


def index_document(project_id: str, document_id: str, text: str) -> int:
    chunks = split_text(text)
    if not chunks:
        return 0
    # In batches inside one transaction: a 2 MB document is ~1,700 chunks, and embedding them all at once held tens of MB of vectors
    # per document, which added up when many large documents arrived together.
    with db.conn() as c:
        with c.cursor() as cur:
            for start in range(0, len(chunks), INDEX_BATCH):
                batch = chunks[start:start + INDEX_BATCH]
                cur.executemany(
                    "INSERT INTO chunks (id, project_id, document_id, seq, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s::vector)",
                    [(db.new_id(), project_id, document_id, start + i, t, db.vec(v)) for i, (t, v) in enumerate(zip(batch, embed(batch)))],
                )
    return len(chunks)


def search(project_id: str, query: str, k: int = 5, exclude_document: str = "") -> List[dict]:
    qv = db.vec(embed([query])[0])
    return db.rows(
        "SELECT c.text, d.filename, c.seq, 1 - (c.embedding <=> %s::vector) AS score "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.project_id = %s AND c.document_id <> %s "
        "ORDER BY c.embedding <=> %s::vector LIMIT %s",
        (qv, project_id, exclude_document, qv, k),
    )


def reindex_all() -> int:
    """Rebuild every chunk embedding, e.g. after changing the embeddings provider."""
    docs = db.rows("SELECT id, project_id, text FROM documents")
    db.execute("DELETE FROM chunks")
    return sum(index_document(d["project_id"], d["id"], d["text"]) for d in docs)
