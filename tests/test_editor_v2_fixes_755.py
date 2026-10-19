"""The five manual v2 rebuild fixes from #755 and the question-line part of #757."""

from copy import deepcopy
from pathlib import Path

from labhq.portfolio import build_portfolio_files
from labhq.report_contract import build_report_package, reader_markdown
from labhq.report_pdf import CSS, FigureEmbedder, build_report_html, markdown_to_html
from tests.test_edition_astra_736 import astra_fixture


def test_pdf_claim_footnotes_become_numbered_endnotes() -> None:
    report = (
        "# 결과\n\n효과를 확인했다[^claim-analysis-main].\n\n"
        "[^claim-analysis-main]: 단계 `analysis` · claim `main` · [근거 파일](evidence.md)\n"
    )

    rendered = reader_markdown(report, footnotes=True)
    page, missing = build_report_html(
        {"id": "req_footnote", "text": "효과는 있는가?", "status": "done", "report": report},
        None,
    )

    assert missing == []
    assert "[^" not in rendered and "[^" not in page
    assert "[근거 1]" in rendered and "[근거 1]" in page
    assert "## Claim 근거" in rendered
    assert "단계 `analysis` · claim `main`" in rendered


def test_legacy_plan_recovers_only_rectangular_aggregate_tables_without_small_cells(tmp_path) -> None:
    from labhq.portfolio import legacy_aggregate_tables

    settings, request, assets, images = astra_fixture(tmp_path)
    request = deepcopy(request)
    for step in request["plan"]["steps"]:
        step.pop("output_types", None)
    for result in request["results"].values():
        if isinstance(result, dict):
            result.pop("output_types", None)

    good = "steps/mdis/outputs/tables/summary.tsv"
    malformed = "steps/mdis/outputs/tables/malformed.tsv"
    small = "steps/mdis/outputs/tables/small.tsv"
    assets[malformed] = "group\tcount\nall\n"
    assets[small] = "group\tcount\nrare\t9\n"

    candidates = legacy_aggregate_tables(request, assets)
    files = build_portfolio_files(request, settings, assets, images)

    assert good in candidates
    assert malformed not in candidates and small not in candidates
    assert "studies/C-mdis/tables/summary.tsv" in files
    assert not any(name.endswith(("malformed.tsv", "small.tsv")) for name in files)
    assert not any(name.endswith("people.tsv") for name in files)


def test_readme_and_pdf_show_only_plain_research_question(tmp_path) -> None:
    settings, request, assets, images = astra_fixture(tmp_path)
    request = deepcopy(request)
    request["text"] = (
        "**컴퓨터 직업과 자녀 성비의 관련성은 있는가?**\n\n"
        "공개 오믹스로도 검토한다.\n\n"
        "Reference pointers from the PI (pointers only):\n"
        "- [path] C:/private/input\n"
        "Never write under a reference path."
    )
    stored = build_report_package(request, request["report"], request["results"]).to_dict()
    stored["question"] = request["text"]
    request["report_package"] = stored

    readme = build_portfolio_files(request, settings, assets, images)["README.md"]
    page, _missing = build_report_html(request, None)

    expected = "컴퓨터 직업과 자녀 성비의 관련성은 있는가? 공개 오믹스로도 검토한다."
    assert f"**질문: {expected}**" in readme
    assert f'<p class="request">{expected}</p>' in page
    for forbidden in ("Reference pointers", "Never write", "C:/private", "****"):
        assert forbidden not in readme
        assert forbidden not in page


def test_consecutive_figures_keep_the_short_following_paragraph_with_them() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    markdown = "![첫 그림](a.svg)\n\n![둘째 그림](b.svg)\n\n짧은 설명입니다.\n"

    html = markdown_to_html(markdown, FigureEmbedder(None, extra={"a.svg": svg, "b.svg": svg}))

    assert '<section class="figure-run">' in html
    assert html.index("첫 그림") < html.index("둘째 그림") < html.index("짧은 설명입니다.")
    assert ".figure-run { break-inside: avoid;" in CSS
    assert ".figure-run figure img { max-width: 80%;" in CSS


def test_empty_references_recover_complete_license_rows_from_input_readme(tmp_path) -> None:
    from labhq.result_storage import edition_input_readmes

    settings, request, assets, images = astra_fixture(tmp_path)
    request = deepcopy(request)
    request["references"] = []
    input_readme = """# 입력 자료

| 자료 | 출처 | 접근 조건 | 재배포 범위 | 필수 인용 |
|---|---|---|---|---|
| 공개 코호트 | https://example.org/cohort | 가입 후 이용 | 원자료 재배포 금지 | Example Cohort 2026 |
"""
    (Path(request["project_dirs"][0]) / "README.md").write_text(input_readme, encoding="utf-8")
    recovered = edition_input_readmes(request)

    licenses = build_portfolio_files(
        request,
        settings,
        assets,
        images,
        input_readmes=recovered,
    )["DATA_AND_LICENSES.md"]

    assert "https://example.org/cohort" in licenses
    assert "가입 후 이용" in licenses
    assert "원자료 재배포 금지" in licenses
    assert "Example Cohort 2026" in licenses
    assert "| 요청 기록에 자료 없음 |" not in licenses
