from __future__ import annotations

import pytest

from labhq.report_contract import build_report_package, reader_markdown
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
