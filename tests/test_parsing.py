import io

import pytest
from docx import Document
from openpyxl import Workbook

from app import parsing, pipeline


def minimal_pdf(text: str) -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offsets:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_pdf():
    assert parsing.extract_text("brd.pdf", minimal_pdf("The system shall ship orders.")) == "The system shall ship orders."
    with pytest.raises(parsing.ParseError, match="Could not read"):
        parsing.extract_text("broken.pdf", b"%PDF-1.4 garbage")


def test_docx_headings_and_tables():
    doc = Document()
    doc.add_heading("Order capture", level=1)
    doc.add_paragraph("The system shall allow buyers to place orders.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "ID", "Requirement"
    table.cell(1, 0).text, table.cell(1, 1).text = "BR-7", "Invoices must show contract prices."
    buf = io.BytesIO()
    doc.save(buf)
    text = parsing.extract_text("brd.docx", buf.getvalue())
    assert "# Order capture" in text and "place orders" in text and "BR-7 | Invoices must show contract prices." in text
    assert pipeline.chunk_sections(text, 5000)[0]["heading"] == "Order capture"


def _xlsx(rows, title="Order capture"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_requirement_sheet_becomes_requirements():
    data = _xlsx([["Req ID", "Requirement", "Priority"],
                  ["BR-1", "The system shall allow buyers to cancel open orders", "High"],
                  [None, None, None],
                  ["BR-2", "The system must email confirmations.", None]])
    text = parsing.extract_text("reqs.xlsx", data)
    assert text.splitlines() == ["# Order capture", "BR-1: The system shall allow buyers to cancel open orders. (Priority: High)",
                                 "BR-2: The system must email confirmations."]
    reqs = pipeline.mock_requirements(pipeline.chunk_sections(text, 5000)[0])
    assert [r["source_id"] for r in reqs] == ["BR-1", "BR-2"]


def test_xlsx_generic_table_and_csv():
    text = parsing.extract_text("t.xlsx", _xlsx([["Field", "Rule"], ["Lot", "Required for biologics"]], title="Data"))
    assert text == "# Data\nField: Lot; Rule: Required for biologics"
    csv_text = parsing.extract_text("r.csv", "ID;Description\nNFR-1;Search must respond within 2 seconds\n".encode("utf-8-sig"))
    assert csv_text == "# Requirements\nNFR-1: Search must respond within 2 seconds."
