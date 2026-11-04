"""Pipeline diagram, run timeline and execution report of the steps that ran (#756)."""

from copy import deepcopy
from xml.etree import ElementTree

import labhq.result_storage as result_storage
from labhq import run_report
from labhq.portfolio import build_portfolio_files
from labhq.report_contract import FRONT_END, FRONT_START, ReportPackage, build_report_package, with_pipeline_section
from labhq.report_pdf import build_report_html, run_figures
from labhq.settings import Settings

from test_portfolio_edition import portfolio_fixture

T0 = 1_760_000_000.0  # a fixed instant; the expected text uses the same local-time formatter
SVG = "{http://www.w3.org/2000/svg}"


def _texts(svg: bytes | str) -> str:
    root = ElementTree.fromstring(svg)
    return " ".join("".join(node.itertext()) for node in root.iter(f"{SVG}text"))


def _request() -> dict:
    return {
        "id": "req_run",
        "text": "공개 RNA-seq 자료로 발현 차이를 본다.",
        "status": "done",
        "report": "# 발현 비교\n\n## 요약\n\n차등 발현 유전자 12개를 찾았다.\n",
        "plan": {"steps": [
            {"id": "fetch", "agent_id": "data_steward", "instruction": "공개 자료를 받는다.",
             "input_refs": ["/home/alice/raw/GSE1.tsv"]},
            {"id": "nf_rnaseq", "agent_id": "bioinfo-agent", "instruction": "nf-core/rnaseq를 돌린다.",
             "depends_on": ["fetch"]},
            {"id": "qc_check", "agent_id": "qc_reviewer", "instruction": "QC를 검토한다.", "depends_on": ["nf_rnaseq"]},
            {"id": "never", "agent_id": "analyst", "instruction": "추가 분석.", "depends_on": ["qc_check"]},
        ]},
        "results": {
            "fetch": {"task_id": "t1", "agent_id": "data_steward", "ok": True, "cost_usd": 0.42, "cost_known": True,
                      "outputs": ["outputs/GSE1_counts.tsv"],
                      "provenance": {"engine": "claude_code", "runs": {
                          "t1": {"started_at": T0, "ended_at": T0 + 125, "engine": "claude_code", "model": "sonnet"}}}},
            "nf_rnaseq": {"task_id": "t2", "agent_id": "bioinfo-agent", "ok": True, "cost_usd": None,
                          "cost_known": False,
                          "outputs": ["outputs/pipeline_info/execution_report_x.html",
                                      "outputs/pipeline_info/execution_timeline_x.html",
                                      "outputs/pipeline_info/execution_trace_x.txt"],
                          "provenance": {"runs": {"t2": {"started_at": T0 + 130}}}},
            "qc_check": {"task_id": "t3", "agent_id": "qc_reviewer", "ok": False, "error": "QC 기준 미달"},
            "never": {"task_id": "", "agent_id": "analyst", "ok": False, "error": "unmet dependencies"},
        },
        "cost_usd": 0.42,
    }


def _public_assets() -> dict[str, str]:
    return {
        "steps/nf_rnaseq/outputs/pipeline_info/execution_report_x.html": "<html>Nextflow report</html>",
        "steps/nf_rnaseq/outputs/pipeline_info/execution_timeline_x.html": "<html>timeline</html>",
        "steps/nf_rnaseq/outputs/pipeline_info/execution_trace_x.txt":
            "task_id\tname\tworkdir\n1\tFASTQC\t/home/alice/nxf/work/ab/cdef\n",
        "steps/nf_rnaseq/outputs/pipeline_info/pipeline_dag_x.html": "<html>dag</html>",
        "steps/nf_rnaseq/outputs/pipeline_info/params_x.json": "{}",
    }


def test_nextflow_kind_reads_nf_core_and_labelled_names_only_under_pipeline_info():
    assert run_report.nextflow_kind("steps/a/outputs/pipeline_info/execution_report_2026-10-19.html") == "report"
    assert run_report.nextflow_kind("outputs/pipeline_info/ds1-timeline-2026.html") == "timeline"
    assert run_report.nextflow_kind("outputs/pipeline_info/execution_trace_1.txt") == "trace"
    assert run_report.nextflow_kind("outputs/pipeline_info/pipeline_dag_1.svg") == "dag"
    assert run_report.nextflow_kind("outputs/pipeline_info/trace.txt") == "trace"
    assert run_report.nextflow_kind("outputs/pipeline_info/params_1.json") is None
    assert run_report.nextflow_kind("outputs/pipeline_info/execution_report_1.txt") is None
    assert run_report.nextflow_kind("outputs/report.html") is None


def test_only_executed_steps_are_drawn_and_missing_records_stay_missing():
    req = _request()
    summary, files = run_report.build(req, hidden=lambda _sid: False, scrub=lambda text: text)

    assert [step.step_id for step in summary.steps] == ["fetch", "nf_rnaseq", "qc_check"]
    assert summary.not_run == ["never"]
    diagram = _texts(files[run_report.DIAGRAM_FILE])
    assert "1. fetch" in diagram and "2. nf_rnaseq" in diagram and "3. qc_check" in diagram
    assert "never" not in diagram and "실행하지 않은 계획 단계 1개" in diagram
    assert "QC" in diagram and "선행: fetch" in diagram and "GSE1_counts.tsv" in diagram
    for body in (files[run_report.DIAGRAM_FILE], files[run_report.TIMELINE_FILE]):
        assert b"<script" not in body and b"xml:lang=\"ko\"" in body and b"<text" in body

    timeline = files[run_report.TIMELINE_FILE].decode("utf-8")
    assert timeline.count('rx="4"') == 1  # one bar: only fetch has both start and end
    assert "2분 5초 · $0.42" in timeline
    assert "종료 기록 없음 · 시작 " + run_report.clock(T0 + 130) in timeline
    assert "시각 기록 없음" in timeline

    report = files[run_report.REPORT_FILE]
    rows = {line.split(" | ")[1]: line for line in report.splitlines() if line.startswith("| ") and " | " in line}
    assert run_report.clock(T0) in rows["fetch"] and run_report.clock(T0 + 125) in rows["fetch"]
    assert "| 2분 5초 | $0.42 |" in rows["fetch"]
    assert "| 기록 없음 | 종료 기록 없음 | 기록 없음 |" in rows["nf_rnaseq"]
    assert "| 실패 | 기록 없음 | 기록 없음 | 기록 없음 | 기록 없음 |" in rows["qc_check"]
    assert "$0.00" not in report and "never" in report.split("## 실행하지 않은 계획 단계", 1)[1]
    assert "기록된 단계 비용 합계: $0.42 (비용 기록 없는 단계 2개 제외)" in report


def test_hidden_steps_show_neither_name_inputs_nor_outputs_and_private_paths_are_scrubbed():
    req = _request()
    summary, files = run_report.build(
        req, hidden=lambda sid: sid == "fetch", scrub=lambda text: text.replace("/home/alice", "<local-path>"))
    diagram = _texts(files[run_report.DIAGRAM_FILE])
    assert "내부 자료 단계 1" in diagram and "fetch" not in diagram.replace("선행: 내부 자료 단계 1", "")
    assert "GSE1_counts.tsv" not in diagram and "내부 자료라 표시하지 않음" in diagram
    assert "/home/alice" not in diagram + files[run_report.REPORT_FILE]
    assert summary.steps[1].depends == ["내부 자료 단계 1"]


def test_edition_collects_nextflow_records_and_links_them_from_readme_report_and_run(tmp_path):
    settings, _request_unused, bundle, _assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    settings.policy.data_zones = []
    req = _request()
    files = build_portfolio_files(req, settings, _public_assets(), images, decisions=decisions, tasks=tasks,
                                  bundle=bundle)

    nextflow = sorted(name for name in files if "/nextflow/" in name)
    assert nextflow == ["run/nextflow/execution_report_x.html", "run/nextflow/execution_timeline_x.html",
                        "run/nextflow/execution_trace_x.txt", "run/nextflow/pipeline_dag_x.html"]
    assert "/home/alice" not in files["run/nextflow/execution_trace_x.txt"]
    for name in (run_report.DIAGRAM_FILE, run_report.TIMELINE_FILE):
        assert isinstance(files[name], bytes) and b"/home/alice" not in files[name]

    readme = files["README.md"]
    answer = readme.index("## 답")
    section = readme.index("## 분석 파이프라인")
    assert answer < section < readme.index("## 한계")
    block = readme[section:readme.index("## 한계")]
    for target in ("run/pipeline_diagram.svg", "run/run_timeline.svg", "run/run_report.md",
                   "run/nextflow/execution_report_x.html", "run/nextflow/pipeline_dag_x.html"):
        assert f"({target})" in block
    assert "![" not in block  # the graphical abstract stays the README's first picture (#608)
    assert "[실행 보고서](run/run_report.md)" in readme.split("## 읽는 순서", 1)[1]
    assert "(../run/pipeline_diagram.svg)" in files["report/report.md"]

    report = files[run_report.REPORT_FILE]
    assert "![분석 파이프라인 모식도](pipeline_diagram.svg)" in report
    assert "사용 · report·timeline·trace·dag" in report
    assert "[trace](nextflow/execution_trace_x.txt)" in report
    assert "쓰지 않음(산출 기록 기준)" in report  # fetch ran without Nextflow
    assert "단계별 시작·종료·비용: [실행 보고서](run_report.md)" in files["run/costs.md"]


def test_edition_without_nextflow_says_so_and_withholds_a_record_with_a_private_path(tmp_path):
    settings, _request_unused, bundle, _assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    settings.policy.data_zones = []
    req = _request()
    req["results"]["nf_rnaseq"]["outputs"] = []
    files = build_portfolio_files(req, settings, {}, images, decisions=decisions, tasks=tasks, bundle=bundle)
    assert "Nextflow 보고서: 이 요청의 실행 기록에 없습니다" in files["README.md"]
    assert "Nextflow 산출 기록이 없습니다" in files[run_report.REPORT_FILE]
    assert "기록 없음 · Nextflow 사용 여부 미확인" in files[run_report.REPORT_FILE]  # bioinfo-agent without records

    blocked = {"steps/nf_rnaseq/outputs/pipeline_info/execution_report_x.html":
               "<html>launch dir .labhq/runs/req_run</html>"}
    files = build_portfolio_files(deepcopy(req), settings, blocked, images, decisions=decisions, tasks=tasks,
                                  bundle=bundle)
    assert not any("/nextflow/" in name for name in files)
    assert "report — 개인 경로를 바꾸지 못해 넣지 않음" in files[run_report.REPORT_FILE]


def test_internal_requests_draw_generic_steps_and_keep_nextflow_records_out(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    request = deepcopy(request)
    request["results"]["analysis"].update(task_id="t1", agent_id="analyst")
    files = build_portfolio_files(request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)
    diagram = _texts(files[run_report.DIAGRAM_FILE])
    assert "내부 자료 단계 1" in diagram and "analysis" not in diagram
    assert not any("/nextflow/" in name for name in files)
    assert "내부 자료 단계의 Nextflow 기록이라 게시하지 않음" in files[run_report.REPORT_FILE]


def test_pdf_embeds_the_run_pictures_and_names_nextflow_records_without_links():
    req = _request()
    summary, drawn = run_figures(req, Settings())
    assert set(drawn) == {run_report.DIAGRAM_FILE, run_report.TIMELINE_FILE}
    page, missing = build_report_html(req, None, report_text=req["report"], run=(summary, drawn))
    assert missing == []
    assert 'alt="분석 파이프라인 모식도"' in page and 'alt="단계별 실행 타임라인"' in page
    assert "nf_rnaseq report · timeline · trace" in page and "nextflow/" not in page


def test_a_stored_front_gets_the_pipeline_section_before_its_limits():
    req = _request()
    package = build_report_package(req, req["report"])
    stored = f"# 발현 비교\n\n{FRONT_START}\n\n## 질문\n\nq\n\n## 답\n\na\n\n## 한계\n\nl\n\n{FRONT_END}\n\n본문\n"
    assert with_pipeline_section(stored, package) == stored  # nothing drawn, nothing added
    package.pipeline = ReportPackage.from_dict({**package.to_dict(), "pipeline": [
        {"label": "분석 파이프라인 모식도", "path": "run/pipeline_diagram.svg"}]}).pipeline
    shown = with_pipeline_section(stored, package, images=True)
    assert shown.index("## 답") < shown.index("![분석 파이프라인 모식도](run/pipeline_diagram.svg)") < shown.index("## 한계")
    assert with_pipeline_section(shown, package, images=True) == shown


def test_nextflow_reports_have_their_own_size_cap_in_edition_assets(tmp_path, monkeypatch):
    big = tmp_path / "execution_report_x.html"
    big.write_bytes(b"<html>" + b"a" * 2_500_000 + b"</html>")
    text = tmp_path / "notes.txt"
    text.write_bytes(b"b" * 1_500_000)
    candidates = {"steps/nf/outputs/pipeline_info/execution_report_x.html": big, "steps/nf/outputs/notes.txt": text}
    monkeypatch.setattr(result_storage, "edition_candidates", lambda _req, _settings: candidates)
    assets, _images = result_storage.edition_assets({"id": "req_run"}, Settings())
    assert list(assets) == ["steps/nf/outputs/pipeline_info/execution_report_x.html"]
