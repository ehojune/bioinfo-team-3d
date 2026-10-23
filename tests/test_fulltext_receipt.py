"""Synthetic artifacts only: no PDF extraction, network, process, or research execution."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from labhq.models import Task, TaskResult
from labhq.recruit import paper_skills as ps
from labhq.settings import Settings
from labhq.tools.annot_literature import fulltext_receipt


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "scout" / "paper.md"
    source.parent.mkdir()
    source.write_text("# Synthetic paper\nMethods: paired donors\nQC: synthetic only\n", encoding="utf-8")
    row = fulltext_receipt("PMID:123", {"text_file": "scout/paper.md", "access_level": "full_text", "extraction": "complete",
        "text_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}, tmp_path)
    row["evidence"] = [{"kind": "methods", "start_line": 2, "end_line": 2},
                       {"kind": "qc", "start_line": 3, "end_line": 3}]
    row["supplements_required"] = False  # synthetic scout's explicit dependency decision
    return source, row


def build_for(tmp_path, row, monkeypatch):
    monkeypatch.setattr("labhq.adapters.claude_code._git", lambda *a: None)
    monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a: {"status": "ok"})
    s = Settings()
    s.runner.workspace_root = str(tmp_path)
    runner = SimpleNamespace(s=s, registry=SimpleNamespace(talent_dir=tmp_path / "talent", agents={},
                                                         talent_pool=lambda: []))
    task = Task(agent_id=s.recruit.agent_id, request_id="r", prompt="fixture", meta={"paper_skill": {
        "paper_id": "PMID:123", "title": "Synthetic", "reason": "paired", "fulltext_receipt": row}})
    return ps.PaperSkillBuild(runner, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["string", "partial", "missing_qc", "supplement_missing", "supplement_claimed", "valid"])
async def test_cp1_checks_declarations_without_reading_files(sample, mode, monkeypatch):
    _, row = sample
    if mode == "partial": row["extraction"] = "partial_text"
    if mode == "missing_qc": row["evidence"] = row["evidence"][:1]
    if mode == "supplement_missing": row.update(supplements_required=True, supplements="missing")
    if mode == "supplement_claimed": row.update(supplements_required=True, supplements="complete")
    paper = {"title": "Synthetic", "citations": ["PMID:123 access_level=full_text"]}
    if mode != "string": paper["fulltext_receipt"] = row
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda *a: pytest.fail("CP1 read artifact"))
    run = AsyncMock(side_effect=AssertionError("CP1 dispatched"))
    plan = await ps.prepare_plan({"steps": [{"id": "s", "agent_id": "worker"}], "paper_skills": [{
        "paper_id": "PMID:123", "title": "Synthetic", "reason": "paired", "step_ids": ["s"]}]},
        {"papers": [paper]}, [{"id": "worker"}], "r", Settings(), run)
    assert plan["paper_skill_results"][0]["status"] == ("deferred" if mode == "valid" else "skipped")
    run.assert_not_awaited()


@pytest.mark.parametrize("mode", ["valid", "changed", "missing", "range", "outside", "wrong_id", "partial"])
def test_artifact_validation_after_approval(tmp_path, sample, mode, monkeypatch):
    source, row = sample
    if mode == "changed": source.write_text("changed", encoding="utf-8")
    if mode == "missing": source.unlink()
    if mode == "range": row["evidence"][0]["end_line"] = 99
    if mode == "outside": row["text_file"] = str(tmp_path.parent / "outside.md")
    if mode == "wrong_id": row["paper_id"] = "PMID:456"
    if mode == "partial": row["extraction"] = "partial_text"
    build = build_for(tmp_path, row, monkeypatch)
    task, directory, result = build.prepare()
    if mode == "valid":
        assert result is None and (directory / "paper-input.md").read_bytes() == source.read_bytes()
        assert str(directory / "paper-input.md") in task.prompt and "Evidence line ranges" in task.prompt
    else:
        assert directory is None and result.structured["status"] == "failed"


def test_reuse_requires_same_source_receipt_and_hash(tmp_path, sample, monkeypatch):
    source, row = sample
    build = build_for(tmp_path, row, monkeypatch)
    _, directory, _ = build.prepare()
    skill = directory / "delivered"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Synthetic advice", encoding="utf-8")
    monkeypatch.setattr("labhq.recruit.paper2agent.probation", lambda *a: (True, ""))
    monkeypatch.setattr("labhq.recruit.paper2agent.recruiter_engine", lambda *a: "mock")
    result = build.finish(TaskResult(task_id=build.task.id, agent_id=build.task.agent_id, ok=True,
                                    structured={"skill_dir": str(skill), "verification_status": "verified"}))
    assert result.structured["status"] == "ready"
    again = build_for(tmp_path, copy.deepcopy(row), monkeypatch)
    assert again.prepare()[2].structured["status"] == "reused"
    changed = copy.deepcopy(row)
    changed["evidence"][0]["end_line"] = 3
    assert build_for(tmp_path, changed, monkeypatch).prepare()[2].structured["status"] == "failed"
    source.write_text("changed", encoding="utf-8")
    assert again.prepare()[2].structured["status"] == "failed"


def test_legacy_receipt_is_not_silently_reused(tmp_path, sample, monkeypatch):
    _, row = sample
    build = build_for(tmp_path, row, monkeypatch)
    build.talent.mkdir(parents=True)
    build.receipt.write_text(json.dumps({"paper_id": "pmid:123", "skill_sha256": "a" * 64}), encoding="utf-8")
    assert build.prepare()[2].structured["status"] == "failed"


def test_converter_input_change_is_rejected(tmp_path, sample, monkeypatch):
    _, row = sample
    build = build_for(tmp_path, row, monkeypatch)
    _, directory, _ = build.prepare()
    (directory / "paper-input.md").write_text("changed", encoding="utf-8")
    result = build.finish(TaskResult(task_id=build.task.id, agent_id=build.task.agent_id, ok=True,
                                    structured={"skill_dir": str(directory)}))
    assert result.structured["status"] == "failed" and "hash" in result.structured["error"]


@pytest.mark.parametrize("mode", ["partial_promoted", "source_relabel", "sidecar_missing", "access_promoted"])
def test_declared_receipt_cannot_override_tool_original(tmp_path, sample, monkeypatch, mode):
    source, row = sample
    original_path = source.with_name(source.name + ".receipt.json")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    if mode == "partial_promoted":
        produced = fulltext_receipt("PMID:123", {**original, "text_file": "scout/paper.md",
                                                 "extraction": "partial_text"}, tmp_path)
        assert produced["extraction"] == "partial_text"
        assert row["extraction"] == "complete"  # same actual text/hash, falsely promoted declaration
    elif mode == "access_promoted":
        produced = fulltext_receipt("PMID:123", {**original, "text_file": "scout/paper.md",
                                                 "access_level": "abstract"}, tmp_path)
        assert produced["access_level"] == "abstract"
    elif mode == "source_relabel":
        row["source"] = "invented provider"
    else:
        original_path.unlink()
    result = build_for(tmp_path, row, monkeypatch).prepare()[2]
    assert result.structured["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,coverage", [("literal_pdf", "partial_text"),
    ("missing_page", "partial_text"), ("nonempty_pages", "unknown"), ("jats", "complete")])
async def test_automatic_oa_coverage_reaches_original_receipt_plan_and_build(
        tmp_path, monkeypatch, mode, coverage):
    import sys
    import httpx
    from tests.test_annot_literature import make, by_query, record, search_answer, complete_fallback_pdf

    if mode == "literal_pdf":
        monkeypatch.setitem(sys.modules, "pypdf", None)  # force the real bounded regex fallback
    elif mode in {"missing_page", "nonempty_pages"}:
        values = ["Methods and QC synthetic text", "" if mode == "missing_page" else "Results synthetic text"]
        pages = [SimpleNamespace(extract_text=lambda value=value: value) for value in values]
        monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(
            PdfReader=lambda _stream: SimpleNamespace(pages=pages)))
    xml = "<article><body><sec><title>Methods</title><p>Paired donors.</p></sec>" \
          "<sec><title>QC</title><p>Synthetic quality checks.</p></sec></body></article>"

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "N", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 1}, "results": [{
                "id": "https://openalex.org/W1", "title": "Synthetic", "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "http://93.184.216.34/paper.pdf", "is_oa": True}}]})
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, content=complete_fallback_pdf("Synthetic Methods QC"),
                                  headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}, xml=xml)
                       if mode == "jats" else handler, tmp_path)
    row = (await annotator.fulltext(["PMID:123"]))["results"][0]
    receipt = copy.deepcopy(row["fulltext_receipt"])
    assert row["access_level"] == "full_text" and receipt["extraction"] == coverage
    original = json.loads(Path(receipt["text_file"] + ".receipt.json").read_text(encoding="utf-8"))
    assert original["extraction"] == coverage and original["text_sha256"] == receipt["text_sha256"]
    lines = Path(receipt["text_file"]).read_text(encoding="utf-8").splitlines()
    locations = {"methods": lines.index("Paired donors.") + 1,
                 "qc": lines.index("Synthetic quality checks.") + 1} if mode == "jats" else {"methods": 1, "qc": 1}
    receipt["evidence"] = [{"kind": kind, "start_line": line, "end_line": line}
                           for kind, line in locations.items()]
    receipt["supplements_required"] = False  # independently declared after locating evidence
    plan = await ps.prepare_plan({"steps": [{"id": "s", "agent_id": "worker"}], "paper_skills": [{
        "paper_id": "PMID:123", "title": "Synthetic", "reason": "paired", "step_ids": ["s"]}]},
        {"papers": [{"title": "Synthetic", "citations": ["PMID:123"], "fulltext_receipt": receipt}]},
        [{"id": "worker"}], "r", Settings(), AsyncMock())
    assert plan["paper_skill_results"][0]["status"] == ("deferred" if coverage == "complete" else "skipped")
    _, directory, result = build_for(tmp_path, receipt, monkeypatch).prepare()
    if coverage == "complete":
        assert result is None and (directory / "paper-input.md").is_file()
    else:
        assert directory is None and result.structured["status"] == "failed"


def test_access_label_without_extraction_stays_unknown(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("Synthetic readable text", encoding="utf-8")
    receipt = fulltext_receipt("PMID:123", {"text_file": "paper.md", "access_level": "full_text",
        "text_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}, tmp_path)
    assert receipt["extraction"] == "unknown"
    assert json.loads(source.with_name("paper.md.receipt.json").read_text(encoding="utf-8"))["extraction"] == "unknown"


def test_jats_omitted_text_block_is_partial():
    from labhq.tools.annot_literature import jats_markdown
    _, info = jats_markdown("<article><body><sec><title>Methods</title><p>Readable.</p>"
                           "<unhandled>Missing method parameters.</unhandled></sec></body></article>", [])
    assert info["extraction"] == "partial_text"


def test_automatic_html_structure_is_not_extraction_coverage():
    from labhq.tools.annot_literature import _copy_text
    html = "<html><body><h1>Methods</h1><p>" + "synthetic text " * 120 + "</p><h2>Results</h2></body></html>"
    text, body_format, coverage = _copy_text(html.encode(), "text/html")
    assert text and body_format == "HTML" and coverage == "unknown"


@pytest.mark.parametrize("claimed", [False, True])
def test_old_false_complete_receipt_requires_revalidation(tmp_path, sample, monkeypatch, claimed):
    source, row = sample
    original_path = source.with_name(source.name + ".receipt.json")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    original.pop("coverage_version")
    original_path.write_text(json.dumps(original), encoding="utf-8")
    if not claimed:
        row.pop("coverage_version")
    result = build_for(tmp_path, row, monkeypatch).prepare()[2]
    assert result.structured["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["institution", "pi"])
async def test_pdftotext_success_preserves_access_but_requires_coverage_recheck(tmp_path, monkeypatch, route):
    from labhq.settings import LiteratureSettings
    from labhq.tools import annot_literature as lit
    from tests.test_literature_institution import coordinator, FakeInstitution, paper, PDF

    def fake_pdftotext(command, **_kwargs):
        # A readable Results page can coexist with scanned/unreadable Methods and QC.
        Path(command[-1]).write_text("Key paper DOI 10.1000/key. Results. Table 2. Readable results page.",
                                   encoding="utf-8")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lit.shutil, "which", lambda _name: "fixture-pdftotext")
    monkeypatch.setattr(lit.subprocess, "run", fake_pdftotext)

    async def provide(_summary, detail, _timeout):
        supplied = tmp_path / "work" / detail["papers"][0]["pdf_file"]
        supplied.parent.mkdir(parents=True, exist_ok=True)
        supplied.write_bytes(PDF)
        return {"approved": True, "state": "resolved"}

    settings = LiteratureSettings(external_model_allowed=True, institution_enabled=route == "institution",
                                  institution_tdm_allowed=route == "institution", institution_profile="synthetic" if route == "institution" else "")
    flow = coordinator(tmp_path, settings, FakeInstitution(), provide, extract=lit.extract_pdf_text)
    row = (await flow.run([paper()]))["results"][0]
    assert row["access_level"] == "full_text" and row["extraction"] == "unknown"
    assert row["read_source"] == ("institution_session" if route == "institution" else "pi_provided_pdf")
    assert "access_failure" not in row  # uncertain coverage does not turn access into access_denied
    receipt = copy.deepcopy(row["fulltext_receipt"])
    assert receipt["extraction"] == "unknown" and receipt["coverage_version"] == 2
    original = json.loads(Path(receipt["text_file"] + ".receipt.json").read_text(encoding="utf-8"))
    assert original["extraction"] == "unknown"
    receipt["evidence"] = [{"kind": kind, "start_line": 1, "end_line": 1} for kind in ("methods", "qc")]
    plan = await ps.prepare_plan({"steps": [{"id": "s", "agent_id": "worker"}], "paper_skills": [{
        "paper_id": "10.1000/key", "title": "Synthetic", "reason": "paired", "step_ids": ["s"]}]},
        {"papers": [{"title": "Synthetic", "citations": ["10.1000/key"], "fulltext_receipt": receipt}]},
        [{"id": "worker"}], "r", Settings(), AsyncMock())
    assert plan["paper_skill_results"][0]["status"] == "skipped"
    build = build_for(tmp_path, receipt, monkeypatch)
    build.pid = "doi:10.1000/key"
    assert build.prepare()[2].structured["status"] == "failed"


def test_v1_false_complete_institution_receipt_needs_recheck(tmp_path, sample, monkeypatch):
    _, row = sample
    row["coverage_version"] = 1
    assert build_for(tmp_path, row, monkeypatch).prepare()[2].structured["status"] == "failed"
