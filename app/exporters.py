"""Story exporters. All consume the same canonical story dicts."""
import csv
import io
import json
from typing import Any, Callable, Dict, List, Tuple

from openpyxl import Workbook

Story = Dict[str, Any]
COLUMNS = ["id", "epic", "title", "persona", "narrative", "acceptance_criteria", "priority", "labels",
           "product_scope", "variant_criteria", "compliance_tags", "source_ref", "open_questions", "status"]


def _cell(story: Story, col: str) -> str:
    v = story.get(col, "")
    if col == "variant_criteria":
        return "\n".join("[%s] %s" % (x.get("scope", ""), x.get("criteria", "")) for x in v)
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return "" if v is None else str(v)


def _guard(value: str) -> str:
    """Neutralize spreadsheet formula injection in CSV/XLSX cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(stories: List[Story]) -> Tuple[bytes, str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for s in stories:
        w.writerow([_guard(_cell(s, c)) for c in COLUMNS])
    return buf.getvalue().encode("utf-8"), "text/csv"


def to_json(stories: List[Story]) -> Tuple[bytes, str]:
    return json.dumps(stories, indent=2).encode("utf-8"), "application/json"


def to_markdown(stories: List[Story]) -> Tuple[bytes, str]:
    lines = ["# User stories", ""]
    epic = None
    for s in stories:
        if s["epic"] != epic:
            epic = s["epic"]
            lines += ["## " + epic, ""]
        lines += ["### %s" % s["title"], "", "**%s** · Priority: %s · Status: %s" % (s["persona"], s["priority"], s["status"]),
                  "", s["narrative"], "", "**Acceptance criteria**", ""]
        lines += ["- " + a for a in s["acceptance_criteria"]] or ["- _none_"]
        if s.get("variant_criteria"):
            lines += ["", "**Product variants**", ""] + ["- _%s_: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"]]
        meta = []
        for key, label in (("product_scope", "Products"), ("compliance_tags", "Compliance"), ("labels", "Labels")):
            if s.get(key):
                meta.append("%s: %s" % (label, ", ".join(s[key])))
        if s.get("source_ref"):
            meta.append("Source: " + s["source_ref"])
        if meta:
            lines += ["", "_" + " | ".join(meta) + "_"]
        if s.get("open_questions"):
            lines += ["", "**Open questions**", ""] + ["- " + q for q in s["open_questions"]]
        lines.append("")
    return "\n".join(lines).encode("utf-8"), "text/markdown"


def to_xlsx(stories: List[Story]) -> Tuple[bytes, str]:
    wb = Workbook()
    ws = wb.active
    ws.title = "Stories"
    ws.append(COLUMNS)
    for s in stories:
        ws.append([_guard(_cell(s, c)) for c in COLUMNS])
    tm = wb.create_sheet("Traceability")
    tm.append(["source_ref", "story_id", "title", "product_scope", "compliance_tags"])
    for s in sorted(stories, key=lambda x: x.get("source_ref", "")):
        tm.append([_guard(s.get("source_ref", "")), s["id"], _guard(s["title"]),
                   _guard(_cell(s, "product_scope")), _guard(_cell(s, "compliance_tags"))])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def to_jira_csv(stories: List[Story]) -> Tuple[bytes, str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Summary", "Issue Type", "Priority", "Description", "Labels", "Epic Name"])
    for s in stories:
        desc = s["narrative"] + "\n\nh3. Acceptance criteria\n" + "\n".join("* " + a for a in s["acceptance_criteria"])
        if s.get("variant_criteria"):
            desc += "\n\nh3. Product variants\n" + "\n".join("* %s: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"])
        labels = [l.replace(" ", "-") for l in s.get("labels", []) + s.get("compliance_tags", [])]
        w.writerow([_guard(s["title"]), "Story", s["priority"], _guard(desc), " ".join(labels), _guard(s["epic"])])
    return buf.getvalue().encode("utf-8"), "text/csv"


def to_traceability(stories: List[Story]) -> Tuple[bytes, str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_ref", "story_id", "title", "product_scope", "compliance_tags"])
    for s in sorted(stories, key=lambda x: x.get("source_ref", "")):
        w.writerow([_guard(s.get("source_ref", "")), s["id"], _guard(s["title"]),
                    _guard(_cell(s, "product_scope")), _guard(_cell(s, "compliance_tags"))])
    return buf.getvalue().encode("utf-8"), "text/csv"


EXPORTERS: Dict[str, Tuple[Callable[[List[Story]], Tuple[bytes, str]], str]] = {
    "csv": (to_csv, "csv"), "json": (to_json, "json"), "md": (to_markdown, "md"),
    "xlsx": (to_xlsx, "xlsx"), "jira-csv": (to_jira_csv, "csv"), "traceability": (to_traceability, "csv"),
}
