import py_compile
import re
from pathlib import Path

import pytest

import labhq.portfolio as portfolio
from labhq.evidence.report_check import claim_evidence_section
from labhq.report_markdown import rendered_markers
from labhq.korean_qc import check_document
from labhq.portfolio import build_portfolio_files, scan_publish_files
from labhq.result_storage import draft_documents
from labhq.settings import DataZone, Settings


PAPER = """# 공개 오믹스 대체 분석

## 요약(Abstract)

직업 자료가 없어 운동성 비교를 대신 분석했다. 유전자 16,242개와 CpG 24,074개 가운데 FDR 0.05를 넘은 항목은 각각 0개였다.

## 서론

직업과 컴퓨터 사용을 기록한 공개 자료는 찾지 못했다.

## 방법

공개 발현·메틸화 자료를 비교하고 BH-FDR 0.05를 적용했다.

## 결과

차등 발현 유전자 16,242개와 CpG 24,074개를 검정했다. FDR 0.05 기준을 통과한 항목은 모두 0개로 통계적으로 유의하지 않았다.

## 논의

이 대체 분석은 직업 효과를 직접 검정하지 못한다.

## 결론

직업 효과는 판단할 수 없고 대체 비교에서는 통계적으로 유의한 차이가 없었다.

## 참고

PMID:21674046 · DOI:10.1371/journal.pone.0020280 · GEO:GSE26982

## 부록

작은 집단을 비교했다.
"""


def test_edition_repairs_only_missing_final_note_and_records_it():
    name = "steps/clinical/outputs/access_ledger.tsv"
    original = "candidate\taccess\t비고\nA\tunavailable\nB\tfulltext\tchecked\n"
    assert not portfolio._table_metadata(name, original)[2]
    request = {"id": "req_notes", "text": "문헌 접근 기록", "report": "# 결과\n\n접근 상태를 기록했다."}
    assets = {name: original}

    files = build_portfolio_files(request, Settings(), assets, {})

    assert assets[name] == original
    assert files["tables/access_ledger.tsv"] == original.replace("A\tunavailable\n", "A\tunavailable\t\n")
    assert portfolio._table_metadata(name, files["tables/access_ledger.tsv"])[2]
    assert "tables/access_ledger.tsv" in files["docs/reference/table_repairs.md"]
    assert "헤더=1" in files["docs/reference/table_repairs.md"]
    assert "[표 형식 보정 기록]" in files["README.md"]


@pytest.mark.parametrize("body", [
    "candidate\tresult\nA\n",  # Missing result is never inferred.
    "candidate\tresult\tnotes\nA\n",  # More than a final note is missing.
    "candidate\tnotes\nA\tB\tC\n",  # Extra fields are never discarded.
    "candidate\tnotes\nA\nB\tC\tD\n",  # Do not partially repair a broken table.
])
def test_edition_keeps_blocking_other_table_shape_errors(body):
    name = "steps/clinical/outputs/result.tsv"
    assert portfolio._edition_note_cells(name, body) == (body, [])
    request = {"id": "req_shape", "text": "표 확인", "report": "# 결과\n\n[결과 표](steps/clinical/outputs/result.tsv)"}
    with pytest.raises(portfolio.EditionCheckError, match="table_shape"):
        build_portfolio_files(request, Settings(), {name: body}, {})


def test_evidence_technical_identifiers_are_literal_in_reader_edition():
    # Attempt 17: tool names in detail and a ChEMBL query key in source text,
    # not broken emphasis in the authored scientific report.
    row = dict.fromkeys(("step_id", "claim_id", "claim_status", "claim", "evidence_id", "relation",
                         "evidence_status", "detail", "source", "locator", "access", "methods",
                         "qc", "supplement", "lineage"), "unrecorded")
    row.update(detail="No tools: mcp__labhq_annot__vep / mcp__labhq_annot__clinvar",
               source="https://example.org/molecule.json?molecule_chembl_id__in=A,B")
    section = claim_evidence_section([row])
    assert rendered_markers(section) == []
    assert "` mcp__labhq_annot__vep `" in section
    assert row["source"] in section
    files = build_portfolio_files({"id": "req_literal", "text": "근거 확인", "report": "# 결과\n\n" + section}, Settings(), {}, {})
    assert not scan_publish_files(files)
    assert rendered_markers("**(broken)**text")  # The actual render gate remains strict.


def test_cited_technical_source_is_literal_in_publication_footnotes():
    source = "https://example.org/molecule.json?molecule_chembl_id__in=A,B"
    ledger = {
        "claims": [{"id": "c1", "statement": "Source checked", "status": "supported"}],
        "evidence": [{"id": "e1", "status": "observed", "source": {"uri": source}}],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}],
    }
    request = {"id": "req_footnote", "text": "근거 확인", "report": "Source checked.",
               "results": {"s1": {"structured": ledger}}}
    assets = {"steps/report/outputs/report/final_report.md": PAPER + "\nSource checked [[claim:s1/c1]].\n"}
    files = build_portfolio_files(request, Settings(), assets, {})
    assert source in files["report/report.md"]
    assert "[^" in files["report/report.md"]
    assert rendered_markers(files["report/report.md"]) == []
    assert not scan_publish_files(files)


def test_edition_rebuilds_existing_generated_evidence_table_from_ledger():
    source = "https://example.org/query?molecule_chembl_id__in=A,B"
    ledger = {
        "claims": [{"id": "c1", "statement": "Source checked", "status": "supported"}],
        "evidence": [{"id": "e1", "status": "observed", "source": {"uri": source}}],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}],
    }
    request = {"id": "req_existing", "text": "근거 확인", "results": {"s1": {"structured": ledger}}}
    rows = portfolio._source_evidence_rows(request, Settings())
    stale = claim_evidence_section(rows).replace("` ", "").replace(" `", "")
    original = PAPER + "\n" + stale + "\n## 후속 본문\n\nPreserve this paragraph.\n"
    assert rendered_markers(stale)
    request["report"] = original
    files = build_portfolio_files(request, Settings(), {}, {})
    report = files["report/report.md"]
    assert report.count("## " + portfolio.SOURCE_EVIDENCE_TITLE) == 1
    assert "Preserve this paragraph." in report
    assert rendered_markers(report) == []
    assert request["report"] == original


def test_compatibility_report_converts_claims_before_publication_scan():
    request = {"report": "Result [[claim:s1/c1]].\n\n`[[claim:s1/example]]`\n"}

    docs = draft_documents(request, Settings())

    assert "Result [근거 1]." in docs["report.md"]
    assert "근거 원장 `s1/c1`" in docs["report.md"]
    assert "`[[claim:s1/example]]`" in docs["report.md"]
    assert not scan_publish_files({name: docs[name] for name in ("README.md", "report.md")})
    assert request["report"].startswith("Result [[claim:s1/c1]].")


@pytest.mark.parametrize("marker", ["**(broken)**text", "[[placeholder]]"])
def test_compatibility_report_still_blocks_render_errors(marker):
    docs = draft_documents({"report": f"Result [[claim:s1/c1]].\n\n{marker}"}, Settings())

    assert any(issue["kind"] == "markdown_render" and issue["path"] == "report.md"
               for issue in scan_publish_files(docs))


def portfolio_fixture(tmp_path: Path):
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "runs")
    internal = tmp_path / "internal data"
    internal.mkdir()
    settings.policy.data_zones = [DataZone(path=str(internal), level="internal")]
    bundle = Path(settings.runner.workspace_root) / "requests" / "req_portfolio"
    bundle.mkdir(parents=True)
    (bundle / "execution_record.md").write_text(
        "# 실행 기록\n\n입력 C:/Users/alice/private.tsv\n", encoding="utf-8")
    assets = {
        "steps/report/outputs/report/final_report.md": PAPER,
        "steps/analysis/outputs/scripts/analyze.py": "print('ok')\n",
        "steps/analysis/outputs/stats/result_summary.tsv": "tested\tpassed\n16242\t0\n",
        "steps/analysis/outputs/stats/by_job.tsv": "job\tcount\nA\t5\n",
        "steps/analysis/outputs/stats/patient_summary.tsv": "patient_id\tage\nP001\t42\nP002\t53\n",
        "steps/pipeline/outputs/pipeline_info/report.html": "<html>Nextflow report</html>",
        "steps/pipeline/outputs/pipeline_info/timeline.html": "<html>timeline</html>",
        "steps/pipeline/outputs/pipeline_info/trace.txt": "task\tstatus\na\tCOMPLETED\n",
        "steps/lit/outputs/literature/article.md": "third-party full text must stay local",
    }
    images = {
        "steps/report/outputs/graphical_abstract.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>summary</title>'
            b'<desc>question data method result conclusion</desc><text>0</text></svg>'
        ),
        "steps/analysis/outputs/figures/volcano.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>volcano</title>'
            b'<desc>FDR result</desc><text>FDR 0.05: 0</text></svg>'
        ),
    }
    request = {
        "id": "req_portfolio",
        "text": f"공개 오믹스와 {internal / 'cohort.tsv'}를 함께 검토해 달라.",
        "status": "done",
        "outcome": "research_review_revise",
        "report": "# 단계 기록 원고\n\n분석 작업을 마쳤다.",
        "project_dirs": [str(internal)],
        "plan": {"steps": [
            {"id": "analysis", "instruction": "발현과 메틸화를 분석한다.",
             "output_types": [{"name": "outputs/stats/result_summary.tsv", "data_type": "table"}]},
            {"id": "report", "instruction": "논문 형식 보고서를 쓴다."},
        ]},
        "results": {
            "analysis": {"text": "유전자 16,242개와 CpG 24,074개에서 FDR 0.05 통과 0개였다."},
            "report": {"text": "논문 형식 보고서를 작성했다."},
        },
        "clarifications": [{"question": "범위는?", "answer": "공개 자료와 내부 집계"}],
        "pi_notes": [{"at": 1, "text": "유의하지 않은 결과도 숫자와 함께 쓴다."}],
        "review": {"verdict": "revise", "issues": [{
            "priority": "P1", "problem": "직업 효과처럼 읽힌다", "request": "대체 분석임을 밝힌다",
        }]},
        "references": [
            {"kind": "pmid", "value": "21674046"},
            {"kind": "doi", "value": "10.1371/journal.pone.0020280"},
            {"kind": "url", "value": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE26982"},
        ],
        "cost_usd": 1.25,
    }
    decisions = [
        {"approval": {"request_id": "req_portfolio", "kind": "research_plan"},
         "approved": True, "note": "CP1 원문: 계획대로 진행"},
        {"approval": {"request_id": "req_portfolio", "kind": "research_evidence"},
         "approved": True, "note": "CP2 원문: 0개도 결과로 기록"},
        {"approval": {"request_id": "req_portfolio", "kind": "research_continue"},
         "approved": False, "note": "다음 차수 없이 끝낸다"},
    ]
    tasks = [{"request_id": "req_portfolio", "agent_id": "analyst", "completed": True}]
    return settings, request, bundle, assets, images, decisions, tasks


def test_fixed_request_bundle_builds_the_portfolio_contract(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle,
        run_documents={"run/progress.md": "# 진행 기록\n\n완료\n",
                       "run/reproduce.md": "# 재현 방법\n\npython analysis.py\n"})

    required = {
        "README.md", "report/report.md", "run/requests.md", "run/decisions.md",
        "run/execution_record.md", "run/costs.md", "run/progress.md", "run/reproduce.md",
        "code/analysis/outputs/scripts/analyze.py",
        "figures/graphical_abstract.svg", "figures/volcano.svg",
        "tables/result_summary.tsv", "REFERENCES.md", "DATA_AND_LICENSES.md",
    }
    assert required <= files.keys()
    assert files["report/report.md"].startswith("# 공개 오믹스 대체 분석")
    assert "## 정정" not in files["report/report.md"]
    assert "## 검토 종료 때 남은 정정" in files["run/revision_history.md"]
    assert "CP1 원문: 계획대로 진행" in files["run/decisions.md"]
    assert "CP2 원문: 0개도 결과로 기록" in files["run/decisions.md"]
    assert "다음 차수 없이 끝낸다" in files["run/decisions.md"]
    assert "C:/Users" not in files["run/execution_record.md"]

    readme = files["README.md"]
    assert "| 단계 | 자료 | 결과 |" in readme
    assert "16,242" in readme and "0개" in readme
    assert "통계적으로 유의하지" in readme
    for target in ("report/report.md", "run/reproduce.md", "DATA_AND_LICENSES.md"):
        assert target in readme
    assert "by_job.tsv" not in files and "patient_summary.tsv" not in files
    assert "뺀 표: by_job.tsv" in readme and "뺀 표: patient_summary.tsv" in readme
    assert not any("outputs/literature" in name or "article.md" in name for name in files)
    assert not any(name.startswith("run/nextflow/") for name in files)
    assert check_document(readme)["status"] == "PASS"
    assert check_document(files["report/report.md"])["status"] == "PASS"


def test_published_python_uses_input_setting_without_touching_archive_members(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/analysis/outputs/scripts/build_crosswalk.py"] = (
        "from pathlib import Path\n"
        "archive_member = '/Occupation Activities.txt'\n"
        "source = Path('C:/Users/alice/private/cohort.tsv')\n"
        "print(archive_member, source)\n"
    )

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    name = next(name for name in files if name.endswith("build_crosswalk.py"))
    code = files[name]
    assert "/Occupation Activities.txt" in code
    assert "Activities.txt" in code and "LABHQ_INPUT_COHORT_TSV" in code
    assert "C:/Users/alice" not in code and "<local-path>" not in code
    path = tmp_path / "published.py"
    path.write_text(code, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)


def test_published_python_gives_distinct_paths_distinct_stable_input_settings(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/analysis/outputs/scripts/compare_inputs.py"] = (
        "from pathlib import Path\n"
        "left = Path('/home/a/data.csv')\n"
        "right = Path('/srv/b/data.csv')\n"
        "left_again = Path('/home/a/data.csv')\n"
    )

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    name = next(name for name in files if name.endswith("compare_inputs.py"))
    keys = re.findall(r'LABHQ_INPUT_DATA_CSV_[A-F0-9]{8}', files[name])
    assert len(set(keys)) == 2
    assert keys[0] == keys[2]


def test_published_python_rewrites_f_raw_and_joined_paths_or_marks_manual_review(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/analysis/outputs/scripts/path_forms.py"] = (
        "user = 'alice'\n"
        "value = 1\n"
        "dynamic = f'C:/Users/{user}/data.csv'\n"
        "raw = r'C:\\\\Users\\\\alice\\\\raw.csv'\n"
        "joined = ('C:/Users/' 'alice/joined.csv')\n"
        "formatted = f'{value:C:/Users/alice/private.txt}'\n"
    )

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle,
        run_documents={"run/reproduce.md": "# 재현 방법\n\n## 입력 설정\n\n- 없음\n\n## 예상 출력\n"})

    name = next(name for name in files if name.endswith("path_forms.py"))
    code = files[name]
    assert "C:/Users" not in code and r"C:\\Users" not in code
    assert "LABHQ_INPUT_USERS_" in code
    assert "LABHQ_INPUT_RAW_CSV_" in code
    assert "LABHQ_INPUT_JOINED_CSV_" in code
    assert "# LABHQ 수동 확인 필요:" in code
    assert "수동 확인 필요" in files["run/reproduce.md"]
    path = tmp_path / "published_forms.py"
    path.write_text(code, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)


def test_portfolio_reproduce_links_canonical_code_and_lock_paths(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    request["plan"]["steps"][0]["outputs"] = ["outputs/scripts/analyze.py"]
    assets["steps/env/outputs/env/requirements.lock.txt"] = "pandas==2.2.2\n"
    docs = draft_documents(
        request, settings, assets=assets,
        environment_records={"env": {"python": "3.12"}},
        bundle_readme=("## 단계 순서\n\n- `analysis` (의존: 없음)\n\n## 다시 실행\n\n"
                       "- `cd steps/analysis && python outputs/scripts/analyze.py`\n"),
    )

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle,
        run_documents={"run/reproduce.md": docs["REPRODUCE.md"],
                       "run/task_record.md": docs["TASK_RECORD.md"]},
    )

    assert files["run/environment/requirements.lock.txt"] == "pandas==2.2.2\n"
    reproduce = files["run/reproduce.md"]
    assert "code/analysis/outputs/scripts/analyze.py" in files
    assert "cd code/analysis && python outputs/scripts/analyze.py" in reproduce
    assert "`analysis`: `code/analysis`" in reproduce
    assert "cd steps/analysis" not in reproduce
    assert "](environment/requirements.lock.txt)" in reproduce
    assert "run/task_record.md" in files["README.md"]


def test_publish_scan_catches_credentials_paths_signed_urls_and_small_cells():
    files = {
        "README.md": "token=ghp_" + "A" * 24,
        "run/requests.md": "C:/Users/alice/private.tsv https://x.test/a?X-Amz-Signature=secret",
        "tables/private.tsv": "group\tcount\nA\t9\n",
    }
    kinds = {issue["kind"] for issue in scan_publish_files(files, internal=True)}
    assert {"secret", "local_path", "signed_url", "small_cell"} <= kinds


@pytest.mark.parametrize("name", ["README.md", "report/report.md", "outputs/RESULT.md"])
def test_publish_scan_blocks_visible_markdown_markers_before_writing(name):
    from labhq.portfolio import check_failure

    issues = scan_publish_files({name: "첫 줄\n\n**(강조 문장)**이 깨집니다."})
    problem = next(issue for issue in issues if issue["kind"] == "markdown_render")
    assert problem["path"] == name
    assert f"{name} 3줄" in str(check_failure(issues))
    assert "문장부호" in str(check_failure(issues))
    assert not scan_publish_files({name: "```md\n** __ [[ 코드\n```\n"})


def test_public_request_keeps_an_undeclared_row_table(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    settings.policy.data_zones = []
    request["project_dirs"] = []
    request["text"] = "공개 자료를 정리해 달라."

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    assert files["tables/patient_summary.tsv"] == "patient_id\tage\nP001\t42\nP002\t53\n"


def test_report_figure_links_are_relative_to_the_nested_report(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/report/outputs/report/final_report.md"] = PAPER.replace(
        "## 결과\n\n", "## 결과\n\n![화산 그림](work-analysis/outputs/figures/volcano.svg)\n\n")
    request["results"]["analysis"].update(
        workdir="work-analysis", outputs=["outputs/figures/volcano.svg"])

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    assert "![화산 그림](../figures/volcano.svg)" in files["report/report.md"]
    assert portfolio.broken_markdown_links(files) == []


def test_markdown_link_check_covers_every_edition_document():
    files = {
        "README.md": "[보고서](report/report.md)\n",
        "report/report.md": "![그림](../figures/missing.svg)\n",
        "run/requests.md": "[표](../tables/missing.tsv)\n",
    }

    assert portfolio.broken_markdown_links(files) == [
        {"document": "report/report.md", "target": "../figures/missing.svg"},
        {"document": "run/requests.md", "target": "../tables/missing.tsv"},
    ]


def test_duplicate_figure_names_keep_their_source_to_target_links(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/report/outputs/report/final_report.md"] = PAPER.replace(
        "## 결과\n\n",
        "## 결과\n\n![첫 그림](wd1/outputs/plot.svg)\n\n![둘째 그림](wd2/outputs/plot.svg)\n\n",
    )
    images = {
        "wd1/outputs/plot.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>one</text></svg>',
        "wd2/outputs/plot.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>two</text></svg>',
    }

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    report = files["report/report.md"]
    assert "![첫 그림](../figures/plot.svg)" in report
    assert "![둘째 그림](../figures/outputs-plot.svg)" in report
    assert files["figures/plot.svg"] != files["figures/outputs-plot.svg"]


def test_reference_style_links_use_the_same_source_mapping(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/report/outputs/report/final_report.md"] = PAPER.replace(
        "## 결과\n\n", "## 결과\n\n![화산 그림][plot]\n\n[plot]: wd1/outputs/plot.svg\n\n")
    images = {
        "wd1/outputs/plot.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>plot</text></svg>',
    }

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    assert "[plot]: ../figures/plot.svg" in files["report/report.md"]
    assert portfolio.broken_markdown_links(files) == []
    assert portfolio.broken_markdown_links({
        "report/report.md": "![빠진 그림][plot]\n\n[plot]: wd1/outputs/missing.svg\n",
    }) == [{"document": "report/report.md", "target": "wd1/outputs/missing.svg"}]


def test_png_graphical_abstract_gets_a_generated_svg_fallback(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    images.pop("steps/report/outputs/graphical_abstract.svg")
    images["steps/report/outputs/graphical_abstract.png"] = b"synthetic-png"

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    assert files["figures/graphical_abstract.svg"].startswith(b"<svg")
    assert files["figures/graphical_abstract.svg"] != b"synthetic-png"


def test_duplicate_nextflow_outputs_keep_each_steps_provenance(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    settings.policy.data_zones = []
    request["project_dirs"] = []
    request["text"] = "공개 자료를 정리해 달라."
    assets.update({
        "steps/pipeline_a/outputs/pipeline_info/report.html": "<html>pipeline A</html>",
        "steps/pipeline_b/outputs/pipeline_info/report.html": "<html>pipeline B</html>",
    })

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    reports = {name: body for name, body in files.items()
               if name.startswith("run/nextflow/") and name.endswith("report.html")}
    assert len(reports) == 3
    assert set(reports.values()) == {
        "<html>Nextflow report</html>", "<html>pipeline A</html>", "<html>pipeline B</html>",
    }


REPRODUCE_TEXT = """# 재현 방법

## 입력 설정

- 별도 로컬 입력 경로 설정 없음

## 실행 순서와 명령

- `s1` (의존: 없음)
- `s2` (의존: s1)
- `s3` (의존: s2)

- 먼저 `python link_inputs.py`: 단계 폴더의 `inputs/<앞 단계>`를 `steps/<앞 단계>/outputs`로 잇습니다
- `cd steps/s1 && python outputs/scripts/count.py`
- `cd steps/s2 && python outputs/scripts/plot.py`
- `cd steps/s3 && python outputs/scripts/qc_recheck.py`

## 예상 출력

| 단계 | 출력 |
|---|---|

## 재현 가능 범위

### 공개 자료로 다시 만들 수 있는 것

- s1, s2, s3

### 접근 제한 자료가 필요한 것

- 없음

원자료와 접근 자격은 정리본에 포함하지 않습니다.
"""


def test_reproduce_lists_only_what_the_edition_publishes_and_says_what_is_missing():
    """#792: the bundle's command list named every copied script, the edition publishes the reported ones."""
    from labhq.portfolio import _rewrite_reproduce_commands

    source_targets = {"steps/s1/outputs/scripts/count.py": "code/s1/outputs/scripts/count.py",
                      "steps/s2/outputs/scripts/plot.py": "code/s2/outputs/scripts/plot.py"}
    files = {"code/s1/outputs/scripts/count.py": "print('self contained')\n",
             "code/s2/outputs/scripts/plot.py": "from pathlib import Path\nrows = Path('inputs/s1/counts.tsv').read_text()\n"}
    text = _rewrite_reproduce_commands(REPRODUCE_TEXT, source_targets, files)

    assert "cd code/s1 && python outputs/scripts/count.py" in text  # published: kept in its published folder
    assert "cd steps/" not in text and "link_inputs.py" not in text  # nothing to run there, no linker in the edition
    assert "## 공개본과 실행본의 차이" in text
    assert "정리본에 싣지 않은 단계 스크립트" in text and "- `s3`: `qc_recheck.py`" in text
    assert "작업공간 경로(`manifest.json`, `inputs/<앞 단계>/…`, 단계가 선언한 `outputs/…`)를 씁니다" in text
    assert "- `s2`: `outputs/scripts/plot.py`" in text
    assert "### 공개 자료로 다시 만들 수 있는 것\n\n- s1\n" in text  # only the self-contained published step
    assert "### 정리본만으로는 다시 만들 수 없는 것 (비공개 요청 묶음의 작업공간이 필요)\n\n- s2, s3\n" in text


def test_reproduce_without_missing_scripts_is_left_as_it_was():
    from labhq.portfolio import _rewrite_reproduce_commands

    text = REPRODUCE_TEXT.replace("- s1, s2, s3", "- s1").replace(
        "- `cd steps/s2 && python outputs/scripts/plot.py`\n", "").replace(
        "- `cd steps/s3 && python outputs/scripts/qc_recheck.py`\n", "").replace(
        "- 먼저 `python link_inputs.py`: 단계 폴더의 `inputs/<앞 단계>`를 `steps/<앞 단계>/outputs`로 잇습니다\n", "")
    source_targets = {"steps/s1/outputs/scripts/count.py": "code/s1/outputs/scripts/count.py"}
    files = {"code/s1/outputs/scripts/count.py": "print('self contained')\n"}
    rewritten = _rewrite_reproduce_commands(text, source_targets, files)

    assert "공개본과 실행본의 차이" not in rewritten and "정리본만으로는" not in rewritten
    assert "### 공개 자료로 다시 만들 수 있는 것\n\n- s1\n" in rewritten


def test_a_step_with_a_script_left_out_is_not_listed_as_re_creatable():
    """Review P1: one published and one withheld script make the step partly published."""
    from labhq.portfolio import _rewrite_reproduce_commands

    text = REPRODUCE_TEXT.replace(
        "- `cd steps/s3 && python outputs/scripts/qc_recheck.py`",
        "- `cd steps/s1 && python outputs/scripts/extra_check.py`")
    source_targets = {"steps/s1/outputs/scripts/count.py": "code/s1/outputs/scripts/count.py"}
    files = {"code/s1/outputs/scripts/count.py": "print('self contained')\n"}
    rewritten = _rewrite_reproduce_commands(text, source_targets, files)
    assert "- `s1`: `extra_check.py`" in rewritten
    assert "### 공개 자료로 다시 만들 수 있는 것\n\n- 없음\n" in rewritten
    assert "s1" in rewritten.split("### 정리본만으로는")[1]


@pytest.mark.parametrize("code", [
    "rows = Path('inputs') / 's1' / 'table.tsv'\n",
    "rows = os.path.join('inputs', step, 'table.tsv')\n",
    'path = "inputs/s1/table.tsv"\n',
    "# shell: cat inputs/s1/table.tsv\n",
    "meta = json.load(open('manifest.json'))\n",
    'frame = pd.read_csv(r"inputs\s1\table.tsv")' + "\n",
    'open("inputs\\s1\\table.tsv")' + "\n",
    "Path('inputs').joinpath('s1', 'table.tsv')\n",
    "subprocess.run(['cat', 'inputs/s1/table.tsv'])\n",
    "rows = open('outputs/reference/map.tsv')\n",
])
def test_composed_workspace_input_paths_are_detected(code):
    from labhq.portfolio import _WORKSPACE_INPUT

    assert _WORKSPACE_INPUT.search(code)


def test_a_script_without_workspace_paths_is_not_flagged():
    from labhq.portfolio import _WORKSPACE_INPUT

    assert not _WORKSPACE_INPUT.search("import pandas as pd\nframe = pd.read_csv(sys.argv[1])\nprint(len(frame))\n")


def test_a_windows_separator_literal_is_not_taken_for_a_private_path(tmp_path):
    """#792: str(p).replace("\\", "/") is code, not a UNC path; it used to be commented and turned into <local-path>."""
    from labhq.portfolio import clean_publication_text

    code = 'name = str(path).replace("\\\\", "/")\n'
    cleaned = clean_publication_text("steps/s1/outputs/scripts/h.py", code, Settings(), {"id": "r", "text": ""})
    assert cleaned == code and "<local-path>" not in cleaned and "수동 확인 필요" not in cleaned
    unc = 'share = r"\\\\fileserver\\data\\x.tsv"\n'
    assert "fileserver" not in clean_publication_text("steps/s1/outputs/scripts/u.py", unc, Settings(), {"id": "r", "text": ""})


def test_removed_absolute_paths_in_the_execution_record_become_a_count():
    from labhq.portfolio import _collapse_removed_paths

    text = "# 실행 기록\n\n- 남은 절대경로: <local-path> <local-path> <reference-path>, <local-path>\n- 다음 줄\n"
    assert _collapse_removed_paths(text) == (
        "# 실행 기록\n\n- 남은 절대경로: 4개(게시본에는 경로를 적지 않음)\n- 다음 줄\n")
    assert _collapse_removed_paths("- 남은 절대경로: 없음\n") == "- 남은 절대경로: 없음\n"


@pytest.mark.parametrize("code", ["rows = open('Inputs\\s1\\t.tsv')\n", "meta = open('MANIFEST.JSON')\n"])
def test_workspace_paths_are_detected_whatever_the_letter_case(code):
    from labhq.portfolio import _WORKSPACE_INPUT

    assert _WORKSPACE_INPUT.search(code)


def test_the_readme_states_real_limits_figures_files_runs_and_determinism(tmp_path):
    from labhq.portfolio import _determinism_line, _figure_lines, _file_list_lines, _limit_sentences, _run_lines

    report = ("# 보고서\n\n## 한계\n\n공개 조회는 9회까지만 허용돼 새 조회를 하지 않았다. 무작위 3상 수치는 UNVERIFIED로 남았다. "
              "표본이 작아 일반화할 수 없다. 네 번째 한계도 있다.\n\n![ORR와 신뢰구간](figures/orr.svg)\n")
    limits = _limit_sentences(report)
    assert not _limit_sentences("## 한계\n\n해석 범위와 남은 한계는 과학 보고서에 적었습니다.\n")
    assert len(limits) == 3 and "9회까지만" in limits[0] and any("UNVERIFIED" in sentence for sentence in limits)
    assert _figure_lines(report, ["figures/orr.svg", "figures/qc.svg"]) == [
        "- [orr.svg](figures/orr.svg): ORR와 신뢰구간", "- [qc.svg](figures/qc.svg): 보고서에 그림 설명이 없습니다"]
    files = {"code/s1/outputs/scripts/a.py": "import numpy as np\nnp.random.seed(7)\n",
             "code/s2/outputs/scripts/b.py": "print(1)\n"}
    assert "`code/s1/outputs/scripts/a.py`" in _determinism_line(files)
    assert "난수·seed 호출을 찾지 못했습니다" in _determinism_line({"code/s2/outputs/scripts/b.py": "print(1)\n"})
    assert "스크립트가 없어" in _determinism_line({})
    listing = _file_list_lines(files, ["tables/t.tsv"], ["figures/orr.svg"])
    assert listing[0].startswith("- 코드 2개: ") and "tables/t.tsv" in listing[1] and "figures/orr.svg" in listing[2]
    run = _run_lines({"code/x.py": "os.environ['LABHQ_INPUT_DATA_AB12CD34']\n"}, {"run/reproduce.md": "x"})
    assert "`LABHQ_INPUT_DATA_AB12CD34`" in run[0] and "원자료가 없을 때" in run[1]
