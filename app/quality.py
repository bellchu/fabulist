"""Deterministic quality checks.

Requirements follow ISO/IEC/IEEE 29148 characteristics (unambiguous, singular,
verifiable, complete); stories follow INVEST plus a Definition of Ready.
"""
import math
import re
from typing import Any, Dict, List

Issue = Dict[str, str]

VAGUE = ["user-friendly", "user friendly", "easy", "easily", "simple", "fast", "quickly", "efficient",
         "flexible", "robust", "seamless", "seamlessly", "intuitive", "appropriate", "adequate", "as needed",
         "as required", "if possible", "where possible", "etc", "and so on", "and/or", "normally", "generally",
         "approximately", "various", "several", "sufficient", "minimal", "state-of-the-art", "best practice"]
WEAK = ["should", "may", "might", "could", "possibly", "ideally"]
TBD = ["tbd", "tbc", "to be determined", "to be confirmed", "???"]
_VAGUE_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(v) for v in VAGUE) + r")(?![\w-])", re.I)
_WEAK_RE = re.compile(r"\b(" + "|".join(WEAK) + r")\b", re.I)
_TBD_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in TBD) + r")(?!\w)", re.I)
_MEASURABLE = re.compile(r"\b(perform\w*|response|respond\w*|latency|fast\w*|quick\w*|slow\w*|speed|second\w*|availab\w*|uptime|"
                         r"scal\w*|load|concurrent\w*|throughput|capacity|volume|user[- ]friendly|usab\w*|intuitive|easy|easily|"
                         r"responsive|efficient\w*|reliab\w*|downtime|recover\w*)\b", re.I)
_NARRATIVE = re.compile(r"^\s*as an?\s+.+?,?\s+i (want|need|can)\b.+?,?\s+so that\b.+", re.I | re.S)
_GWT = re.compile(r"^\s*(given|when|then|and|but)\b", re.I)


def _issue(code: str, severity: str, message: str) -> Issue:
    return {"code": code, "severity": severity, "message": message}


def vague_terms(text: str) -> List[str]:
    return sorted({m.group(1).lower() for m in _VAGUE_RE.finditer(text)})


def requirement_issues(req: Dict[str, Any]) -> List[Issue]:
    text = req.get("statement", "")
    out: List[Issue] = []
    if _TBD_RE.search(text) or _TBD_RE.search(req.get("notes") or ""):
        out.append(_issue("incomplete", "error", "Contains TBD/TBC: requirement is incomplete."))
    terms = vague_terms(text)
    if terms:
        out.append(_issue("ambiguous", "warning", "Vague terms: %s. Replace with measurable criteria." % ", ".join(terms)))
    if _WEAK_RE.search(text):
        out.append(_issue("weak-modal", "info", "Weak modal verb; confirm whether this is mandatory (shall/must)."))
    if len(re.findall(r"\b(and|or)\b", text, re.I)) >= 3 or len(re.findall(r"\b(shall|must|should|will)\b", text, re.I)) > 1:
        out.append(_issue("not-singular", "warning", "May combine several requirements; split into atomic statements."))
    # Only quality attributes that need a threshold (performance, availability, capacity, usability) must
    # carry a number; e.g. "changes are recorded in an audit trail" is verifiable as stated.
    if req.get("type") == "non-functional" and _MEASURABLE.search(text) and not re.search(r"\d", text):
        out.append(_issue("not-verifiable", "warning", "Non-functional requirement has no measurable threshold."))
    if len(text) > 450:
        out.append(_issue("too-long", "info", "Long statement; consider splitting."))
    return out


def story_issues(story: Dict[str, Any], valid_req_keys: Any = None) -> List[Issue]:
    out: List[Issue] = []
    ac = story.get("acceptance_criteria") or []
    if not _NARRATIVE.match(story.get("narrative") or ""):
        out.append(_issue("narrative", "error", "Narrative should read 'As a <role>, I want <goal>, so that <benefit>'."))
    if not ac:
        out.append(_issue("testable", "error", "No acceptance criteria: story is not testable."))
    else:
        non_gwt = sum(1 for a in ac if not _GWT.match(a))
        if non_gwt:
            out.append(_issue("gherkin", "info", "%d acceptance criteria not in Given/When/Then form." % non_gwt))
    if len(ac) > 8 or len(re.findall(r"\band\b", story.get("title") or "", re.I)) >= 2:
        out.append(_issue("small", "warning", "Story may be too large; consider splitting."))
    if (story.get("persona") or "").strip().lower() in ("", "user", "system"):
        out.append(_issue("valuable", "info", "Generic persona; name the specific role that gets value."))
    links = story.get("requirement_ids") or []
    if not links:
        out.append(_issue("trace", "warning", "Not linked to any requirement (traceability gap)."))
    elif valid_req_keys is not None and any(k not in valid_req_keys for k in links):
        out.append(_issue("trace", "warning", "Links to requirements that no longer exist."))
    terms = vague_terms(" ".join([story.get("narrative") or ""] + list(ac)))
    if terms:
        out.append(_issue("ambiguous", "warning", "Vague terms: %s." % ", ".join(terms)))
    if story.get("open_questions"):
        out.append(_issue("open-questions", "warning", "Open questions must be resolved before the story is ready."))
    return out


def score(issues: List[Issue]) -> int:
    weights = {"error": 30, "warning": 10, "info": 3}
    return max(0, 100 - sum(weights.get(i["severity"], 0) for i in issues))


def is_ready(issues: List[Issue]) -> bool:
    """Definition of Ready: no errors and no unresolved questions or trace gaps."""
    return not any(i["severity"] == "error" or i["code"] in ("open-questions", "trace") for i in issues)


_STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "by", "with", "be", "is", "are", "shall", "must",
         "should", "will", "system", "able", "allow", "that", "from", "as", "at", "it", "its", "their", "each", "all"}


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1}


def duplicate_pairs(reqs: List[Dict[str, Any]], threshold: float = 0.85) -> Dict[str, List[str]]:
    """Map requirement key -> keys of near-duplicates (token Jaccard >= threshold), exactly.

    Prefix filtering (AllPairs): with tokens ordered rare-first, two sets whose Jaccard is at least t must share a token
    among the first n - ceil(t*n) + 1 of each, so only requirements sharing one of those few rare tokens are compared, and
    a length filter drops partners that are too short. The old shortcut (ignore tokens used by more than 10% of the
    requirements) was fast but, on a repetitive BRD vocabulary, left nothing to compare and found no duplicates at all."""
    items = [(r["key"], _tokens(r.get("statement", ""))) for r in reqs]
    items = [(k, t) for k, t in items if len(t) >= 3]
    freq: Dict[str, int] = {}
    for _, ts in items:
        for tok in ts:
            freq[tok] = freq.get(tok, 0) + 1
    items.sort(key=lambda kt: len(kt[1]))  # shorter first: every partner already indexed is no longer than the current one
    index: Dict[str, List[int]] = {}
    out: Dict[str, List[str]] = {}
    for i, (key, ts) in enumerate(items):
        ordered = sorted(ts, key=lambda tok: (freq[tok], tok))
        need = math.ceil(threshold * len(ordered))  # a partner shorter than this cannot reach the threshold
        prefix = ordered[: len(ordered) - need + 1]
        seen = set()
        for tok in prefix:
            for j in index.get(tok, ()):
                if j in seen:
                    continue
                seen.add(j)
                other_key, other = items[j]
                if len(other) >= need and len(ts & other) / len(ts | other) >= threshold:
                    out.setdefault(key, []).append(other_key)
                    out.setdefault(other_key, []).append(key)
        for tok in prefix:
            index.setdefault(tok, []).append(i)
    return {k: sorted(v) for k, v in out.items()}
