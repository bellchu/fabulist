"""Extract plain text from uploaded BRD files."""
import io
from typing import Callable, Dict

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


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


PARSERS: Dict[str, Callable[[bytes], str]] = {
    ".pdf": _pdf, ".docx": _docx, ".md": _text, ".markdown": _text, ".txt": _text,
}


def extract_text(filename: str, data: bytes) -> str:
    ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    parser = PARSERS.get(ext)
    if parser is None:
        raise ParseError("Unsupported file type. Use PDF, DOCX, MD or TXT.")
    try:
        text = parser(data)
    except Exception as exc:  # corrupt/encrypted files surface as a clean 4xx
        raise ParseError("Could not read file: %s" % type(exc).__name__) from exc
    text = text.replace("\x00", "").strip()
    if not text:
        raise ParseError("No text could be extracted from the file.")
    return text[: config.MAX_DOC_CHARS]
