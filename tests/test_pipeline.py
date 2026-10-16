import json

import pytest

from app import exporters, llm, parsing, pipeline, quality

BRD = """# Order Management

## Order capture
BR-1: The system shall allow a sales representative to create orders for pharmacy customers.
The platform must track lot and expiry for biologic vaccines held in cold chain storage.
The system must respond to order searches within 2 seconds.

## Billing
Finance should be able to process chargeback claims from wholesalers quickly.
"""
OPTS = {"chunk_chars": 5000, "max_chunks_per_run": 50, "rag_top_k": 0, "redact_pii": True}
MOCK = {"kind": "mock", "name": "mock", "model": ""}


def _reqs():
    reqs = pipeline.extract_requirements([{"id": "d1", "filename": "brd.md", "text": BRD}], "sap", MOCK, OPTS, lambda m: None, {"in": 0, "out": 0})
    for i, r in enumerate(reqs):
        r["key"] = "REQ-%03d" % (i + 1)
        r["issues"] = quality.requirement_issues(r)
    return reqs


def test_chunk_sections_tracks_epic_and_feature():
    chunks = pipeline.chunk_sections(BRD, 5000)
    assert [(c["epic"], c["heading"]) for c in chunks] == [("Order Management", "Order capture"), ("Order Management", "Billing")]
    long = "# H\n" + "\n".join("line %d requirement text here" % i for i in range(400))
    assert all(len(p["text"]) <= 1000 for p in pipeline.chunk_sections(long, 1000))


def test_extract_requirements_types_ids_and_scope():
    reqs = _reqs()
    assert len(reqs) == 4
    assert reqs[0]["source_id"] == "BR-1" and reqs[0]["statement"].startswith("The system shall allow")
    assert reqs[0]["actors"] == ["sales representative"]
    assert reqs[2]["type"] == "non-functional"
    assert "Biologics and vaccines" in reqs[1]["product_scope"]
    assert reqs[1]["section"] == "Order capture" and reqs[1]["epic"] == "Order Management"


def test_extract_respects_section_limit():
    with pytest.raises(ValueError):
        pipeline.extract_requirements([{"id": "d", "filename": "f", "text": BRD}], "sap", MOCK, dict(OPTS, max_chunks_per_run=1), lambda m: None, {"in": 0, "out": 0})


def test_mock_stories_link_requirements_and_fold_nfrs():
    reqs = _reqs()
    stories = pipeline.generate_stories(reqs, "p", "sap", MOCK, OPTS, lambda m: None, {"in": 0, "out": 0})
    linked = {k for s in stories for k in s["requirement_ids"]}
    assert linked == {r["key"] for r in reqs}, "every requirement must be covered"
    nfr = next(r for r in reqs if r["type"] == "non-functional")
    host = next(s for s in stories if nfr["key"] in s["requirement_ids"])
    assert any("2 seconds" in a for a in host["acceptance_criteria"])
    cold = next(s for s in stories if "Biologics and vaccines" in s["product_scope"])
    assert any("Temperature excursions" in v["criteria"] for v in cold["variant_criteria"])
    billing = next(s for s in stories if s["feature"] == "Billing")
    assert billing["open_questions"], "vague 'quickly' should surface as an open question"


def test_requirement_quality_flags():
    issues = {i["code"] for i in quality.requirement_issues({"statement": "The system should be fast and user-friendly, TBD.", "type": "non-functional"})}
    assert {"incomplete", "ambiguous", "weak-modal", "not-verifiable"} <= issues
    assert quality.requirement_issues({"statement": "The system shall export invoices to CSV.", "type": "functional"}) == []


def test_story_quality_and_readiness():
    good = {"title": "Export invoices", "persona": "finance analyst", "narrative": "As a finance analyst, I want to export invoices, so that I can reconcile.",
            "acceptance_criteria": ["Given invoices exist, when I export, then a CSV downloads."], "requirement_ids": ["REQ-001"], "open_questions": []}
    issues = quality.story_issues(good, {"REQ-001"})
    assert issues == [] and quality.is_ready(issues) and quality.score(issues) == 100
    bad = quality.story_issues({"title": "x", "narrative": "do it", "acceptance_criteria": [], "requirement_ids": ["REQ-404"]}, {"REQ-001"})
    codes = {i["code"] for i in bad}
    assert {"narrative", "testable", "trace", "valuable"} <= codes and not quality.is_ready(bad)


def test_normalize_story_coerces_fields():
    assert pipeline.normalize_story({"title": ""}) is None
    s = pipeline.normalize_story({"title": "T", "priority": "urgent", "story_points": "5", "labels": "a"})
    assert s["priority"] == "Medium" and s["story_points"] == 5 and s["labels"] == ["a"]
    assert pipeline.normalize_story({"title": "T", "story_points": 4})["story_points"] is None


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


def _story(**kw):
    base = {"id": "1", "key": "US-001", "epic": "E", "feature": "F", "title": "=cmd()", "persona": "p", "narrative": "n",
            "acceptance_criteria": ["a"], "priority": "High", "story_points": 3, "labels": [], "product_scope": [],
            "variant_criteria": [{"scope": "s", "criteria": "c"}], "compliance_tags": ["DEA"], "requirement_ids": ["REQ-001"],
            "open_questions": [], "status": "approved", "quality_score": 90, "ready": True}
    base.update(kw)
    return base


REQ = {"key": "REQ-001", "type": "functional", "statement": "The system shall x", "source_ref": "d > h", "status": "approved"}


@pytest.mark.parametrize("fmt", list(exporters.EXPORTERS))
def test_every_exporter_produces_output(fmt):
    data, mime = exporters.EXPORTERS[fmt][0]([_story()], [REQ, dict(REQ, key="REQ-002")])
    assert data and mime


def test_csv_neutralizes_formula_injection_and_rtm_flags_gaps():
    data, _ = exporters.stories_csv([_story()], [REQ])
    assert b"'=cmd()" in data and b",=cmd()" not in data
    rtm, _ = exporters.rtm_csv([_story()], [REQ, dict(REQ, key="REQ-002")])
    assert b"REQ-002" in rtm and b"GAP" in rtm
    assert json.loads(exporters.as_json([_story()], [REQ])[0])["stories"][0]["key"] == "US-001"


def test_classify_and_extract_edge_cases():
    assert pipeline.classify("The system must respond within 2 seconds for 95% of requests.") == "non-functional"
    assert pipeline.classify("Customers can request returns within 30 days of delivery.") == "rule"
    reqs = pipeline.mock_requirements({"text": "BR-303: Returned implants require UDI capture before credit is issued, rules TBD."})
    assert len(reqs) == 1 and reqs[0]["source_id"] == "BR-303"
    issues = {i["code"] for i in quality.requirement_issues({"statement": reqs[0]["statement"], "type": "rule"})}
    assert "incomplete" in issues
    assert "not-singular" in {i["code"] for i in quality.requirement_issues({"statement": "Agents should place orders and screens must be clear.", "type": "functional"})}


def test_duplicate_requirements_detected():
    reqs = [{"key": "REQ-001", "statement": "The system shall allow buyers to cancel open orders before shipment."},
            {"key": "REQ-002", "statement": "Buyers must be able to cancel open orders before shipment."},
            {"key": "REQ-003", "statement": "The system shall export invoices to CSV."},
            {"key": "REQ-004", "statement": "Short."}]
    d = quality.duplicate_pairs(reqs)
    assert d == {"REQ-001": ["REQ-002"], "REQ-002": ["REQ-001"]}


def test_html_report_escapes_and_flags_gaps():
    data, mime = exporters.html_report([_story(title="<script>x</script>")], [REQ, dict(REQ, key="REQ-002")], "Demo <b>")
    text = data.decode()
    assert mime == "text/html" and "<script>x" not in text and "&lt;script&gt;" in text and "Demo &lt;b&gt;" in text
    assert "<span class='gap'>Gap</span>" in text and "50%" in text


def test_mock_refine_requirement_splits_and_strengthens():
    req = {"key": "REQ-004", "type": "functional", "section": "Order capture", "epic": "Orders", "source_ref": "brd.md > Order capture",
           "source_id": "BR-104", "actors": ["customer service agent"],
           "statement": "Customer service agents should be able to place orders on behalf of a clinic and the order screens must be user-friendly."}
    out = pipeline.refine_requirement(req, quality.requirement_issues(req), "generic", {"kind": "mock"}, {}, {"in": 0, "out": 0})
    assert [r["statement"] for r in out] == ["Customer service agents shall be able to place orders on behalf of a clinic.",
                                             "The order screens must be user-friendly."]
    assert all(r["source_id"] == "BR-104" and r["section"] == "Order capture" for r in out)
    assert "user-friendly" in out[1]["notes"] and out[0]["notes"] == ""


def test_quality_false_positives_from_real_run():
    identify = {"key": "REQ-010", "statement": "The system shall enable Compliance to identify affected lots within 4 hours of a recall notice."}
    quarantine = {"key": "REQ-011", "statement": "The system shall enable Compliance to quarantine affected lots within 4 hours of a recall notice."}
    assert quality.duplicate_pairs([identify, quarantine]) == {}, "one different action verb = distinct requirements"
    audit = {"statement": "The system shall capture all pricing changes in an audit trail recording the user, timestamp, and reason.", "type": "non-functional"}
    assert quality.requirement_issues(audit) == []
    slow = {"statement": "Inventory availability checks must respond quickly.", "type": "non-functional"}
    assert "not-verifiable" in {i["code"] for i in quality.requirement_issues(slow)}
    noted = {"statement": "The system shall require UDI capture for returned implants.", "type": "rule", "notes": "Credit rules TBD"}
    assert "incomplete" in {i["code"] for i in quality.requirement_issues(noted)}
    assert "TBD" in pipeline.REQ_SYSTEM


def test_gherkin_steps_and_feature_zip():
    import io
    import zipfile
    assert exporters.gherkin_steps("Given I am signed in as a buyer, when I submit an order, then I see a confirmation.") == [
        "Given I am signed in as a buyer", "When I submit an order", "Then I see a confirmation"]
    assert exporters.gherkin_steps("Order status becomes Cancelled") == ["Then order status becomes Cancelled"]
    story = _story(title="Cancel orders", acceptance_criteria=["Given an open order, when I cancel it, then it is cancelled.", "Audit entry is written"],
                   variant_criteria=[{"scope": "Controlled substances", "criteria": "Cancellation is reported to DEA."}])
    data, mime = exporters.gherkin_zip([story], [REQ])
    z = zipfile.ZipFile(io.BytesIO(data))
    assert mime == "application/zip" and z.namelist() == ["features/e-f.feature"]
    text = z.read("features/e-f.feature").decode()
    assert "Feature: E / F" in text and "@US-001 @REQ-001 @DEA" in text and "    When I cancel it" in text
    assert "Then audit entry is written" in text and 'Given the product is in class "Controlled substances"' in text
