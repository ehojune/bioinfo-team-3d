from __future__ import annotations

from labhq.report_contract import (
    FRONT_SECTION_TITLES,
    build_report_package,
    reader_markdown,
    render_report_front,
    validate_report_package,
    with_report_front,
)
from labhq.report_pdf import build_report_html
from labhq.request_bundle import _readme as bundle_readme
from labhq.portfolio import _readme as portfolio_readme


def fixed_request() -> tuple[dict, str, dict]:
    request = {
        "id": "req_contract",
        "text": "두 집단의 차이와 한계는 무엇인가?",
        "status": "done",
        "research_contract": {"schema_version": 2},
    }
    report = """# 고정 결과

## 요약

두 집단의 차이는 3입니다 [[claim:analysis/main]].

## 결과

![대표 그림](wd_analysis/outputs/plot.svg)

[전체 결과표](wd_analysis/outputs/results.tsv)

## 한계

표본 수가 작습니다.
"""
    results = {
        "analysis": {
            "ok": True,
            "workdir_id": "wd_analysis",
            "outputs": ["outputs/plot.svg", "outputs/results.tsv"],
        }
    }
    return request, report, results


def headings(text: str) -> list[str]:
    return [line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")]


def test_common_front_order_and_reader_copy_are_shared() -> None:
    request, report, results = fixed_request()
    package = build_report_package(request, report, results)

    front = render_report_front(package)
    original = reader_markdown(with_report_front(report, package))

    assert headings(front) == list(FRONT_SECTION_TITLES)
    assert headings(original)[: len(FRONT_SECTION_TITLES)] == list(FRONT_SECTION_TITLES)
    assert "[[claim:" not in original
    assert "[근거 1]" in original
    assert package.kind == "research"


def test_bundle_edition_pdf_and_original_use_the_same_first_screen_order() -> None:
    request, report, results = fixed_request()
    package = build_report_package(request, report, results)
    request.update(report=with_report_front(report, package), report_package=package.to_dict(), results=results)
    expected = list(FRONT_SECTION_TITLES)

    original = reader_markdown(request["report"])
    bundle = bundle_readme(request, [], [], [], False)
    edition = portfolio_readme(
        request,
        report,
        [],
        ["figures/plot.svg"],
        ["tables/results.tsv"],
        [],
        {"run/reproduce.md": "# 재현"},
    )
    pdf_html, _missing = build_report_html(request, None)

    assert headings(original)[:8] == expected
    assert headings(bundle)[:8] == expected
    assert headings(edition)[:8] == expected
    assert all(pdf_html.index(f">{title}</h") < pdf_html.index(f">{next_title}</h")
               for title, next_title in zip(expected, expected[1:]))
    assert "[[claim:" not in pdf_html


def test_contract_check_catches_missing_table_bad_shape_and_reader_marker() -> None:
    request, report, results = fixed_request()
    package = build_report_package(request, report, results)

    missing = validate_report_package(
        package,
        available_files={"wd_analysis/outputs/plot.svg"},
        reader_documents={"web": reader_markdown(with_report_front(report, package))},
    )
    assert any(row["code"] == "missing_table" for row in missing["problems"])

    malformed = validate_report_package(
        package,
        available_files={"wd_analysis/outputs/plot.svg", "wd_analysis/outputs/results.tsv"},
        file_contents={"wd_analysis/outputs/results.tsv": "gene\tvalue\nA\t1\textra\n"},
        reader_documents={"web": "결과 [[claim:analysis/main]]"},
    )
    assert any(row["code"] == "table_shape" for row in malformed["problems"])
    assert any(row["code"] == "internal_marker" for row in malformed["problems"])


def test_audio_result_must_be_in_the_result_section() -> None:
    request, report, results = fixed_request()
    report = report.replace("## 결과", "[녹음](wd_analysis/outputs/result.wav)\n\n## 결과")
    results["analysis"]["outputs"].append("outputs/result.wav")
    package = build_report_package(request, report, results)

    check = validate_report_package(
        package,
        available_files={
            "wd_analysis/outputs/plot.svg",
            "wd_analysis/outputs/results.tsv",
            "wd_analysis/outputs/result.wav",
        },
        file_contents={"wd_analysis/outputs/results.tsv": "gene\tvalue\nA\t1\n"},
    )
    assert any(row["code"] == "audio_outside_results" for row in check["problems"])


def test_request_kind_required_matrix_distinguishes_not_applicable() -> None:
    request, report, results = fixed_request()
    research = build_report_package(request, report, results)
    general = build_report_package({"id": "g", "text": "정리해 줘", "status": "done"},
                                   "# 답\n\n## 결과\n\n정리했습니다.\n\n## 한계\n\n없음.", {})
    failure = build_report_package({"id": "f", "text": "분석해 줘", "status": "failed", "error": "runner offline"},
                                   "# 실패\n\n## 결과\n\n완료하지 못했습니다.\n\n## 한계\n\nrunner offline", {})

    assert research.required["evidence_notes"] == "required"
    assert general.required["evidence_notes"] == "optional"
    assert failure.required["figures"] == "not_applicable"
    assert failure.required["execution_record"] == "required"
