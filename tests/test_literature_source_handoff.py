"""Synthetic access/receipt handoff only; no HTTP, PDF download or institution session."""
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from labhq.recruit import paper_skills as ps
from labhq.tools import annot_literature as lit
from labhq.tools.annot import Item
from tests.test_annot_literature import make, by_query, record, search_answer, complete_fallback_pdf


@pytest.mark.parametrize("row,status", [
    ({"found": False, "unread_reason": "no_copy"}, "not_obtained"),
    ({"found": True, "doi": "10.1000/test", "oa_locations": [{"url": "https://example.org"}]}, "undetermined"),
    ({"access_level": "metadata", "unread_reason": "subscription_required"}, "paywalled"),
    ({"access_level": "metadata", "unread_reason": "access_denied", "unread_detail": "HTTP 403"}, "not_obtained"),
    ({"access_level": "metadata", "fulltext_error": "HTTP 503"}, "undetermined"),
    ({"access_level": "abstract", "text_file": "abstract.md", "unread_reason": "subscription_required"}, "abstract_only"),
    ({"access_level": "full_text", "text_error": "write failed"}, "undetermined"),
])
def test_access_is_separate_from_discovery_and_evidence_status(row, status):
    before = copy.deepcopy(row)
    metadata = lit.source_access_metadata(row)
    assert metadata["access_status"] == status
    assert metadata["methods_locator"] is None and metadata["qc_locator"] is None
    assert "status" not in metadata and row == before


async def test_linked_supplement_and_unknown_dependency_survive_summary_receipt_and_cache(tmp_path):
    xml = ('<article><body><sec><title>Methods</title><p>Paired donors.</p></sec>'
           '<sec><title>QC</title><p>Synthetic checks.</p></sec></body>'
           '<back><supplementary-material><label>Supplement 1</label></supplementary-material></back></article>')
    annotator, calls = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}, xml=xml), tmp_path)
    first = (await annotator.fulltext(["123"]))["results"][0]
    receipt = first["fulltext_receipt"]
    assert first["access_status"] == "full_text" and first["supplementary_status"] == "missing"
    assert receipt["supplements_required"] is None and receipt["supplements"] == "missing"
    assert receipt["evidence"] == [] and first["methods_locator"] is None and first["qc_locator"] is None
    assert ps.evidence_problem(receipt) is not None
    count = len(calls)
    second = (await annotator.fulltext(["123"]))["results"][0]
    assert len(calls) == count and second["fulltext_receipt"] == receipt
    assert second["supplementary_status"] == "missing"
    declared = copy.deepcopy(receipt)
    lines = Path(receipt["text_file"]).read_text(encoding="utf-8").splitlines()
    declared["evidence"] = [{"kind": kind, "start_line": lines.index(text) + 1,
                             "end_line": lines.index(text) + 1}
                            for kind, text in (("methods", "Paired donors."), ("qc", "Synthetic checks."))]
    declared["supplements_required"] = False
    assert ps.verified_text(declared, tmp_path, "pmid:123") == Path(receipt["text_file"]).read_bytes()
    metadata = lit.source_access_metadata({"fulltext_receipt": declared})
    assert metadata["methods_locator"].endswith(f":L{lines.index('Paired donors.') + 1}-L{lines.index('Paired donors.') + 1}")
    assert metadata["qc_locator"] and receipt["evidence"] == []
    declared["supplements"] = "complete"
    with pytest.raises(ValueError, match="원본"):
        ps.verified_text(declared, tmp_path, "pmid:123")


@pytest.mark.parametrize("format", ["PDF", "HTML"])
async def test_oa_copy_keeps_byte_hash_and_extraction_without_inventing_reading(tmp_path, monkeypatch, format):
    import sys
    monkeypatch.setitem(sys.modules, "pypdf", None)
    content = complete_fallback_pdf() if format == "PDF" else (
        '<html><body><h1>Introduction</h1><h2>Methods</h2>' + 'synthetic study body ' * 100 + '</body></html>').encode()

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "N", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{"open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "https://93.184.216.34/paper", "is_oa": True}}]})
        assert request.url.host == "93.184.216.34"
        return httpx.Response(200, content=content, headers={"content-type": "application/pdf" if format == "PDF" else "text/html"})

    annotator, _ = make(handler, tmp_path)
    row = (await annotator.fulltext(["123"]))["results"][0]
    receipt = row["fulltext_receipt"]
    assert row["access_status"] == "full_text" and receipt["extraction"] == ("partial_text" if format == "PDF" else "unknown")
    assert row["oa_copy"]["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert receipt["oa_copy"] == row["oa_copy"] and row["oa_copy"]["format"] == format
    assert row["text_sha256"] == receipt["text_sha256"] == hashlib.sha256(Path(receipt["text_file"]).read_bytes()).hexdigest()
    assert receipt["evidence"] == [] and receipt["supplements_required"] is None
    assert row["supplementary_status"] == "undetermined"


def test_pdf_paths_hashes_and_declared_locators_survive_summary():
    row = {"found": True, "access_level": "full_text", "text_file": "paper.md", "text_sha256": "a" * 64,
           "pdf_file": ".tmp/literature/inbox/paper.pdf", "pdf_sha256": "b" * 64,
           "fulltext_receipt": {"access_level": "full_text", "text_file": "paper.md", "text_sha256": "a" * 64,
                                "evidence": [{"kind": "methods", "start_line": 2, "end_line": 3}], "supplements": "unknown"}}
    summary = lit._summary(Item(lit.PaperId("pmid", "123"), "123", answer=row))
    assert summary["pdf_file"] == row["pdf_file"] and summary["pdf_sha256"] == row["pdf_sha256"]
    assert summary["methods_locator"] == "paper.md:L2-L3" and summary["qc_locator"] is None


def test_receipt_refuses_outside_file_or_wrong_hash(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("Synthetic Methods QC", encoding="utf-8")
    base = {"text_file": "paper.md", "text_sha256": "0" * 64, "access_level": "full_text"}
    assert lit.fulltext_receipt("PMID:123", base, tmp_path) is None
    assert lit.fulltext_receipt("PMID:123", {**base, "text_file": str(source)}, tmp_path / "another") is None
    assert not source.with_name("paper.md.receipt.json").exists()


def test_legacy_receipt_without_new_metadata_still_validates(tmp_path):
    source = tmp_path / "legacy.md"
    source.write_text("Methods\nQC\n", encoding="utf-8")
    legacy = {"coverage_version": 2, "paper_id": "PMID:123", "source": "legacy", "access_level": "full_text",
              "extraction": "complete", "text_file": str(source), "text_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "evidence": [{"kind": "methods", "start_line": 1, "end_line": 1}, {"kind": "qc", "start_line": 2, "end_line": 2}],
              "supplements_required": False, "supplements": "unknown"}
    source.with_name("legacy.md.receipt.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert ps.verified_text(legacy, tmp_path, "pmid:123") == source.read_bytes()


def test_abstract_receipt_is_not_promoted_in_paper_skill_selection():
    receipt = {"access_level": "abstract", "text_file": "abstract.md", "text_sha256": "a" * 64,
               "evidence": [], "supplements": "unknown", "unread_reason": "subscription_required"}
    chosen = ps.selections({"steps": [{"id": "s", "agent_id": "worker"}], "paper_skills": [{
        "paper_id": "PMID:123", "title": "Synthetic", "reason": "test", "step_ids": ["s"]}]},
        {"papers": [{"title": "Synthetic", "citations": ["PMID:123"], "fulltext_receipt": receipt}]}, [{"id": "worker"}])
    assert chosen[0]["access_level"] == "abstract" and chosen[0]["access_status"] == "abstract_only"
    assert chosen[0]["fulltext_receipt"] == receipt and ps.evidence_problem(receipt) is not None
