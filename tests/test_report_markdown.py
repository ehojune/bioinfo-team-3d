from __future__ import annotations

import pytest

from labhq.report_contract import build_report_package, reader_markdown
from labhq.report_format import add_references, scientific_appendix
from labhq.report_markdown import markdown_body, markdown_regions, rendered_markers


@pytest.mark.parametrize("code", [
    "````markdown\n```\n[[claim:fake/code]] ![x](fake.png) ** __\n````",
    "~~~markdown\n[[claim:fake/code]] ![x](fake.png) ** __\n~~~",
    "    [[claim:fake/code]] ![x](fake.png) ** __",
    "> ```markdown\n> [[claim:fake/code]] ![x](fake.png) ** __\n> ```",
    "- ```markdown\n  [[claim:fake/code]] ![x](fake.png) ** __\n  ```",
    "문장 ``[[claim:fake/code]] ![x](fake.png) ** __ ` `` 끝",
    "문장 `여러 줄\n[[claim:fake/code]] ![x](fake.png) ** __` 끝",
    "문장 `[[claim:fake/code]] ![x](fake.png) ** __\\` 끝",
])
def test_commonmark_code_regions_are_shared_by_all_report_scans(code):
    source = code + "\n\n실제 근거 [[claim:real/body]].\n"
    package = build_report_package({"text": "질문"}, source)
    regions = markdown_regions(source)
    assert "".join(source[part.start:part.end] for part in regions) == source
    body = markdown_body(source)
    assert len(body) == len(source) and body.count("\n") == source.count("\n")
    assert "fake/code" not in body
    assert package.evidence_notes == ["real/body"]
    assert package.figures == []
    assert reader_markdown(source).startswith(code)
    assert rendered_markers(code) == []


def test_backticks_do_not_span_commonmark_paragraph_boundaries():
    source = "미완성 `\n\n본문 [[claim:real/body]]\n\n다른 문단 `"
    assert "real/body" in markdown_body(source)
    assert "본문 [근거 1]" in reader_markdown(source)


@pytest.mark.parametrize("source", [
    "본문 **짧은 강조**입니다.",
    "별 하나 *`코드`*와 강조 **정상**",
    "<code>** __ [[</code> 본문",
    "<!-- ** __ [[ -->본문",
    "[정상 링크](https://example.org/**/__)",
    "별 하나 *\n* 다른 줄",
])
def test_render_check_uses_visible_html_text(source):
    assert rendered_markers(source) == []


def test_render_check_reports_source_line_after_softbreak():
    found = rendered_markers("첫 줄\n둘째 줄 **(강조)**이 깨집니다.")
    assert [(hit.marker, hit.line) for hit in found] == [("**", 2)]


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
def test_code_regions_preserve_source_newlines(newline):
    source = newline.join(["> 앞 `[[claim:example/code]]` 뒤", "", "실제 [[claim:real/body]]", ""])
    rendered = reader_markdown(source)
    assert "> 앞 `[[claim:example/code]]` 뒤" + newline in rendered
    assert "실제 [근거 1]" in rendered


def test_reader_report_hides_local_prose_and_translates_warning_but_keeps_links_and_evidence():
    source = (
        "# 결과\n\n## 요약\n\n"
        '작업 위치 "C:/Users/test name/run/data.tsv"와 \\\\pc\\share\\counts.tsv입니다. [[claim:s1/c1]]\n\n'
        "- s1: ev_missing cites artifact outputs/check.json missing from artifact_refs\n"
        "- 표본 짝을 확인하지 못한 점은 해석에 남습니다.\n\n"
        "![그림](/tmp/labhq/outputs/fig.svg)\n"
        "[표](steps/s1/outputs/stats.tsv) · https://example.org/Users/public · <b>라벨</b>\n"
        "위치 /home/person/result.tsv:L10-L12\n"
    )
    stored = add_references(source, "", [])
    rendered = reader_markdown(stored)
    assert "test name" not in rendered and "\\\\pc" not in rendered and "/home/person" not in rendered
    assert "missing from artifact_refs" not in rendered
    assert "산출물이 원장에 등록되지 않았습니다" in rendered and "표본 짝을 확인" in rendered
    assert "[근거 1]" in rendered and "s1/c1" in rendered and "[[claim:s1/c1]]" in stored
    assert "![그림](/tmp/labhq/outputs/fig.svg)" in rendered  # bundle maps the declared target
    assert "[표](steps/s1/outputs/stats.tsv)" in rendered
    assert "https://example.org/Users/public" in rendered and "<b>라벨</b>" in rendered
    assert "로컬 경로:L10-L12" in rendered
    package = build_report_package({}, stored)
    assert "test name" not in package.answer and package.evidence_notes == ["s1/c1"]


def test_scientific_appendix_keeps_review_but_masks_machine_path():
    report = scientific_appendix("", {}, {"issues": [{"problem": "See ~/local/run.csv", "request": "Check samples"}]}, "")
    assert "Check samples" in report and "로컬 경로" in report and "~/local" not in report
