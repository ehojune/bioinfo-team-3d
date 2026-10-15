"""PI report/operation boundary and paper layout (#609). No live staff or GitHub calls."""
import json
import zipfile
from pathlib import Path

import pytest

from labhq.integrations.rounds import build_record, render_record, _marked_body
from labhq.orchestrator import cso
from labhq.request_bundle import build_request_bundle
from test_round_records import settings, round_request
from test_request_bundle import pending_bundle_fixture
from labhq.gateway.server import Hub


@pytest.mark.parametrize("template", [cso.SYNTH_PROMPT, cso.RESEARCH_SYNTH_PROMPT, cso.SOLO_PROMPT], ids=["general", "research", "solo"])
def test_paper_section_order(template):
    headings = ['"제목"', '"요약(Abstract)"', '"서론"', '"방법"', '"결과"', '"논의"', '"결론"', '"참고"', '"부록"']
    positions = [template.index(heading) for heading in headings]
    assert positions == sorted(positions)


@pytest.mark.parametrize("template", [cso.PLAN_PROMPT, cso.REPLAN_PROMPT, cso.RESEARCH_PLAN_PROMPT,
                                      cso.RESEARCH_CP2_PLAN_PROMPT], ids=["plan", "replan", "research", "cp2"])
def test_plan_assigns_abstract_to_writable_worker(template):
    assert "outputs/graphical_abstract.png" in template
    assert "write-capable" in template and "final report step" in template


def test_round_links_reports_without_scientific_body_even_in_json(tmp_path):
    hub = Hub(settings(tmp_path))
    round_request(hub, "paper", "done")
    req = hub.requests["paper"]
    req.update(report="SCIENTIFIC_BODY_SENTINEL", bundle_path=str(tmp_path / "bundle"),
               execution_record="## 실행 기록\nworker completed", result_storage={"status": "published", "repo": "example/results", "branch": "main"})
    req["results"]["a"]["outputs"] = ["outputs/report.html", "outputs/timeline.html", "outputs/trace.txt",
                                           "outputs/pipeline_info/execution_report.html"]
    record = build_record(hub, "paper")
    assert "SCIENTIFIC_BODY_SENTINEL" not in json.dumps(record)
    for text in (render_record(record), _marked_body(record, limit=12000)):
        assert "SCIENTIFIC_BODY_SENTINEL" not in text
        assert "report.md)" in text and "https://github.com/example/results/blob/main/labhq/results/paper/report.md" in text
        assert "[과학 보고서 · 요청 묶음]" not in text
        assert "[과학 보고서 · GitHub 정리본](" in text
        assert "worker completed" in text
        for name in ("report.html", "timeline.html", "trace.txt", "pipeline_info/"):
            assert name in text
        assert "[Nextflow" not in text


def test_audit_bundle_splits_a_legacy_execution_appendix(tmp_path):
    from labhq.evidence.audit import write_bundle

    out = tmp_path / "audit.zip"
    report = {"request_id": "legacy", "files": [], "problems": [], "unreported_outputs": {}}
    request = {"id": "legacy", "report_appendix":
               "## 부록\n\n### 가정\npaired\n\n## 부록: 실행 기록\nlegacy warning",
               "execution_record": "current operation"}
    write_bundle(report, request, out)
    with zipfile.ZipFile(out) as bundle:
        appendix = bundle.read("report_appendix.md").decode()
        operations = bundle.read("execution_record.md").decode()

    assert "paired" in appendix and "legacy warning" not in appendix
    assert "legacy warning" in operations and "current operation" in operations


def test_legacy_round_render_drops_old_report_fields(tmp_path):
    hub = Hub(settings(tmp_path))
    round_request(hub, "legacy", "done")
    record = build_record(hub, "legacy")
    record["result"].update(report="OLD_BODY_SENTINEL", root_cause_summary="OLD_BODY_SENTINEL")
    assert "OLD_BODY_SENTINEL" not in render_record(record)
    assert "OLD_BODY_SENTINEL" not in _marked_body(record, limit=800)


def test_bundle_keeps_science_appendix_and_moves_operations(tmp_path):
    cfg, req, task = pending_bundle_fixture(tmp_path, "paper")
    req.update(report="# 연구 제목\n\n## 요약(Abstract)\nGSE10072와 PMID 12345678을 참고했다.\n\n"
                      "## 서론\n질문\n\n## 방법\n방법\n\n## 결과\n결과\n\n## 논의\n한계\n\n## 결론\n결론\n\n## 참고\n",
               report_appendix="## 부록\n\n### 가정\n설계 가정", execution_record="## 실행 기록\nworker completed")
    built = build_request_bundle(req, cfg, {task["result"]["task_id"]: task})
    root = Path(built["path"])
    appendix = (root / "report_appendix.md").read_text(encoding="utf-8")
    assert "설계 가정" in appendix and "요청 묶음 변환" not in appendix
    operations = (root / "execution_record.md").read_text(encoding="utf-8")
    assert "worker completed" in operations and "요청 묶음 변환" in operations
    from labhq.ro_crate import verify_bundle_copy
    assert not verify_bundle_copy(root)["problems"]
    from labhq.report_pdf import build_report_html
    page, missing = build_report_html(req, root)
    assert not missing and "설계 가정" in page and "worker completed" not in page
    assert "요청 묶음 변환" not in page and "과학 부록" in page
    from labhq.result_storage import draft_documents, editor_prompt
    docs = draft_documents(req, cfg)
    assert "GSE10072" in docs["SOURCES.md"] and "12345678" in docs["SOURCES.md"]
    assert "## 서론" in docs["report.md"] and "## 논의" in docs["report.md"]
    assert "GSE10072" in editor_prompt(docs, "질문")


def test_finish_keeps_review_assumptions_and_sources_but_not_run_details():
    from test_cso import FakeHub
    hub = FakeHub(None)
    hub.commit_terminal = lambda *args: None
    hub.requests["r"]["plan"] = {"steps": [{"id": "A", "agent_id": "worker"}],
                                  "assumptions": [{"choice": "paired", "reason": "same donor"}]}
    report = "# 제목\n\n## 요약(Abstract)\n질문\n\n## 서론\nGSE10072 PMID 12345678\n\n## 방법\n방법\n\n## 결과\n결과\n\n## 논의\n한계\n\n## 결론\n결론\n\n## 참고\n"
    cso.Orchestrator(hub)._finish("r", report, {"A": {"status": "done"}}, True,
                                {"verdict": "accept", "issues": [{"priority": "P2", "problem": "small sample", "request": "limit claim"}]})
    req = hub.requests["r"]
    assert "Step status" not in req["report_appendix"]
    assert "Step status" in req["execution_record"]
    assert "small sample" in req["report_appendix"] and "same donor" in req["report_appendix"]
    assert "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE10072" in req["report"]
    assert "https://pubmed.ncbi.nlm.nih.gov/12345678/" in req["report"]
    headings = [line for line in req["report"].splitlines() if line.startswith("## ")]
    assert headings == ["## 요약(Abstract)", "## 서론", "## 방법", "## 결과", "## 논의", "## 결론", "## 참고"]
