"""Exporters. Each takes (stories, requirements) in canonical form and returns (bytes, mime)."""
import csv
import io
import json
from typing import Any, Callable, Dict, List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

Item = Dict[str, Any]
Output = Tuple[bytes, str]
STORY_COLUMNS = ["key", "epic", "feature", "title", "persona", "narrative", "acceptance_criteria", "priority",
                 "story_points", "requirement_ids", "labels", "product_scope", "variant_criteria", "compliance_tags",
                 "open_questions", "quality_score", "ready", "status"]
REQ_COLUMNS = ["key", "type", "statement", "epic", "section", "source_id", "source_ref", "actors", "product_scope",
               "compliance_tags", "quality_score", "status"]


def _cell(item: Item, col: str) -> str:
    v = item.get(col, "")
    if col == "variant_criteria":
        return "\n".join("[%s] %s" % (x.get("scope", ""), x.get("criteria", "")) for x in v or [])
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    return "" if v is None else str(v)


def _guard(value: str) -> str:
    """Neutralize spreadsheet formula injection in CSV/XLSX cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _csv(header: List[str], rows: List[List[str]]) -> Output:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_guard(c) for c in r])
    return buf.getvalue().encode("utf-8"), "text/csv"


def _rtm_rows(stories: List[Item], reqs: List[Item]) -> List[List[str]]:
    by_req: Dict[str, List[Item]] = {}
    for s in stories:
        for k in s.get("requirement_ids", []):
            by_req.setdefault(k, []).append(s)
    rows = []
    for r in reqs:
        linked = by_req.get(r["key"], [])
        rows.append([r["key"], r["type"], r["statement"], r.get("source_ref", ""),
                     "\n".join(s["key"] for s in linked), "\n".join(s["title"] for s in linked),
                     "covered" if linked else "GAP"])
    return rows


RTM_HEADER = ["requirement", "type", "statement", "source", "story_keys", "story_titles", "coverage"]


def stories_csv(stories: List[Item], reqs: List[Item]) -> Output:
    return _csv(STORY_COLUMNS, [[_cell(s, c) for c in STORY_COLUMNS] for s in stories])


def requirements_csv(stories: List[Item], reqs: List[Item]) -> Output:
    return _csv(REQ_COLUMNS, [[_cell(r, c) for c in REQ_COLUMNS] for r in reqs])


def rtm_csv(stories: List[Item], reqs: List[Item]) -> Output:
    return _csv(RTM_HEADER, _rtm_rows(stories, reqs))


def jira_csv(stories: List[Item], reqs: List[Item]) -> Output:
    rows = []
    for s in stories:
        desc = s["narrative"] + "\n\nh3. Acceptance criteria\n" + "\n".join("* " + a for a in s["acceptance_criteria"])
        if s.get("variant_criteria"):
            desc += "\n\nh3. Product variants\n" + "\n".join("* %s: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"])
        if s.get("requirement_ids"):
            desc += "\n\nh3. Requirements\n" + ", ".join(s["requirement_ids"])
        labels = [x.replace(" ", "-") for x in s.get("labels", []) + s.get("compliance_tags", [])]
        rows.append([s["title"], "Story", s["priority"], str(s.get("story_points") or ""), desc, " ".join(labels), s["epic"]])
    return _csv(["Summary", "Issue Type", "Priority", "Story Points", "Description", "Labels", "Epic Link"], rows)


def azure_csv(stories: List[Item], reqs: List[Item]) -> Output:
    rows = []
    for s in stories:
        ac = "<br>".join(s["acceptance_criteria"])
        rows.append(["User Story", s["title"], s["narrative"], ac, {"High": "1", "Medium": "2", "Low": "3"}[s["priority"]],
                     str(s.get("story_points") or ""), "; ".join(s.get("labels", []) + s.get("compliance_tags", []))])
    return _csv(["Work Item Type", "Title", "Description", "Acceptance Criteria", "Priority", "Story Points", "Tags"], rows)


def as_json(stories: List[Item], reqs: List[Item]) -> Output:
    return json.dumps({"requirements": reqs, "stories": stories}, indent=2).encode("utf-8"), "application/json"


def markdown(stories: List[Item], reqs: List[Item]) -> Output:
    lines = ["# Product backlog", ""]
    epic = feature = None
    for s in stories:
        if s["epic"] != epic:
            epic, feature = s["epic"], None
            lines += ["## Epic: " + epic, ""]
        if s.get("feature") != feature:
            feature = s.get("feature")
            lines += ["### Feature: %s" % feature, ""]
        pts = " · %s pts" % s["story_points"] if s.get("story_points") else ""
        lines += ["#### %s: %s" % (s["key"], s["title"]), "",
                  "_%s · Priority %s%s · %s_" % (s["persona"], s["priority"], pts, s["status"]), "", s["narrative"], "",
                  "**Acceptance criteria**", ""]
        lines += ["- " + a for a in s["acceptance_criteria"]] or ["- _none_"]
        if s.get("variant_criteria"):
            lines += ["", "**Product variants**", ""] + ["- _%s_: %s" % (v["scope"], v["criteria"]) for v in s["variant_criteria"]]
        meta = []
        for key, label in (("requirement_ids", "Requirements"), ("product_scope", "Products"),
                           ("compliance_tags", "Compliance"), ("labels", "Labels")):
            if s.get(key):
                meta.append("%s: %s" % (label, ", ".join(s[key])))
        if meta:
            lines += ["", " | ".join(meta)]
        if s.get("open_questions"):
            lines += ["", "**Open questions**", ""] + ["- " + q for q in s["open_questions"]]
        lines.append("")
    return "\n".join(lines).encode("utf-8"), "text/markdown"


def xlsx(stories: List[Item], reqs: List[Item]) -> Output:
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="334155")

    def sheet(ws: Any, header: List[str], rows: List[List[str]], widths: Dict[int, int]) -> None:
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append([_guard(v) for v in r])
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        for idx, w in widths.items():
            ws.column_dimensions[chr(64 + idx)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Stories"
    sheet(ws, STORY_COLUMNS, [[_cell(s, c) for c in STORY_COLUMNS] for s in stories], {4: 40, 6: 60, 7: 70})
    sheet(wb.create_sheet("Requirements"), REQ_COLUMNS, [[_cell(r, c) for c in REQ_COLUMNS] for r in reqs], {3: 80})
    sheet(wb.create_sheet("Traceability"), RTM_HEADER, _rtm_rows(stories, reqs), {3: 70, 6: 50})
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# key -> (function, file extension, label, description)
EXPORTERS: Dict[str, Tuple[Callable[[List[Item], List[Item]], Output], str, str, str]] = {
    "xlsx": (xlsx, "xlsx", "Excel workbook", "Stories, requirements and traceability matrix on separate sheets."),
    "jira-csv": (jira_csv, "csv", "Jira import (CSV)", "For Jira's external system CSV import, with an Epic Link column."),
    "azure-csv": (azure_csv, "csv", "Azure DevOps import (CSV)", "Work items for Azure Boards CSV import."),
    "md": (markdown, "md", "Markdown", "Readable backlog for Confluence, wikis or pull requests."),
    "stories-csv": (stories_csv, "csv", "Stories (CSV)", "Every story field, one row per story."),
    "requirements-csv": (requirements_csv, "csv", "Requirements (CSV)", "Extracted requirements with quality scores."),
    "rtm-csv": (rtm_csv, "csv", "Traceability matrix (CSV)", "Requirement-to-story coverage, with gaps flagged."),
    "json": (as_json, "json", "JSON", "Requirements and stories for integrations and APIs."),
}
