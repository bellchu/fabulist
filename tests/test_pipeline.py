import json

import pytest

from app import exporters, llm, parsing, pipeline

BRD = """# Order Management

The system shall allow a sales representative to create orders for pharmacy customers.
The platform must track lot and expiry for biologic products held in cold chain storage.
Controlled substance orders must capture DEA CSOS details and keep an audit trail.

## Billing
Finance should be able to process chargeback claims from wholesalers.
"""


def test_chunk_sections_splits_on_headings_and_limit():
    chunks = pipeline.chunk_sections(BRD, 5000)
    assert [c["heading"] for c in chunks] == ["Order Management", "Billing"]
    long = "# H\n" + "\n".join("line %d requirement text here" % i for i in range(400))
    parts = pipeline.chunk_sections(long, 1000)
    assert len(parts) > 1 and all(len(p["text"]) <= 1000 for p in parts)


def test_mock_generation_extracts_stories_with_tags_and_scope():
    chunks = pipeline.chunk_sections(BRD, 5000)
    stories = [s for c in chunks for s in pipeline.mock_stories(c, "brd.md")]
    assert len(stories) == 4
    ctrl = next(s for s in stories if "Controlled" in s["title"] or "controlled" in s["title"].lower())
    assert "DEA" in ctrl["compliance_tags"] and "Controlled substances" in ctrl["product_scope"]
    assert ctrl["source_ref"] == "brd.md > Order Management"
    assert any(s["persona"] == "sales representative" for s in stories)


def test_normalize_story_validates_and_coerces():
    assert pipeline.normalize_story({"title": ""}) is None
    assert pipeline.normalize_story("nope") is None
    s = pipeline.normalize_story({"title": "T", "priority": "urgent", "labels": "a", "acceptance_criteria": ["x", ""]})
    assert s["priority"] == "Medium" and s["labels"] == ["a"] and s["acceptance_criteria"] == ["x"]


def test_parse_json_array_handles_fences_and_prose():
    assert pipeline.parse_json_array('Here:\n```json\n[{"title": "a"}]\n```') == [{"title": "a"}]
    with pytest.raises(ValueError):
        pipeline.parse_json_array("no json here")


def test_redact_masks_identifiers():
    out = llm.redact("Mail a.b@example.com or call (555) 123-4567, SSN 123-45-6789, card 4111111111111111")
    assert "@" not in out and "123-4567" not in out and "6789" not in out and "4111" not in out


def test_parsing_rejects_unsupported_and_empty():
    with pytest.raises(parsing.ParseError):
        parsing.extract_text("x.exe", b"abc")
    with pytest.raises(parsing.ParseError):
        parsing.extract_text("x.txt", b"   ")
    assert parsing.extract_text("x.md", b"# hi\nbody") == "# hi\nbody"


def _story(**kw):
    base = {"id": "1", "epic": "E", "title": "=cmd()", "persona": "p", "narrative": "n", "acceptance_criteria": ["a"],
            "priority": "High", "labels": [], "product_scope": [], "variant_criteria": [{"scope": "s", "criteria": "c"}],
            "compliance_tags": ["DEA"], "source_ref": "d > h", "open_questions": [], "status": "draft"}
    base.update(kw)
    return base


@pytest.mark.parametrize("fmt", list(exporters.EXPORTERS))
def test_every_exporter_produces_output(fmt):
    fn, _ext = exporters.EXPORTERS[fmt]
    data, mime = fn([_story()])
    assert data and mime


def test_csv_neutralizes_formula_injection():
    data, _ = exporters.to_csv([_story()])
    assert b"'=cmd()" in data and b",=cmd()" not in data
    assert json.loads(exporters.to_json([_story()])[0])[0]["id"] == "1"
