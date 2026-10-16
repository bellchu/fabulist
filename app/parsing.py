"""Extract plain text from uploaded BRD files."""
import io
from typing import Callable, Dict, List

from . import config


class ParseError(ValueError):
    pass


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def _docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    out = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading") or style == "title":
            out.append("\n# " + text)
        else:
            out.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n".join(out)


_STATEMENT_COLS = ("requirement", "statement", "description", "user story", "story", "text", "detail")
_ID_COLS = ("id", "req id", "requirement id", "ref", "reference", "key", "#")
MAX_ROWS = 5000


def _rows_to_text(title: str, rows: List[List[str]]) -> str:
    """Render a table as BRD text. If a requirement/description column exists, emit 'ID: statement'
    lines (plus other columns as context); otherwise 'header: value' pairs per row."""
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)][: MAX_ROWS + 1]
    if not rows:
        return ""
    header = [h.lower() for h in rows[0]]
    stmt = next((i for i, h in enumerate(header) if any(h == c or h.startswith(c) for c in _STATEMENT_COLS)), None)
    ident = next((i for i, h in enumerate(header) if h in _ID_COLS), None)
    out = ["# " + title]
    for r in rows[1:]:
        r = r + [""] * (len(header) - len(r))
        if stmt is not None and r[stmt]:
            extra = ["%s: %s" % (rows[0][i], v) for i, v in enumerate(r) if v and i not in (stmt, ident) and i < len(rows[0])]
            line = ("%s: " % r[ident] if ident is not None and r[ident] else "") + r[stmt].rstrip(".") + "."
            out.append(line + (" (" + "; ".join(extra) + ")" if extra else ""))
        elif stmt is None:
            out.append("; ".join("%s: %s" % (rows[0][i], v) for i, v in enumerate(r) if v and i < len(rows[0])))
    return "\n".join(out)


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True, max_row=MAX_ROWS + 1)]
        parts.append(_rows_to_text(ws.title, rows))
    wb.close()
    return "\n\n".join(p for p in parts if p)


def _csv(data: bytes) -> str:
    import csv

    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return _rows_to_text("Requirements", list(csv.reader(io.StringIO(text), dialect)))


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


PARSERS: Dict[str, Callable[[bytes], str]] = {
    ".pdf": _pdf, ".docx": _docx, ".xlsx": _xlsx, ".csv": _csv, ".md": _text, ".markdown": _text, ".txt": _text,
}


def extract_text(filename: str, data: bytes) -> str:
    ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    parser = PARSERS.get(ext)
    if parser is None:
        raise ParseError("Unsupported file type. Use PDF, DOCX, XLSX, CSV, MD or TXT.")
    try:
        text = parser(data)
    except Exception as exc:  # corrupt/encrypted files surface as a clean 4xx
        raise ParseError("Could not read file: %s" % type(exc).__name__) from exc
    text = text.replace("\x00", "").strip()
    if not text:
        raise ParseError("No text could be extracted from the file.")
    return text[: config.MAX_DOC_CHARS]
