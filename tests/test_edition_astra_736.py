"""Astra's publication-edition findings accepted in issue #736 (items 1, 4, 5, 7 and 9)."""

from copy import deepcopy
from pathlib import Path

import pytest

from labhq.portfolio import EditionCheckError, build_portfolio_files, scan_publish_files
from labhq.result_storage import place_pdf_in_readme
from tests.test_portfolio_branch_folders import branched_fixture


def astra_fixture(tmp_path: Path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    source_report = request["report"]
    request["report"] = source_report.replace(
        "## 결과\n\n",
        "## 결과\n\n[오믹스 집계표](work-omics/outputs/tables/summary.tsv)\n\n",
    ).replace(
        "## 결론",
        "두 공개 자료의 결과는 일치했다 [[claim:omics/c1]].\n\n"
        "### 이전 차수에서 고친 것\n\n- 표의 탭 문자를 교정했다.\n\n"
        "### 리뷰 참고\n\n- 이전 리뷰의 표현 지적을 반영했다.\n\n## 결론",
    )
    request["references"] = [
        {
            "kind": "path",
            "value": str(Path(request["project_dirs"][0]) / "births.tsv"),
            "note": "접근=승인된 내부 환경; 재배포=공개 가능한 집계표만; 인용=미확인",
        },
        {
            "kind": "doi",
            "value": "10.1000/example",
            "note": "접근=공개 메타데이터; 재배포=서지정보; 인용=DOI 10.1000/example",
        },
    ]
    settings.policy.data_zones[0].note = (
        "접근=승인된 내부 환경; 재배포=공개 가능한 집계표만; 인용=미확인"
    )
    request["plan"]["steps"][1]["agent_id"] = "analyst"
    request["plan"]["steps"][-1].update(
        agent_id="scientist",
        output_types=[{"name": "outputs/tables/combined.tsv", "data_type": "table"}],
    )
    request["results"]["omics"]["structured"] = {
        "schema_version": 2,
        "step_id": "omics",
        "claims": [{"id": "c1", "statement": "두 공개 자료의 결과는 일치했다."}],
        "evidence": [{"id": "e1", "source": {"artifact_id": "a1", "locator": "all rows"}}],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}],
        "artifact_refs": [{"artifact_id": "a1", "path": "outputs/tables/summary.tsv"}],
    }
    request["results"]["synth"].update(
        workdir="work-synth",
        outputs=["outputs/tables/combined.tsv", "outputs/figures/forest.svg"],
    )
    assets["steps/synth/outputs/tables/combined.tsv"] = "source\testimate\nA\t1.0\n"
    images["work-synth/outputs/figures/forest.svg"] = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>forest</text></svg>'
    )
    return settings, request, assets, images


def test_astra_publication_edition_contract(tmp_path):
    settings, request, assets, images = astra_fixture(tmp_path)
    original_report = request["report"]

    files = build_portfolio_files(request, settings, assets, images)

    assert request["report"] == original_report  # the validated request report keeps its claim anchors
    assert "tables/combined.tsv" in files
    assert "studies/B-omics/tables/summary.tsv" in files

    readme = files["README.md"]
    ordered = (
        "**질문:",
        "**답:",
        "**한계:",
        "![그래픽 초록]",
        "![대표 그림]",
        "## 보고서·결과표·재현",
        "| 갈래 | 상태 | 핵심 결과 한 줄 | 폴더 |",
    )
    positions = [readme.index(marker) for marker in ordered]
    assert positions == sorted(positions)
    with_pdf = place_pdf_in_readme(readme, link="report/report.pdf")
    assert with_pdf.index("[PDF](report/report.pdf)") < with_pdf.index("| 갈래 | 상태 |")
    assert "탭 문자를 교정" not in readme
    assert "뺀 표: people.tsv" in readme
    assert "사유:" in readme and "대체 요약:" in readme and "주요 열:" in readme

    report = files["report/report.md"]
    assert "[[claim:" not in report
    assert "[^claim-omics-c1]" in report
    assert "단계 `omics` · claim `c1` · [근거 파일]" in report
    assert "이전 차수에서 고친 것" not in report and "리뷰 참고" not in report
    assert "이전 차수에서 고친 것" in files["run/revision_history.md"]
    assert "리뷰 참고" in files["run/revision_history.md"]

    branch = files["studies/B-omics/README.md"]
    assert "## 입력→방법→QC→결과 연결표" in branch
    assert "| 구간 | 단계 | 직원 | 주요 산출 |" in branch
    assert "analyst" in branch
    assert "../../docs/reference/" in branch
    assert "docs/reference/A-literature/stage_report.md" in files
    assert "studies/A-literature/report/stage_report.md" not in files
    assert "뺀 표: people.tsv" in files["studies/C-mdis/README.md"]

    licenses = files["DATA_AND_LICENSES.md"]
    assert "| 자료 | 출처 | 접근 조건 | 재배포 범위 | 필수 인용 |" in licenses
    assert "승인된 내부 환경" in licenses and "공개 가능한 집계표만" in licenses
    assert "DOI 10.1000/example" in licenses
    assert "<reference-path>" not in licenses
    assert "넣으세요" not in licenses and "확인 필요" not in licenses


def test_report_referenced_table_must_have_a_rectangular_shape(tmp_path):
    settings, request, assets, images = astra_fixture(tmp_path)
    assets["steps/omics/outputs/tables/summary.tsv"] = "tested\tpassed\n16242\n"

    with pytest.raises(EditionCheckError, match="table_shape"):
        build_portfolio_files(request, settings, assets, images)


def test_data_license_placeholders_block_publication():
    issues = scan_publish_files({
        "DATA_AND_LICENSES.md": (
            "| 자료 | 출처 | 접근 조건 | 재배포 범위 | 필수 인용 |\n"
            "| x | <reference-path> | 확인 필요 | 미확인 | 원 출처의 필수 문구를 넣으세요 |\n"
        )
    })

    assert {issue["kind"] for issue in issues} == {"placeholder"}
