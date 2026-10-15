"""BRD text -> user stories: chunk, retrieve related context (RAG), generate, validate."""
import json
import re
from typing import Any, Callable, Dict, List, Optional

from . import config, embeddings, llm, taxonomy
from .profiles import PLATFORMS

PRIORITIES = {"high", "medium", "low"}
_REQ = re.compile(r"\b(shall|must|should|needs? to|required? to|will be able|support|allow|enable|provide)\b", re.I)
_ACTOR = [
    ("sales representative", ["sales rep", "sales representative", "account manager"]),
    ("customer", ["customer", "pharmacy", "hospital", "provider", "buyer"]),
    ("warehouse operator", ["warehouse", "picker", "receiving", "shipping"]),
    ("finance analyst", ["finance", "accounting", "billing", "invoice", "chargeback"]),
    ("compliance officer", ["compliance", "regulatory", "audit", "quality"]),
    ("administrator", ["admin", "administrator", "configure"]),
    ("customer service agent", ["service agent", "support", "case"]),
]
SYSTEM_PROMPT = (
    "You are a senior business analyst converting business requirements documents (BRDs) into "
    "agile user stories for enterprise application delivery in medical product distribution. "
    "Reply with ONLY a JSON array. Each element: {\"epic\": str, \"title\": str, \"persona\": str, "
    "\"narrative\": \"As a <persona>, I want <goal>, so that <benefit>\", "
    "\"acceptance_criteria\": [Given/When/Then strings], \"priority\": \"High|Medium|Low\", "
    "\"labels\": [str], \"product_scope\": [product class names that apply, or empty for all], "
    "\"variant_criteria\": [{\"scope\": product class or attribute, \"criteria\": str}], "
    "\"compliance_tags\": [str], \"source_ref\": str, \"open_questions\": [str]}. "
    "Rules: one story per distinct requirement; do not invent requirements; put product-specific "
    "differences in variant_criteria instead of duplicating stories; list ambiguities in open_questions."
)


def _clean_list(v: Any) -> List[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def normalize_story(raw: Any, fallback_ref: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    persona = str(raw.get("persona") or "user").strip()
    prio = str(raw.get("priority") or "Medium").strip().capitalize()
    variants = []
    for v in raw.get("variant_criteria") or []:
        if isinstance(v, dict) and v.get("criteria"):
            variants.append({"scope": str(v.get("scope") or "").strip(), "criteria": str(v["criteria"]).strip()})
    return {
        "epic": str(raw.get("epic") or "General").strip()[:200],
        "title": title[:300],
        "persona": persona[:100],
        "narrative": str(raw.get("narrative") or "").strip(),
        "acceptance_criteria": _clean_list(raw.get("acceptance_criteria")),
        "priority": prio if prio.lower() in PRIORITIES else "Medium",
        "labels": _clean_list(raw.get("labels")),
        "product_scope": _clean_list(raw.get("product_scope")),
        "variant_criteria": variants,
        "compliance_tags": _clean_list(raw.get("compliance_tags")),
        "source_ref": str(raw.get("source_ref") or fallback_ref).strip()[:300],
        "open_questions": _clean_list(raw.get("open_questions")),
    }


def parse_json_array(text: str) -> List[Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in model output")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("model output is not a list")
    return data


def chunk_sections(text: str, limit: int) -> List[Dict[str, str]]:
    """Split on headings, then pack sections into chunks of at most `limit` chars."""
    sections: List[Dict[str, str]] = []
    heading, buf = "General", []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append({"heading": heading, "text": body})

    for line in text.splitlines():
        s = line.strip()
        is_head = bool(re.match(r"^#{1,6}\s+\S", s)) or bool(re.match(r"^\d+(\.\d+)*[.)]?\s+[A-Z][^.!?]{2,80}$", s))
        if is_head:
            flush()
            heading, buf = s.lstrip("# ").strip(), []
        else:
            buf.append(line)
    flush()
    chunks: List[Dict[str, str]] = []
    for sec in sections:
        body = sec["text"]
        while len(body) > limit:
            cut = body.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append({"heading": sec["heading"], "text": body[:cut]})
            body = body[cut:].strip()
        if body:
            chunks.append({"heading": sec["heading"], "text": body})
    return chunks


def mock_stories(chunk: Dict[str, str], source: str) -> List[Dict[str, Any]]:
    """Offline heuristic: one story per requirement-like sentence."""
    out = []
    body = re.sub(r"\s+", " ", chunk["text"])
    for sent in re.split(r"(?<=[.!?;])\s+|\s[•\-]\s", body):
        sent = sent.strip(" -•\t")
        if len(sent) < 25 or not _REQ.search(sent):
            continue
        low = sent.lower()
        persona = next((p for p, kws in _ACTOR if any(k in low for k in kws)), "user")
        goal = re.sub(r"^(the )?(system|solution|application|platform)\s+(shall|must|should|will)\s+", "", sent, flags=re.I)
        goal = goal.rstrip(".").strip()
        goal = goal[0].lower() + goal[1:] if goal else goal
        tags = taxonomy.match_compliance(sent)
        ac = ["Given the requirement context, when the capability is used, then it behaves as described: " + goal[:160]]
        ac += [taxonomy.COMPLIANCE_AC[t] for t in tags]
        out.append({
            "epic": chunk["heading"], "title": goal[:90].capitalize(), "persona": persona,
            "narrative": "As a %s, I want %s, so that the business requirement is satisfied." % (persona, goal),
            "acceptance_criteria": ac, "priority": "High" if re.search(r"\bmust|shall\b", low) else "Medium",
            "labels": [], "product_scope": taxonomy.match_classes(sent), "variant_criteria": [],
            "compliance_tags": tags, "source_ref": "%s > %s" % (source, chunk["heading"]), "open_questions": [],
        })
    return out


def _user_prompt(chunk: Dict[str, str], source: str, platform: str, related: List[dict]) -> str:
    profile = PLATFORMS.get(platform, PLATFORMS["generic"])
    classes = "; ".join("%s (%s)" % (c["name"], ", ".join(c["attributes"])) for c in taxonomy.PRODUCT_CLASSES)
    parts = [
        "Platform: %s. %s" % (profile["label"], profile["guidance"]),
        "Product classes and behaviour attributes: " + classes,
        "Compliance tags to consider: " + ", ".join(taxonomy.COMPLIANCE_KEYWORDS),
    ]
    if related:
        parts.append("Related context from other project documents (for consistency only; do not "
                     "create stories from it):\n" + "\n---\n".join(r["text"][:600] for r in related))
    parts.append("Document: %s\nSection: %s\n\nBRD text:\n%s" % (source, chunk["heading"], chunk["text"]))
    return "\n\n".join(parts)


def generate(
    documents: List[Dict[str, Any]], project_id: str, platform: str, provider: str, model: str,
    progress: Callable[[str], None] = lambda _m: None,
) -> List[Dict[str, Any]]:
    work = []
    for doc in documents:
        for ch in chunk_sections(doc["text"], config.CHUNK_CHARS):
            work.append((doc, ch))
    if len(work) > config.MAX_CHUNKS_PER_RUN:
        raise ValueError("Documents too large: %d sections exceeds limit of %d" % (len(work), config.MAX_CHUNKS_PER_RUN))
    stories: List[Dict[str, Any]] = []
    seen = set()
    for i, (doc, ch) in enumerate(work, 1):
        progress("Section %d of %d" % (i, len(work)))
        source = doc["filename"]
        if provider == "mock":
            raw = mock_stories(ch, source)
        else:
            related: List[dict] = []
            if config.RAG_TOP_K > 0:
                related = embeddings.search(project_id, ch["text"][:1000], config.RAG_TOP_K, doc["id"])
            reply = llm.complete(SYSTEM_PROMPT, llm.redact(_user_prompt(ch, source, platform, related)), provider, model)
            try:
                raw = parse_json_array(reply)
            except (ValueError, json.JSONDecodeError):
                reply = llm.complete(SYSTEM_PROMPT, "Return ONLY a valid JSON array for this input.\n\n"
                                     + llm.redact(_user_prompt(ch, source, platform, related)), provider, model)
                raw = parse_json_array(reply)
        for r in raw:
            s = normalize_story(r, "%s > %s" % (source, ch["heading"]))
            if not s:
                continue
            key = re.sub(r"\W+", " ", s["title"].lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            stories.append(s)
    return stories
