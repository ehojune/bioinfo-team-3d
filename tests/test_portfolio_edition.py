from pathlib import Path

import labhq.portfolio as portfolio
from labhq.korean_qc import check_document
from labhq.portfolio import build_portfolio_files, scan_publish_files
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
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle)

    required = {
        "README.md", "report/report.md", "run/requests.md", "run/decisions.md",
        "run/execution_record.md", "run/costs.md", "code/analyze.py",
        "figures/graphical_abstract.svg", "figures/volcano.svg",
        "tables/result_summary.tsv", "REFERENCES.md", "DATA_AND_LICENSES.md",
    }
    assert required <= files.keys()
    assert files["report/report.md"].startswith("# 공개 오믹스 대체 분석")
    assert "## 정정" in files["report/report.md"]
    assert "CP1 원문: 계획대로 진행" in files["run/decisions.md"]
    assert "CP2 원문: 0개도 결과로 기록" in files["run/decisions.md"]
    assert "다음 차수 없이 끝낸다" in files["run/decisions.md"]
    assert "C:/Users" not in files["run/execution_record.md"]

    readme = files["README.md"]
    assert "| 단계 | 자료 | 결과 |" in readme
    assert "16,242" in readme and "0개" in readme
    assert "통계적으로 유의하지" in readme
    assert "by_job.tsv" not in files and "patient_summary.tsv" not in files
    assert "내부 자료 영역이라 표 2개는 요청 묶음에만 둠" in readme
    assert not any("outputs/literature" in name or "article.md" in name for name in files)
    assert not any(name.startswith("run/nextflow/") for name in files)
    assert check_document(readme)["status"] == "PASS"
    assert check_document(files["report/report.md"])["status"] == "PASS"


def test_publish_scan_catches_credentials_paths_signed_urls_and_small_cells():
    files = {
        "README.md": "token=ghp_" + "A" * 24,
        "run/requests.md": "C:/Users/alice/private.tsv https://x.test/a?X-Amz-Signature=secret",
        "tables/private.tsv": "group\tcount\nA\t9\n",
    }
    kinds = {issue["kind"] for issue in scan_publish_files(files, internal=True)}
    assert {"secret", "local_path", "signed_url", "small_cell"} <= kinds


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
