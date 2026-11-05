from __future__ import annotations

import pytest

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


def test_report_assets_ignore_markdown_examples_inside_code() -> None:
    request, report, results = fixed_request()
    report = report.replace(
        "![대표 그림](wd_analysis/outputs/plot.svg)",
        "인라인 예시 `![그림](example.png)`\n\n```markdown\n![그림](fenced.png)\n```",
    )
    results["analysis"]["outputs"] = ["outputs/results.tsv"]

    package = build_report_package(request, report, results)
    checked = validate_report_package(package, available_files={"wd_analysis/outputs/results.tsv"})

    assert package.figures == []
    assert not any(row["code"] == "missing_figure" for row in checked["problems"])


@pytest.mark.parametrize("example", [
    "인라인 예시 `[[claim:example/inline]]`입니다.",
    "```markdown\n[[claim:example/fenced]]\n```",
    "~~~markdown\n[[claim:example/tilde]]\n~~~",
])
def test_report_claim_anchors_ignore_markdown_examples_inside_code(example: str) -> None:
    request, report, results = fixed_request()
    report = report.replace(
        "두 집단의 차이는 3입니다 [[claim:analysis/main]].",
        example,
    )

    package = build_report_package(request, report, results)
    checked = validate_report_package(
        package,
        available_files={
            "wd_analysis/outputs/plot.svg",
            "wd_analysis/outputs/results.tsv",
        },
    )

    assert package.evidence_notes == []
    assert package.source_report == report
    assert checked["status"] == "FAIL"
    assert any(
        row["code"] == "required_missing" and row["field"] == "evidence_notes"
        for row in checked["problems"]
    )


def test_report_claim_anchors_preserve_real_evidence_next_to_code_examples() -> None:
    request, report, results = fixed_request()
    report += "\n`[[claim:example/inline]]`\n\n~~~markdown\n[[claim:example/fenced]]\n~~~\n"

    package = build_report_package(request, report, results)

    assert package.evidence_notes == ["analysis/main"]
    assert "analysis/main" in render_report_front(package)


def test_reader_claim_replacement_does_not_touch_code_examples() -> None:
    report = """결론입니다 [[claim:analysis/main]].

`[[claim:example/inline]]`

```markdown
[[claim:example/fenced]]
```
"""

    rendered = reader_markdown(report)

    assert "결론입니다 [근거 1]." in rendered
    assert "`[[claim:example/inline]]`" in rendered
    assert "```markdown\n[[claim:example/fenced]]\n```" in rendered
    assert "근거 원장 `analysis/main`" in rendered
    assert "example/inline`" not in rendered.split("## 근거 각주", 1)[-1]


@pytest.mark.parametrize("suffix", [".xlsx", ".xls", ".XLSX", ".XLS"])
def test_excel_report_assets_are_classified_as_tables(suffix: str) -> None:
    request, report, results = fixed_request()
    excel_report = report.replace("results.tsv", f"results{suffix}")
    table_path = f"wd_analysis/outputs/results{suffix}"
    results["analysis"]["outputs"][1] = f"outputs/results{suffix}"
    package = build_report_package(request, excel_report, results)

    assert [table.path for table in package.tables] == [table_path]
    assert all(row.path != table_path for row in package.files)
    assert "tables" not in package.omissions
    assert f"[전체 결과표]({table_path})" in render_report_front(package)
    missing = validate_report_package(
        package, available_files={"wd_analysis/outputs/plot.svg"},
    )
    assert any(row["code"] == "missing_table" for row in missing["problems"])


def test_report_assets_keep_spaced_angle_bracket_destination() -> None:
    request, report, results = fixed_request()
    report = report.replace(
        "wd_analysis/outputs/plot.svg",
        "<wd_analysis/outputs/my plot.svg>",
    )
    results["analysis"]["outputs"][0] = "outputs/my plot.svg"

    package = build_report_package(request, report, results)

    assert [figure.path for figure in package.figures] == ["wd_analysis/outputs/my plot.svg"]
    assert "figures" not in package.omissions


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


def test_edition_pdf_rebuilds_the_front_from_cleaned_inputs() -> None:
    request, report, results = fixed_request()
    private = "Authorization: Bearer secret-token C:\\private\\cohort.tsv"
    unsafe_request = {**request, "text": private}
    unsafe_report = report.replace("두 집단의 차이는 3입니다", private)
    stored = build_report_package(unsafe_request, unsafe_report, results).to_dict()
    scrub = lambda text: text.replace("Authorization: Bearer secret-token", "[credential]").replace(
        "C:\\private\\cohort.tsv", "[path]"
    )
    shown = {**unsafe_request, "text": scrub(private), "report_package": stored, "results": results}

    pdf_html, _missing = build_report_html(
        shown,
        None,
        report_text=scrub(unsafe_report),
        scrub=scrub,
    )

    assert "secret-token" not in pdf_html
    assert "C:\\private\\cohort.tsv" not in pdf_html
    assert "[credential] [path]" in pdf_html


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


@pytest.mark.parametrize("document", [
    "결론은 **(차이가 큽니다.)**이 맞습니다.",
    "**전체 문장에 **중요** 표시를 넣었습니다.**이 이어집니다.",
    "문장 안 __강조__는 그대로 보입니다.",
    "남은 [[자리표시]]를 고칩니다.",
])
@pytest.mark.parametrize("surface", ["README.md", "report.md", "RESULT.md", "web"])
def test_contract_check_rejects_emphasis_markers_left_on_screen(document: str, surface: str) -> None:
    request, report, results = fixed_request()
    package = build_report_package(request, report, results)

    checked = validate_report_package(package, reader_documents={surface: document})

    problem = next(row for row in checked["problems"] if row["code"] == "markdown_render")
    assert problem["field"] == surface
    assert "1줄" in problem["message"]
    assert "고치" in problem["message"] or "문장부호" in problem["message"] or "치환" in problem["message"]


def test_contract_check_ignores_render_markers_inside_code() -> None:
    request, report, results = fixed_request()
    package = build_report_package(request, report, results)
    document = "본문은 **정상 강조**입니다.\n\n```markdown\n**코드 예시** [[그대로]]\n```\n"

    checked = validate_report_package(package, reader_documents={"RESULT.md": document})

    assert not any(row["code"] in {"markdown_render", "internal_marker"} for row in checked["problems"])


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


@pytest.mark.parametrize("heading", ["## 결과", "# Results"])
def test_audio_is_allowed_under_a_result_subheading(heading: str) -> None:
    request, report, results = fixed_request()
    report = report.replace(
        "![대표 그림](wd_analysis/outputs/plot.svg)",
        "### 음성 판독\n\n[녹음](wd_analysis/outputs/result.wav)\n\n"
        "![대표 그림](wd_analysis/outputs/plot.svg)",
    )
    report = report.replace("## 결과", heading)
    results["analysis"]["outputs"].append("outputs/result.wav")
    package = build_report_package(request, report, results)

    check = validate_report_package(package)

    assert not any(row["code"] == "audio_outside_results" for row in check["problems"])


def test_audio_after_the_result_section_is_rejected() -> None:
    request, report, results = fixed_request()
    report += "\n## 참고\n\n### 녹음\n\n[음성](outside.wav)\n"
    checked = validate_report_package(build_report_package(request, report, results))
    assert any(row["code"] == "audio_outside_results" and row["field"] == "outside.wav"
               for row in checked["problems"])


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


@pytest.mark.parametrize("name,text", [
    ("a longer backtick fence holds a shorter one", "````md\n```\nopen[^1] y\n```\nx\n```\nz\n```\n````\n\nbody[^1]\n\n[^1]: note\n"),
    ("a longer tilde fence holds a shorter one", "~~~~\n~~~\nopen[^1] y\n~~~\nx\n~~~\nz\n~~~\n~~~~\n\nbody[^1]\n\n[^1]: note\n"),
])
def test_footnote_syntax_inside_a_longer_code_fence_is_left_alone(name, text):
    """#1193: the footnote pass stashes code first; a fence of four or more characters must be stashed whole."""
    rendered = reader_markdown(text, footnotes=True)
    assert "open[^1] y" in rendered, name
    assert "body[근거 1]" in rendered and "1. note" in rendered, name


def test_code_spans_are_the_one_scanner_for_links_and_footnotes():
    from labhq import portfolio, report_contract
    text = "a `b` ````x\n```\ny\n```\n```` c"
    spans = report_contract.code_spans(text)
    assert [text[start:end] for start, end in spans] == ["`b`", "````x\n```\ny\n```\n````"]
    assert portfolio._mask_code(text) == report_contract.mask_code(text)
    assert len(report_contract.mask_code(text)) == len(text)
