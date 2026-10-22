"""Two-stage generation: BRD text -> atomic requirements -> user stories.

Stage 1 extracts requirements with stable IDs for analyst review. Stage 2 turns
reviewed requirements into epics/features/stories linked back to those IDs.
"""
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import embeddings, llm, quality, taxonomy
from .profiles import PLATFORMS

REQ_TYPES = ["business", "functional", "non-functional", "constraint", "rule"]
PRIORITIES = ["High", "Medium", "Low"]
POINTS = [1, 2, 3, 5, 8, 13]
_REQ = re.compile(r"\b(shall|must|should|needs? to|requires?|required to|will be able|support|allow|enable|provide)\b", re.I)
_ACTOR = [
    ("sales representative", ["sales rep", "sales representative", "account manager"]),
    ("customer", ["customer", "pharmacy", "hospital", "clinic", "buyer"]),
    ("warehouse operator", ["warehouse", "picker", "receiving", "shipping", "distribution center"]),
    ("finance analyst", ["finance", "accounting", "billing", "invoice", "chargeback", "rebate"]),
    ("compliance officer", ["compliance", "regulatory", "audit", "quality"]),
    ("administrator", ["admin", "administrator", "configure"]),
    ("customer service agent", ["service agent", "support agent", "case", "call center"]),
    ("buyer", ["procurement", "purchasing", "supplier", "vendor"]),
]
_NFR = re.compile(r"\b(performance|response time|within \d+\s*(ms|milliseconds?|seconds?)|uptime|availability|scalab|concurrent|"
                  r"secur|encrypt|backup|recover|latency|throughput|accessib|wcag|retention|audit trail|log)\w*", re.I)
_CONSTRAINT = re.compile(r"\b(comply|compliance with|regulat|must use|integrate with|limited to|constraint|legacy)\w*", re.I)
_BUSINESS = re.compile(r"\b(business|revenue|margin|cost|goal|objective|kpi|reduce|increase|improve)\b", re.I)

Progress = Callable[[str], None]
Usage = Dict[str, int]

REQ_SYSTEM = (
    "You are a senior business analyst. Extract atomic requirements from a business requirements document "
    "(BRD) section for enterprise application delivery in medical product distribution. Reply with ONLY a JSON "
    "array. Each element: {\"statement\": str, \"type\": \"business|functional|non-functional|constraint|rule\", "
    "\"actors\": [str], \"source_id\": str (requirement ID used in the BRD, or empty), \"rationale\": str}. "
    "Rules: one verifiable requirement per element (split compound statements); keep the BRD's meaning and "
    "terminology; never invent requirements; skip background, narrative and table-of-contents text; prefer "
    "'The system shall ...' phrasing for functional requirements. Keep open-item markers such as TBD, TBC or "
    "'to be confirmed' verbatim in the statement; never resolve or drop them."
)
STORY_SYSTEM = (
    "You are a senior business analyst writing an agile backlog from approved requirements. Reply with ONLY a "
    "JSON array of user stories. Each element: {\"epic\": str, \"feature\": str, \"title\": str (short, verb-first), "
    "\"persona\": str (specific role), \"narrative\": \"As a <persona>, I want <goal>, so that <benefit>\", "
    "\"acceptance_criteria\": [Gherkin strings starting with Given/When/Then], \"priority\": \"High|Medium|Low\", "
    "\"story_points\": 1|2|3|5|8|13|null, \"labels\": [str], \"requirement_ids\": [requirement IDs covered], "
    "\"product_scope\": [product classes affected, empty = all], \"variant_criteria\": [{\"scope\": product class "
    "or attribute, \"criteria\": str}], \"compliance_tags\": [str], \"open_questions\": [str]}. Rules: follow INVEST "
    "(independent, valuable, small, testable); every requirement ID must be covered by at least one story; fold "
    "non-functional requirements and business rules into the acceptance criteria of the stories they constrain, "
    "or create an enabler story when they stand alone; express product-specific differences as variant_criteria "
    "instead of duplicate stories; record ambiguities as open_questions instead of guessing."
)


# ---- helpers -------------------------------------------------------------

def _clean_list(v: Any) -> List[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def _norm_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def parse_json_array(text: str) -> List[Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in model output")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("model output is not a list")
    return data


def chunk_sections(text: str, limit: int) -> List[Dict[str, str]]:
    """Split on headings (tracking the top-level heading as epic), then pack to `limit` chars."""
    sections: List[Dict[str, str]] = []
    stack: List[Tuple[int, str]] = []
    buf: List[str] = []

    def current() -> Tuple[str, str]:
        if not stack:
            return "General", "General"
        return stack[0][1], stack[-1][1]

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            epic, heading = current()
            sections.append({"epic": epic, "heading": heading, "text": body})

    for line in text.splitlines():
        s = line.strip()
        md = re.match(r"^(#{1,6})\s+(\S.*)$", s)
        num = re.match(r"^(\d+(?:\.\d+)*)[.)]?\s+([A-Z][^.!?]{2,80})$", s)
        if md or num:
            flush()
            buf = []
            level = len(md.group(1)) if md else num.group(1).count(".") + 1
            title = (md.group(2) if md else num.group(2)).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    chunks: List[Dict[str, str]] = []
    for sec in sections:
        body = sec["text"]
        while len(body) > limit:
            cut = body.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(dict(sec, text=body[:cut]))
            body = body[cut:].strip()
        if body:
            chunks.append(dict(sec, text=body))
    return chunks


def classify(statement: str) -> str:
    if _NFR.search(statement):
        return "non-functional"
    if _CONSTRAINT.search(statement):
        return "constraint"
    if _BUSINESS.search(statement) and not re.search(r"\b(system|platform|application|solution)\b", statement, re.I):
        return "business"
    if re.search(r"\b(if|unless|only if|cannot|may not|not allowed|before|within \d+\s*(hours?|days?|weeks?))\b", statement, re.I):
        return "rule"
    return "functional"


def detect_persona(text: str) -> str:
    low = text.lower()
    return next((p for p, kws in _ACTOR if any(k in low for k in kws)), "user")


def _enrich(item: Dict[str, Any], text: str) -> None:
    item["product_scope"] = sorted(set(item.get("product_scope") or []) | set(taxonomy.match_classes(text)))
    item["compliance_tags"] = sorted(set(item.get("compliance_tags") or []) | set(taxonomy.match_compliance(text)))


def normalize_requirement(raw: Any, section: Dict[str, str], source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    statement = re.sub(r"\s+", " ", str(raw.get("statement") or "")).strip()
    if len(statement) < 8:
        return None
    rtype = str(raw.get("type") or "").strip().lower()
    req = {
        "statement": statement[:1500],
        "type": rtype if rtype in REQ_TYPES else classify(statement),
        "actors": _clean_list(raw.get("actors"))[:6],
        "source_id": str(raw.get("source_id") or "").strip()[:40],
        "rationale": str(raw.get("rationale") or "").strip()[:500],
        "epic": str(raw.get("epic") or section.get("epic") or "General").strip()[:200],
        "section": str(raw.get("section") or section.get("heading") or "General").strip()[:200],
        "source_ref": str(raw.get("source_ref") or "%s > %s" % (source, section.get("heading", ""))).strip()[:300],
        "product_scope": _clean_list(raw.get("product_scope")),
        "compliance_tags": _clean_list(raw.get("compliance_tags")),
        "notes": str(raw.get("notes") or "").strip()[:1000],
    }
    _enrich(req, statement)
    return req


def normalize_story(raw: Any, fallback: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    fallback = fallback or {}
    prio = str(raw.get("priority") or "Medium").strip().capitalize()
    try:
        points: Optional[int] = int(raw.get("story_points")) if raw.get("story_points") not in (None, "") else None
    except (TypeError, ValueError):
        points = None
    variants = [{"scope": str(v.get("scope") or "").strip(), "criteria": str(v["criteria"]).strip()}
                for v in raw.get("variant_criteria") or [] if isinstance(v, dict) and v.get("criteria")]
    return {
        "epic": str(raw.get("epic") or fallback.get("epic") or "General").strip()[:200],
        "feature": str(raw.get("feature") or fallback.get("feature") or "General").strip()[:200],
        "title": title[:300],
        "persona": str(raw.get("persona") or "user").strip()[:100],
        "narrative": str(raw.get("narrative") or "").strip()[:2000],
        "acceptance_criteria": _clean_list(raw.get("acceptance_criteria"))[:30],
        "priority": prio if prio in PRIORITIES else "Medium",
        "story_points": points if points in POINTS else None,
        "labels": _clean_list(raw.get("labels"))[:20],
        "requirement_ids": _clean_list(raw.get("requirement_ids"))[:50],
        "product_scope": _clean_list(raw.get("product_scope")),
        "variant_criteria": variants[:20],
        "compliance_tags": _clean_list(raw.get("compliance_tags")),
        "open_questions": _clean_list(raw.get("open_questions"))[:20],
        "notes": str(raw.get("notes") or "").strip()[:2000],
    }


_usage_lock = threading.Lock()


def _count(usage: Usage, u: Dict[str, int]) -> None:
    with _usage_lock:  # sections are asked in parallel: += on a shared dict is not atomic
        usage["in"] += u["in"]
        usage["out"] += u["out"]
        usage["requests"] = usage.get("requests", 0) + 1


def _in_order(count: int, work: Callable[[int], Any], workers: int, progress: Progress, describe: Callable[[int], str]) -> List[Any]:
    """[work(i) for i in range(count)], with up to `workers` calls in flight, results in order. `progress(describe(i))` is called here, in
    the calling thread, as each result is awaited in order, so it is still the run's heartbeat and its cancellation checkpoint. The first
    failure (in order) or a cancel drops the work not yet started and is raised."""
    if workers <= 1 or count <= 1:
        results = []
        for i in range(count):
            progress(describe(i))
            results.append(work(i))
        return results
    cancel = llm.get_cancel_check()

    def task(i: int) -> Any:
        llm.set_cancel_check(cancel)  # the cancel hook is per thread; also what marks this as a background call
        return work(i)
    pool = ThreadPoolExecutor(max_workers=min(workers, count), thread_name_prefix="ai")
    futures = [pool.submit(task, i) for i in range(count)]
    try:
        results = []
        for i, future in enumerate(futures):
            progress(describe(i))
            results.append(future.result())
        return results
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True)  # calls already in flight finish (or notice the cancel); nothing new starts


JSON_ATTEMPTS = 3  # replies that are not a JSON array are asked for again (twice) before giving up: a run makes hundreds of requests


def _ask(profile: Dict[str, Any], opts: Dict[str, Any], system: str, prompt: str, usage: Usage) -> List[Any]:
    if opts.get("redact_pii", True):
        prompt = llm.redact(prompt)
    ask = prompt
    for attempt in range(JSON_ATTEMPTS):
        text, u = llm.complete(profile, system, ask, opts)
        _count(usage, u)
        try:
            return parse_json_array(text)
        except (ValueError, json.JSONDecodeError):
            ask = "Your previous reply was not valid JSON. Return ONLY the JSON array for this input.\n\n" + prompt
    raise ValueError("The AI provider did not return valid JSON after %d tries. Try a stronger model, a smaller section size, "
                     "or a lower temperature." % JSON_ATTEMPTS)


def _ask_at(where: str, profile: Dict[str, Any], opts: Dict[str, Any], system: str, prompt: str, usage: Usage) -> List[Any]:
    """_ask, but a failure says which part of the work it happened in and that the run changed nothing."""
    try:
        return _ask(profile, opts, system, prompt, usage)
    except (llm.LLMError, ValueError) as exc:
        raise type(exc)("%s: %s. Nothing was saved; existing results were kept." % (where, str(exc).rstrip(".")))


def _platform_context(platform: str) -> str:
    profile = PLATFORMS.get(platform, PLATFORMS["generic"])
    classes = "; ".join("%s (%s)" % (c["name"], ", ".join(c["attributes"])) for c in taxonomy.classes())
    return "\n".join([
        "Target platform: %s. %s" % (profile["label"], profile["guidance"]),
        "Product classes and behaviour attributes: " + classes,
        "Compliance tags: " + ", ".join(taxonomy.COMPLIANCE_KEYWORDS),
    ])


# ---- stage 1: requirements -----------------------------------------------

def mock_requirements(section: Dict[str, str]) -> List[Dict[str, Any]]:
    """Offline heuristic: one requirement per requirement-like sentence."""
    out = []
    # Wrapped lines (common in PDFs) are joined, but a line starting with a requirement ID or a
    # bullet always begins a new item (spreadsheet rows, numbered lists).
    marked = re.sub(r"\n(?=\s*(?:[•\-\*]\s|\(?[A-Z]{1,5}[-_ ]?\d{1,4}\b))", "\x1e", section["text"])
    body = re.sub(r"[ \t\r\n]+", " ", marked)
    for sent in re.split(r"(?<=[.!?;])\s+|\s[•\-\*]\s|\s*\x1e\s*", body):
        sent = sent.strip(" -•*\t")
        if len(sent) < 25 or not _REQ.search(sent):
            continue
        sid = re.match(r"^\(?([A-Z]{1,5}[-_ ]?\d{1,4}(?:\.\d+)*)\)?[:.\s-]+", sent)
        if sid:
            sent = sent[sid.end():].strip()
        out.append({"statement": sent, "actors": [detect_persona(sent)], "source_id": sid.group(1) if sid else ""})
    return out


def extract_requirements(documents: List[Dict[str, Any]], platform: str, profile: Dict[str, Any],
                         opts: Dict[str, Any], progress: Progress, usage: Usage,
                         existing: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Requirements from `documents`. With `existing` (requirements already kept), a statement they already contain is not added again:
    its source is recorded on the existing requirement instead (the dict in `existing` is updated in place), and only new ones are returned."""
    work = [(d, ch) for d in documents for ch in chunk_sections(d["text"], int(opts["chunk_chars"]))]
    if len(work) > int(opts["max_chunks_per_run"]):
        raise ValueError("Documents produce %d sections, above the limit of %d. Raise 'Max sections per run' in Admin > Generation settings or split the documents."
                         % (len(work), opts["max_chunks_per_run"]))
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {_norm_key(e["statement"]): e for e in existing or [] if e.get("statement")}
    context = _platform_context(platform)

    def read(i: int) -> List[Any]:
        doc, sec = work[i]
        if profile["kind"] == "mock":
            return mock_requirements(sec)
        prompt = "%s\n\nDocument: %s\nSection: %s > %s\n\nBRD text:\n%s" % (context, doc["filename"], sec["epic"], sec["heading"], sec["text"])
        return _ask_at("Section %d of %d (%s)" % (i + 1, len(work), sec["heading"][:60]), profile, opts, REQ_SYSTEM, prompt, usage)
    workers = 1 if profile["kind"] == "mock" else int(opts.get("ai_concurrency", 1))
    answers = _in_order(len(work), read, workers, progress, lambda i: "Reading section %d of %d: %s" % (i + 1, len(work), work[i][1]["heading"][:60]))
    for (doc, sec), raw in zip(work, answers):
        for r in raw:
            req = normalize_requirement(r, sec, doc["filename"])
            if not req:
                continue
            first = seen.get(_norm_key(req["statement"]))
            if first is None:
                seen[_norm_key(req["statement"])] = req
                out.append(req)
            else:
                _add_source(first, req)
    return out


def _add_source(kept: Dict[str, Any], repeat: Dict[str, Any]) -> None:
    """The same requirement stated again (in another BRD, or under another BRD id): keep one requirement, but not at the cost
    of forgetting where else it came from."""
    also = repeat["source_ref"] + (" (%s)" % repeat["source_id"] if repeat.get("source_id") else "")
    if "; also " + also not in kept["source_ref"]:
        kept["source_ref"] = (kept["source_ref"] + "; also " + also)[:300]


# ---- stage 2: stories ----------------------------------------------------

def _benefit(req: Dict[str, Any]) -> str:
    tags = req.get("compliance_tags") or []
    if tags:
        return "the business stays compliant with %s obligations" % "/".join(tags)
    section = (req.get("section") or "").strip()
    if not section or section == "General":
        return "the business requirement %s is met" % req.get("key", "")
    return "%s objectives in '%s' are met" % (req.get("epic") or "business", section)


def _short_title(text: str, limit: int = 80) -> str:
    """Sentence-case title cut at a word boundary (keeps acronyms like GPO intact)."""
    text = text.strip().rstrip(".")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text[:1].upper() + text[1:]


def mock_stories(reqs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Offline heuristic: a story per functional/business requirement; NFRs, rules and
    constraints become acceptance criteria on stories in the same section."""
    stories: List[Dict[str, Any]] = []
    by_section: Dict[str, List[Dict[str, Any]]] = {}
    for r in reqs:
        if r["type"] in ("functional", "business"):
            persona = (r.get("actors") or [detect_persona(r["statement"])])[0]
            goal = re.sub(r"^(the )?(system|solution|application|platform)\s+(shall|must|should|will)\s+(be able to\s+)?",
                          "", r["statement"], flags=re.I).rstrip(".")
            # "allow a sales representative to create X" -> "create X" (the persona is already the subject)
            goal = re.sub(r"^(allow|enable|permit)s?\s+(an?\s+|the\s+)?[\w\s-]{2,40}?\s+to\s+", "", goal, flags=re.I)
            goal = goal[0].lower() + goal[1:] if goal else goal
            ac = ["Given I am signed in as a %s, when I %s, then the result is saved and confirmed to me." % (persona, goal[:140]),
                  "Given invalid or incomplete input, when I submit, then I see which fields need correction and nothing is saved."]
            ac += ["Then " + taxonomy.COMPLIANCE_AC[t] for t in r.get("compliance_tags", []) if t in taxonomy.COMPLIANCE_AC]
            story = {
                "epic": r.get("epic"), "feature": r.get("section"), "title": _short_title(goal),
                "persona": persona, "narrative": "As a %s, I want to %s, so that %s." % (persona, goal, _benefit(r)),
                "acceptance_criteria": ac, "priority": "High" if re.search(r"\b(must|shall)\b", r["statement"], re.I) else "Medium",
                "requirement_ids": [r["key"]], "product_scope": r.get("product_scope", []),
                "variant_criteria": taxonomy.variant_criteria(r.get("product_scope", [])),
                "compliance_tags": r.get("compliance_tags", []), "labels": [r["type"]],
                "open_questions": ["Clarify wording: %s" % i["message"] for i in r.get("issues", []) if i["code"] in ("ambiguous", "incomplete")],
            }
            stories.append(story)
            by_section.setdefault(r.get("section", ""), []).append(story)
    for r in reqs:
        if r["type"] in ("functional", "business"):
            continue
        targets = by_section.get(r.get("section", ""))
        if targets:
            for s in targets:
                s["acceptance_criteria"].append("Then %s" % r["statement"].rstrip(".")[:300] + ".")
                s["requirement_ids"].append(r["key"])
        else:
            stories.append({
                "epic": r.get("epic"), "feature": r.get("section"), "title": _short_title("Enabler: " + r["statement"]),
                "persona": "platform team", "narrative": "As a platform team, I want to satisfy the %s requirement '%s', so that %s."
                % (r["type"], r["statement"].rstrip(".")[:200], _benefit(r)),
                "acceptance_criteria": ["Given the solution is deployed, when it is verified, then %s" % r["statement"][:300]],
                "priority": "Medium", "requirement_ids": [r["key"]], "labels": ["enabler", r["type"]],
                "compliance_tags": r.get("compliance_tags", []), "product_scope": r.get("product_scope", []),
            })
    return stories


def story_batches(reqs: List[Dict[str, Any]]) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
    """The AI requests story generation makes: requirements grouped by feature area, at most 25 per request."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in reqs:
        groups.setdefault((r.get("epic", ""), r.get("section", "")), []).append(r)
    return [(epic, section, items[i:i + 25]) for (epic, section), items in groups.items() for i in range(0, len(items), 25)]


def generate_stories(reqs: List[Dict[str, Any]], project_id: str, platform: str, profile: Dict[str, Any],
                     opts: Dict[str, Any], progress: Progress, usage: Usage, taken: Optional[set] = None) -> List[Dict[str, Any]]:
    if profile["kind"] == "mock":
        progress("Drafting stories for %d requirements" % len(reqs))
        raw = mock_stories(reqs)
        return _finalize(raw, reqs, taken)
    batches = story_batches(reqs)
    if len(batches) > int(opts["max_chunks_per_run"]):
        raise ValueError("Too many requirement groups (%d) for one run (limit %d). Raise 'Max sections per run' in Admin > Generation "
                         "settings, or generate from approved requirements only." % (len(batches), int(opts["max_chunks_per_run"])))
    raw: List[Dict[str, Any]] = []
    platform_context = _platform_context(platform)

    def write(i: int) -> List[Any]:
        epic, section, items = batches[i]
        lines = "\n".join("%s [%s] %s" % (r["key"], r["type"], r["statement"]) for r in items)
        related = []
        if int(opts.get("rag_top_k", 0)) > 0:
            related = embeddings.search(project_id, " ".join(r["statement"] for r in items)[:1500], int(opts["rag_top_k"]))
        context = ("\n\nRelated BRD context (for consistency only):\n" + "\n---\n".join(h["text"][:600] for h in related)) if related else ""
        prompt = "%s%s\n\nEpic: %s\nFeature area: %s\n\nRequirements:\n%s" % (platform_context, context, epic, section, lines)
        return _ask_at("Story group %d of %d (%s)" % (i + 1, len(batches), section[:60]), profile, opts, STORY_SYSTEM, prompt, usage)
    answers = _in_order(len(batches), write, int(opts.get("ai_concurrency", 1)), progress,
                        lambda i: "Writing stories %d of %d: %s" % (i + 1, len(batches), batches[i][1][:60]))
    for (epic, section, _items), stories in zip(batches, answers):
        for s in stories:
            if isinstance(s, dict):
                s.setdefault("epic", epic)
                s.setdefault("feature", section)
                raw.append(s)
    return _finalize(raw, reqs, taken)


def _finalize(raw: List[Dict[str, Any]], reqs: List[Dict[str, Any]], taken: Optional[set] = None) -> List[Dict[str, Any]]:
    """Normalise the stories from every batch. Titles are not unique across a large run (an AI writes 'Manage users' in many
    feature areas), and dropping the later story also dropped its requirement links, silently uncovering those requirements:
    - same title in the same feature area: one story twice; merge it (links and any new criteria are kept);
    - same title in another feature area: a different story; keep it, with the feature in the title to tell them apart.
    `taken` holds (title, feature) of stories that already exist when stories are being added to a backlog: a new story never takes the title
    of one of those (it gets the feature, and a number if that is taken too), and an existing story is never merged into or changed."""
    valid = {r["key"] for r in reqs}
    out: List[Dict[str, Any]] = []
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    existing_titles = {t for t, _ in (taken or set())}
    used = set(existing_titles)
    for r in raw:
        s = normalize_story(r)
        if not s:
            continue
        s["requirement_ids"] = [k for k in dict.fromkeys(s["requirement_ids"]) if k in valid]
        key, feature = _norm_key(s["title"]), _norm_key(s.get("feature") or "")
        twin = next((o for o in by_title.get(key, []) if _norm_key(o.get("feature") or "") == feature), None)
        if twin:
            twin["requirement_ids"] = list(dict.fromkeys(twin["requirement_ids"] + s["requirement_ids"]))
            twin["acceptance_criteria"] += [a for a in s["acceptance_criteria"] if a not in twin["acceptance_criteria"]][:max(0, 12 - len(twin["acceptance_criteria"]))]
            continue
        _enrich(s, s["title"] + " " + s["narrative"])
        if by_title.get(key) or key in existing_titles:
            s["title"] = ("%s (%s)" % (s["title"], s.get("feature") or "other"))[:200]
            n = 2
            while _norm_key(s["title"]) in used:  # still taken: number it
                s["title"] = ("%s (%s) %d" % (s["title"].split(" (")[0], s.get("feature") or "other", n))[:200]
                n += 1
        used.add(_norm_key(s["title"]))
        by_title.setdefault(key, []).append(s)
        out.append(s)
    return out


# ---- single-story refinement ---------------------------------------------

REFINE_SYSTEM = (
    "You are a senior business analyst improving one agile user story. Fix the listed quality issues while "
    "keeping the story's scope, epic, feature and requirement links. Follow INVEST, write the narrative as "
    "'As a <specific role>, I want <goal>, so that <benefit>', write every acceptance criterion in Given/When/Then "
    "form with concrete, testable outcomes, and replace vague words with measurable criteria taken from the "
    "requirements. Never invent requirement IDs. Keep unresolved ambiguities in open_questions. Reply with ONLY a "
    "JSON array containing exactly one story object with the same fields as the input."
)


def _gherkin(line: str) -> str:
    line = line.strip()
    if re.match(r"^(given|when|then|and|but)\b", line, re.I):
        return line
    return "Then " + line[0].lower() + line[1:] if line else line


def mock_refine(story: Dict[str, Any], reqs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Offline heuristic fixes for structural issues; content issues are left to humans."""
    out = dict(story)
    actors = [a for r in reqs for a in (r.get("actors") or []) if a and a != "user"]
    if (out.get("persona") or "user").lower() in ("", "user", "system") and actors:
        out["persona"] = actors[0]
    if not re.match(r"^\s*as an?\s+.+?i (want|need|can)\b.+?so that\b", out.get("narrative") or "", re.I | re.S):
        goal = (out.get("title") or "").rstrip(".…")
        goal = goal[:1].lower() + goal[1:]
        benefit = _benefit(reqs[0]) if reqs else "the business requirement is met"
        out["narrative"] = "As a %s, I want to %s, so that %s." % (out.get("persona") or "user", goal, benefit)
    out["acceptance_criteria"] = [_gherkin(a) for a in out.get("acceptance_criteria") or []]
    if not out["acceptance_criteria"] and reqs:
        out["acceptance_criteria"] = ["Given the feature is available, when it is used, then %s" % reqs[0]["statement"].rstrip(".")[:200] + "."]
    return out


def refine_story(story: Dict[str, Any], reqs: List[Dict[str, Any]], issues: List[Dict[str, str]], platform: str,
                 profile: Dict[str, Any], opts: Dict[str, Any], usage: Usage) -> Dict[str, Any]:
    fields = {k: story.get(k) for k in ("epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority",
                                        "story_points", "labels", "requirement_ids", "product_scope", "variant_criteria",
                                        "compliance_tags", "open_questions", "notes")}
    if profile["kind"] == "mock":
        raw = mock_refine(fields, reqs)
    else:
        prompt = "%s\n\nQuality issues to fix:\n%s\n\nLinked requirements:\n%s\n\nStory:\n%s" % (
            _platform_context(platform), "\n".join("- " + i["message"] for i in issues) or "- none (polish wording only)",
            "\n".join("%s [%s] %s" % (r["key"], r["type"], r["statement"]) for r in reqs) or "none", json.dumps(fields, indent=1))
        result = _ask(profile, opts, REFINE_SYSTEM, prompt, usage)
        raw = result[0] if result and isinstance(result[0], dict) else {}
    refined = normalize_story(dict(fields, **{k: v for k, v in raw.items() if v not in (None, "")}))
    if refined is None:
        raise ValueError("The AI provider returned an unusable story")
    allowed = set(story.get("requirement_ids") or []) | {r["key"] for r in reqs}
    refined["requirement_ids"] = [k for k in refined["requirement_ids"] if k in allowed] or list(story.get("requirement_ids") or [])
    return refined


# ---- single-requirement refinement ----------------------------------------

REQ_REFINE_SYSTEM = (
    "You are a senior business analyst improving one requirement to meet ISO/IEC/IEEE 29148: unambiguous, singular, "
    "verifiable, complete. If it combines several requirements, split it into separate items. Keep the original meaning "
    "and terminology; never invent numbers, thresholds or scope. When a measurable value is missing, keep the wording "
    "and put the question for the business in 'notes'. Use 'shall' for mandatory requirements. Reply with ONLY a JSON "
    "array of requirement objects: {\"statement\": str, \"type\": \"business|functional|non-functional|constraint|rule\", "
    "\"actors\": [str], \"notes\": str}."
)
_SPLIT = re.compile(r",?\s+and\s+(?=(?:the\s+|all\s+|each\s+)?[\w\s/-]{0,40}?\b(?:shall|must|should|will)\b)", re.I)


def mock_refine_requirement(req: Dict[str, Any], issues: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    parts = [p.strip().rstrip(".") for p in _SPLIT.split(req["statement"]) if p.strip()]
    out = []
    for part in parts:
        text = re.sub(r"\b(should|could|may)\b(?= be able to| support| allow| provide| display| record| validate)", "shall", part, flags=re.I)
        text = re.sub(r"\bshould\b", "shall", text, flags=re.I) if req.get("type") != "business" else text
        text = text[:1].upper() + text[1:] + "."
        notes = []
        vague = [i["message"] for i in quality.requirement_issues({"statement": text, "type": req.get("type")}) if i["code"] in ("ambiguous", "incomplete", "not-verifiable")]
        if vague:
            notes.append("Needs business input: " + " ".join(vague))
        out.append({"statement": text, "type": classify(text), "actors": req.get("actors") or [], "notes": " ".join(notes)})
    return out


def refine_requirement(req: Dict[str, Any], issues: List[Dict[str, str]], platform: str, profile: Dict[str, Any],
                       opts: Dict[str, Any], usage: Usage) -> List[Dict[str, Any]]:
    if profile["kind"] == "mock":
        raw = mock_refine_requirement(req, issues)
    else:
        prompt = "%s\n\nQuality issues:\n%s\n\nRequirement %s (%s, section '%s'):\n%s" % (
            _platform_context(platform), "\n".join("- " + i["message"] for i in issues) or "- none (polish only)",
            req.get("key", ""), req.get("type", ""), req.get("section", ""), req["statement"])
        raw = _ask(profile, opts, REQ_REFINE_SYSTEM, prompt, usage)[:10]
    section = {"epic": req.get("epic", ""), "heading": req.get("section", "")}
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        keep = {k: req.get(k) for k in ("epic", "section", "source_ref", "source_id", "product_scope", "compliance_tags")}
        item = normalize_requirement(dict(keep, **{k: v for k, v in r.items() if k in ("statement", "type", "actors", "notes")}),
                                     section, req.get("source_ref", ""))
        if item:
            out.append(item)
    if not out:
        raise ValueError("The AI provider returned no usable requirement")
    return out


# ---- question answering over BRDs (RAG) ----------------------------------

ASK_SYSTEM = (
    "You answer questions about business requirements documents. Use ONLY the numbered passages provided. "
    "Cite every claim with the passage number in square brackets, e.g. [2]. If the passages do not contain the "
    "answer, say that the BRDs do not cover it and suggest what to ask the business. Be concise."
)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 20]


def mock_answer(question: str, passages: List[Dict[str, Any]]) -> str:
    """Offline: quote the sentences that best overlap the question, with citations."""
    q = set(re.findall(r"[a-z0-9]{3,}", question.lower()))
    scored = []
    for n, p in enumerate(passages, 1):
        for sent in _sentences(p["text"]):
            overlap = len(q & set(re.findall(r"[a-z0-9]{3,}", sent.lower())))
            if overlap:
                scored.append((overlap, n, sent))
    if not scored:
        return "The BRDs don't appear to cover this. Consider confirming it with the business owner."
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0]
    # Keep only sentences nearly as relevant as the best match, so the answer stays on topic.
    keep = [x for x in scored if x[0] >= max(2, best * 0.7)] or scored[:1]
    return " ".join("%s [%d]" % (sent.rstrip("."), n) + "." for _, n, sent in keep[:3])


def answer_question(question: str, passages: List[Dict[str, Any]], profile: Dict[str, Any], opts: Dict[str, Any],
                    usage: Usage) -> str:
    if not passages:
        return "No indexed BRD passages yet. Add documents on the Sources tab first."
    if profile["kind"] == "mock":
        return mock_answer(question, passages)
    context = "\n\n".join("[%d] (%s)\n%s" % (n, p["filename"], p["text"][:1500]) for n, p in enumerate(passages, 1))
    prompt = "Passages:\n%s\n\nQuestion: %s" % (context, question)
    if opts.get("redact_pii", True):
        prompt = llm.redact(prompt)
    text, u = llm.complete(profile, ASK_SYSTEM, prompt, dict(opts, max_output_tokens=min(int(opts.get("max_output_tokens", 1500)), 1500)))
    usage["in"] += u["in"]
    usage["out"] += u["out"]
    return text.strip()


# ---- relative estimation --------------------------------------------------

ESTIMATE_SYSTEM = (
    "You are an experienced agile team estimating user stories in relative story points (1, 2, 3, 5, 8, 13). "
    "Consider complexity, uncertainty, integrations, compliance and data work; 13 means the story should be split. "
    "Reply with ONLY a JSON array: [{\"key\": story key, \"points\": 1|2|3|5|8|13, \"rationale\": short reason}]."
)


def mock_estimate(story: Dict[str, Any]) -> Tuple[int, str]:
    size = len(story.get("acceptance_criteria") or []) + 2 * len(story.get("variant_criteria") or [])
    size += 2 * len(story.get("compliance_tags") or []) + 3 * len(story.get("open_questions") or [])
    size += 2 if re.search(r"\b(integrat|interface|api|edi|sync|migrat)", (story.get("title", "") + story.get("narrative", "")).lower()) else 0
    points = next(p for p, limit in ((1, 1), (2, 3), (3, 5), (5, 8), (8, 12), (13, 10 ** 6)) if size <= limit)
    parts = ["%d acceptance criteria" % len(story.get("acceptance_criteria") or [])]
    if story.get("variant_criteria"):
        parts.append("%d product variants" % len(story["variant_criteria"]))
    if story.get("compliance_tags"):
        parts.append("compliance: " + ", ".join(story["compliance_tags"]))
    if story.get("open_questions"):
        parts.append("open questions add uncertainty")
    return points, "; ".join(parts)


def estimate_stories(stories: List[Dict[str, Any]], profile: Dict[str, Any], opts: Dict[str, Any],
                     usage: Usage) -> List[Dict[str, Any]]:
    if profile["kind"] == "mock":
        out = []
        for s in stories:
            pts, why = mock_estimate(s)
            out.append({"key": s["key"], "points": pts, "rationale": why})
        return out
    by_key = {s["key"]: s for s in stories}
    out: List[Dict[str, Any]] = []
    for i in range(0, len(stories), 30):
        batch = stories[i:i + 30]
        lines = "\n\n".join("%s: %s\n%s\nAcceptance criteria: %d; variants: %d; compliance: %s" % (
            s["key"], s["title"], s.get("narrative", ""), len(s.get("acceptance_criteria") or []),
            len(s.get("variant_criteria") or []), ", ".join(s.get("compliance_tags") or []) or "none") for s in batch)
        for r in _ask(profile, opts, ESTIMATE_SYSTEM, "Stories:\n\n" + lines, usage):
            if isinstance(r, dict) and r.get("key") in by_key:
                try:
                    pts = int(r.get("points"))
                except (TypeError, ValueError):
                    continue
                if pts in POINTS:
                    out.append({"key": r["key"], "points": pts, "rationale": str(r.get("rationale") or "")[:300]})
    return out
