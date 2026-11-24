from __future__ import annotations

import pytest

from labhq.report_contract import build_report_package, reader_markdown
from labhq.report_format import add_references, scientific_appendix
from labhq.report_markdown import clean_report_prose, markdown_body, markdown_regions, rendered_markers


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


def test_report_cleanup_preserves_only_the_exact_benchmark_closing_marker():
    source = (
        "Table: /tmp/results.tsv\n"
        "<!-- /LABHQ_BENCH_RESULT/private -->\n"
        "<!-- /labhq_bench_result -->\n"
        "<!-- /LABHQ_BENCH_RESULT -->"
    )
    assert clean_report_prose(source) == (
        "Table: 로컬 경로\n<!-- 로컬 경로>\n<!-- 로컬 경로>\n<!-- /LABHQ_BENCH_RESULT -->"
    )


def test_reader_report_preserves_tex_beside_windows_rooted_path():
    source = (
        r"The model used \beta coefficients and \log transformation ($\beta = 1$) [[claim:s1/c1]]."
        "\n"
        r"Expressions $\alpha\beta$, $\sin\theta$ and $\operatorname{log}\beta$ were tested."
        "\n"
        r"Alternate notation \(\sin\theta\) and \[\alpha\beta\]."
        "\n"
        "$$\n" + r"\sin\theta" + "\n$$"
    )
    report = source + "\n" + r"Input was \Users\Example User\counts.tsv and \final results.tsv."
    assert add_references(report, "", []) == source + "\nInput was 로컬 경로 and 로컬 경로."


def test_reader_report_hides_local_prose_and_translates_warning_but_keeps_links_and_evidence():
    source = (
        "# 결과\n\n## 요약\n\n"
        '작업 위치 "C:/Users/test name/run/data.tsv"와 \\\\pc\\share\\counts.tsv입니다. [[claim:s1/c1]]\n\n'
        "- s1: ev_missing cites artifact outputs/check.json missing from artifact_refs\n"
        "- 표본 짝을 확인하지 못한 점은 해석에 남습니다.\n\n"
        "![로컬 그림](/tmp/labhq/outputs/fig.svg)\n"
        "![그림](steps/s1/outputs/fig.svg)\n"
        "[표](steps/s1/outputs/stats.tsv) · https://example.org/Users/public · <b>라벨</b>\n"
        "위치 /data/owner:Example User/result.tsv:L10-L12[[claim:s1/c2]]\n"
    )
    stored = add_references(source, "", [])
    rendered = reader_markdown(stored)
    assert "test name" not in rendered and "\\\\pc" not in rendered and "/data/owner" not in rendered and "Example User" not in rendered
    assert "missing from artifact_refs" not in rendered
    assert "산출물이 원장에 등록되지 않았습니다" in rendered and "표본 짝을 확인" in rendered
    assert "[근거 1]" in rendered and "s1/c1" in rendered and "[[claim:s1/c1]]" in stored
    assert "로컬 그림 (로컬 경로)" in rendered and "/tmp/labhq" not in rendered
    assert "![그림](steps/s1/outputs/fig.svg)" in rendered
    assert "[표](steps/s1/outputs/stats.tsv)" in rendered
    assert "https://example.org/Users/public" in rendered and "<b>라벨</b>" in rendered
    assert "로컬 경로:L10-L12[근거 2]" in rendered and "s1/c2" in rendered
    package = build_report_package({}, stored)
    assert "test name" not in package.answer and package.evidence_notes == ["s1/c1", "s1/c2"]


def test_scientific_appendix_keeps_review_but_masks_machine_path():
    report = scientific_appendix("", {}, {"issues": [{"problem": "See ~/local/run.csv", "request": "Check samples"}]}, "")
    assert "Check samples" in report and "로컬 경로" in report and "~/local" not in report


def test_local_path_before_relative_link_keeps_label_and_target():
    report = "See /tmp/a and [result](steps/s1/outputs/a.tsv) [[claim:s1/c1]]"
    assert add_references(report, "", []) == "See 로컬 경로 and [result](steps/s1/outputs/a.tsv) [[claim:s1/c1]]"


@pytest.mark.parametrize("path", ["/home/example/private.tsv", "C:/Users/Example/data.tsv",
                                 r"C:\Users\Example\data.tsv", r"\Users\Example\data.tsv",
                                 "file:///home/example/private.tsv"])
def test_generated_report_redacts_paths_immediately_after_a_label_colon(path):
    links = "HTTPS:https://example.org/Users/public.\nFTP:ftp://example.org/pub/data.tsv."
    source = f"Source:{path}. [[claim:s1/c1]]\n{links}"
    assert add_references(source, "", []) == f"Source:로컬 경로. [[claim:s1/c1]]\n{links}"


@pytest.mark.parametrize("ratio", ["CD4+/CD8+", "CD4⁺/CD8⁺", "CD4+ /CD8+"])
def test_reader_report_preserves_slash_in_biological_ratio(ratio):
    report = f"{ratio} ratio increased [[claim:s1/c1]]."
    assert add_references(report, "", []) == report
    assert add_references(ratio, "", []) == ratio


@pytest.mark.parametrize("path", [
    "C:/Users/Jane Doe/run/results.tsv",
    "/home/Jane Doe/run/final results.tsv",
    "//server/Shared Data/Project Run/results.tsv",
    "///home/Jane Doe/run/results.tsv",
    r"\\pc\Shared Data\Project Run\results.tsv",
    r"C:\Users\Jane Doe\run\results.tsv",
    r"\Users\Jane Doe\run\results.tsv",
    r"\\?\C:\Users\Example User\Project Run\results.tsv",
    r"\\.\C:\Users\Example User\Project Run\results.tsv",
    r"\\?\UNC\server\Shared Data\Project Run\results.tsv",
    "file:///C:/Users/Jane Doe/run/final results.tsv",
    "/home/O'Connor/Project (pilot, v2)/results.tsv",
    "C:/Users/Doe,Jane/run/results.tsv",
    "/data/owner:Example User/project/results.tsv",
    "/data/owner:Example User/project/results.tsv:L10.raw",
    "/home/Project response increased expression/results.tsv",
])
def test_reader_report_redacts_unquoted_path_components_with_spaces(path):
    report = f"Table source: {path} followed by Case/control counts. [[claim:s1/c1]]"
    stored = add_references(report, "", [])
    assert stored == "Table source: 로컬 경로 followed by Case/control counts. [[claim:s1/c1]]"
    assert "[근거 1]" in reader_markdown(stored)
    assert add_references(f"표는 {path}입니다.", "", []) == "표는 로컬 경로입니다."


@pytest.mark.parametrize("target", [
    "C:/Users/Jane Doe/run/results.tsv",
    "</home/O'Connor/Project (pilot)/results.tsv>",
    "C%3A%2FUsers%2FJane%20Doe%2Fresults.tsv",
    "~example/Shared Data/results.tsv",
    "/Example User",
])
def test_generated_report_masks_local_link_target_and_preserves_its_label(target):
    report = f"[표]({target})와 [결과표](steps/s1/outputs/results.tsv)를 읽었습니다. [[claim:s1/c1]]"
    assert add_references(report, "", []) == (
        "표 (로컬 경로)와 [결과표](steps/s1/outputs/results.tsv)를 읽었습니다. [[claim:s1/c1]]"
    )


@pytest.mark.parametrize("path", [
    "/home/results/Jane Doe",
    "/data/stats.tsv:Example User",
    "~example/Shared Data",
    "/Example User",
    "C:/Users/Jane Doe/Project Alpha",
    "/home/results/Jane Doe/Project [phase 2]",
    "/home/Project response increased expression/run",
])
def test_generated_report_redacts_extensionless_local_tail(path):
    report = f"작업 폴더 {path}; 표본을 확인했습니다. [[claim:s1/c1]]"
    assert add_references(report, "", []) == "작업 폴더 로컬 경로; 표본을 확인했습니다. [[claim:s1/c1]]"
    assert add_references(f"작업 폴더 {path}. [[claim:s1/c1]]", "", []) == (
        "작업 폴더 로컬 경로. [[claim:s1/c1]]"
    )
    assert add_references(f"작업 폴더 {path}. Case/control 발현은 증가했습니다. [[claim:s1/c1]]", "", []) == (
        "작업 폴더 로컬 경로. Case/control 발현은 증가했습니다. [[claim:s1/c1]]"
    )

@pytest.mark.parametrize(("path", "conclusion"), [
    ("/home/results/run", "contains normalized counts and treatment increased expression"),
    ("/home/results/run", "treatment increased expression"),
    ("/home/results/run", "There was no difference"),
    ("/home/results/run", "Results indicate increased expression"),
    ("/home/results/run", "Protein levels increased"),
    ("/home/Example User/Project Alpha", "Gene expression increased"),
    ("/home/results/run", "gene expression was higher"),
    ("/home/results/run", "cell viability was reduced"),
    ("/home/results/run", "treatment effect was significant"),
    ("/home/results/run", "gene expression did not increase"),
    ("/home/results/run", "treatment response can improve"),
    ("/home/results/run", "cell viability would not change"),
    ("/home/results/run", "CD4+ T cells increased"),
    ("/home/results/run", "p53 expression was higher"),
    ("/home/results/run", "IL-6 levels fell"),
    ("/home/results/run", "16S rRNA abundance increased"),
    ("/home/results/run", "5hmC levels increased"),
    ("/home/results/run", "CD4+/CD8+ ratio increased"),
    ("/home/results/run", "CD4+ / CD8+ ratio increased"),
    ("/home/results/run", "CD4+ /CD8+ ratio increased"),
    ("/home/results/run", "CD4⁺  / CD8⁺ ratio increased"),
    ("/home/Example User/Project phase two", "Treatment produced lower counts"),
    ("/home/Example User/Project Alpha", "Both groups had similar expression"),
    ("/home/Example User/Project Alpha", "We found no evidence of treatment response"),
    ("/home/Example User/Project Alpha", "발현 증가를 확인했습니다"),
    ("/home/Example User/Project phase two", "the control group yielded lower counts"),
    ("/home/Example User/Project Alpha", "RNA levels rose in the treatment group"),
    ("/home/Jane Doe/Project Alpha", "contains normalized counts and treatment increased expression"),
    (r"C:\Users\Example User\Project Alpha", "처리군의 발현은 증가했습니다"),
    ("file:///C:/Users/Example User/run/final results.tsv", "처리군의 발현은 증가했습니다"),
])
def test_generated_report_keeps_unpunctuated_conclusion_after_local_path(path, conclusion):
    source = f"작업 위치 {path} {conclusion} [[claim:s1/c1]]"
    stored = add_references(source, "", [])
    assert stored == f"작업 위치 로컬 경로 {conclusion} [[claim:s1/c1]]"
    rendered = reader_markdown(stored)
    assert f"{conclusion} [근거 1]" in rendered
    assert "s1/c1" in rendered
    front = build_report_package({}, f"# 연구 보고서\n\n## 요약\n\n{source}")
    assert front.answer == "작업 위치 로컬 경로 " + " ".join(conclusion.split())
    assert front.evidence_notes == ["s1/c1"]


@pytest.mark.parametrize(("path", "conclusion"), [
    ("/home/results/run", "treatment increased expression"),
    (r"C:\Users\Example User\Project Alpha", "Protein levels increased"),
    ("/home/results/run", "CD4+ / CD8+ ratio increased"),
])
def test_generated_report_keeps_conclusion_after_path_without_anchor(path, conclusion):
    source = f"작업 위치 {path} {conclusion}"
    expected = f"작업 위치 로컬 경로 {conclusion}"
    assert add_references(source, "", []) == expected
    assert build_report_package({}, f"# 연구 보고서\n\n## 요약\n\n{source}").answer == expected


@pytest.mark.parametrize("name", ["Project contains Private Data", "Project Results indicate Private Data"])
def test_generated_report_masks_extensionless_name_that_contains_a_verb(name):
    source = f"작업 폴더 /home/results/{name}"
    assert add_references(source, "", []) == "작업 폴더 로컬 경로"
    assert add_references(source + " [[claim:s1/c1]]", "", []) == "작업 폴더 로컬 경로 [[claim:s1/c1]]"
