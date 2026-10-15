"""The PI's PDF figure policy of 2026-10-15 (#608).

- Figures in the PDF have no size limit of their own and come from the request bundle's originals, not from the
  size-limited edition copies.
- The PDF is at most 50 MB: when the first print is bigger the browser re-encodes the figures at a lower resolution and the
  page is printed again; no figure and no PDF is dropped for its size.
- At most 11 figures per report, one of them the graphical abstract; labhq draws one when the report has none.
- The writer prompts say so, and the edition's PDF goes up to GitHub through the binary put up to 50 MB.

GitHub is a mock transport and the browser is mocked, except one test that runs a real Edge or Chrome and skips when the
PC has none."""
import base64
import json
import os
import re
import struct
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx
import pytest

from labhq.integrations.github import GitHubClient
from labhq.orchestrator import cso
from labhq.report_abstract import UNITS, _width, build_graphical_abstract, with_abstract_link
from labhq.report_figures import (MAX_LINKED_FIGURES, MAX_REPORT_FIGURES, bundle_figure_check, image_links,
                                  linked_figure_paths)
from labhq.report_pdf import (NO_BROWSER, SHRINK_LEVELS, FigureEmbedder, build_report_file, build_report_html,
                              find_browser, print_pdf, print_within_cap, shrink_page)
from tests.test_report_pdf import PNG, SVG, build
from tests.test_result_storage_pdf import approve_all, builder, prepare, publish, puts

REAL_FIND_BROWSER = find_browser  # bound at import: tests/conftest.py patches the module's name for every test
SVG_NS = "{http://www.w3.org/2000/svg}"


def fake_printer(monkeypatch, sizes, pages=None, *, failures=None):
    """Replace the browser print: the PDF's size is ``sizes[shrink step]`` (the step is read from the page it is given).

    ``failures``: shrink step -> the reason print_pdf gives instead of a PDF."""
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: "C:/fake/msedge.exe")
    seen: list[int] = []

    def printer(browser, html_path, pdf_path, *, timeout):
        text = Path(html_path).read_text(encoding="utf-8")
        found = re.search(r'name="labhq-shrink" content="(\d+)"', text)
        step = int(found.group(1)) if found else 0
        seen.append(step)
        if pages is not None:
            pages.append(text)
        if failures and step in failures:
            return failures[step]
        Path(pdf_path).write_bytes(b"%PDF-1.7\n" + b"0" * sizes[min(step, len(sizes) - 1)])
        return None

    monkeypatch.setattr("labhq.report_pdf.print_pdf", printer)
    return seen


def with_cap(settings, megabytes):
    settings.github.edition_pdf_max_mb = megabytes
    return settings


def figure_report(count):
    return "# 결론과 권고\n\n" + "\n\n".join(
        f"![그림 {index}](WD/outputs/f{index}.png)" for index in range(count)) + "\n"


def image_sources(page):
    return re.findall(r'<img src="(data:[^"]+)" alt="([^"]*)"', page)


# --- 1. figures are not limited and come from the bundle ---------------------------------------------------------------

async def test_large_figures_stay_in_the_pdf_and_come_from_the_bundle_not_the_edition(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    big = b"\x89PNG large synthetic figure " + b"\x00" * 1_500_000  # over the edition's 1 MB per file
    (bundle / "steps/s1/outputs/big.png").write_bytes(big)
    with (bundle / "MANIFEST.tsv").open("a", encoding="utf-8") as handle:
        handle.write("steps/s1/outputs/big.png\tcopied\n")
    request = hub.requests["req_test"]
    request["report"] += "\n\n![큰 그림](wd1/outputs/big.png)\n"
    request["results"]["s1"]["outputs"].append("outputs/big.png")
    pages = []
    fake_printer(monkeypatch, [4096], pages)
    approve_all(hub, [])
    calls = []
    await publish(hub, calls)
    assert len(pages) == 1
    assert base64.b64encode(big).decode("ascii") in pages[0]  # the original, whatever its size
    # #611 review: the size limit is lifted, the eligibility is not; an unreported output stays out of the PDF as in the edition
    assert base64.b64encode(b"\x89PNG synthetic extra").decode("ascii") not in pages[0]
    assert "(그림 생략: 미보고 그림)" in pages[0]
    assert not (edition / "steps/s1/outputs/big.png").exists()  # the edition keeps its own limit for copied files
    assert "*(그림 생략: 큰 그림)*" in (edition / "report.md").read_text(encoding="utf-8")
    assert "[그림 없음: 큰 그림" not in pages[0] and "[그림 없음: 미보고 그림" not in pages[0]  # only the remote link is missing
    assert hub.requests["req_test"]["result_storage"]["pdf"]["included"] is True


def test_a_figure_has_no_size_limit_of_its_own(tmp_path):
    (tmp_path / "steps/s1/outputs").mkdir(parents=True)
    payload = b"\x89PNG" + b"\x00" * (9 * 1024 * 1024)  # the old per-figure limit was 8 MB
    (tmp_path / "steps/s1/outputs/huge.png").write_bytes(payload)
    embedder = FigureEmbedder(tmp_path)
    assert "<img " in embedder.markup("huge", "steps/s1/outputs/huge.png", block=False) and not embedder.missing
    assert FigureEmbedder(tmp_path).markup("empty", "steps/s1/outputs/none.png", block=False).startswith("<span")


# --- 2. the 50 MB cap and the shrink loop ------------------------------------------------------------------------------

MB = 1024 * 1024


def test_shrink_loop_is_entered_when_the_first_print_is_over_the_cap_and_ends_within_it(tmp_path, monkeypatch):
    settings, request, tasks, bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    with_cap(settings, 1)
    pages: list[str] = []
    seen = fake_printer(monkeypatch, [3 * MB, 2 * MB, int(1.5 * MB), 900_000, 800_000], pages)
    built = build_report_file(request, settings, tasks)
    assert seen == [0, 1, 2, 3]  # the plain page first, then each step until the PDF fits
    assert built.kind == "pdf" and len(built.data) <= 1 * MB and built.shrunk == 3 and not built.over_cap
    assert any("그림을 3단계 줄여 PDF를" in note for note in built.notes)
    assert "<script>" not in pages[0] and "script-src" not in pages[0]
    for step, page in enumerate(pages[1:], 1):
        edge, quality = SHRINK_LEVELS[step - 1]
        assert f"MAX={edge},Q={quality}" in page and "script-src 'unsafe-inline'" in page
        assert page.count("<img ") == pages[0].count("<img ")  # the figures are the same, only their pixels change
    reread = build_report_file(request, settings, tasks)
    assert reread.cached and reread.shrunk == 3 and len(seen) == 4  # cached with its shrink step


def test_a_pdf_that_fits_is_printed_once_and_the_figures_stay_as_they_are(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    with_cap(settings, 50)
    seen = fake_printer(monkeypatch, [49 * MB])
    built = build_report_file(request, settings, tasks)
    assert seen == [0] and built.shrunk == 0 and not built.over_cap and built.notes == ()


def test_when_no_step_fits_the_smallest_pdf_is_kept_with_every_figure(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    with_cap(settings, 1)
    pages: list[str] = []
    seen = fake_printer(monkeypatch, [9 * MB, 8 * MB, 7 * MB, 6 * MB, 5 * MB, 4 * MB, 3 * MB], pages)
    built = build_report_file(request, settings, tasks)
    assert seen == list(range(len(SHRINK_LEVELS) + 1))  # every step was tried
    assert built.kind == "pdf" and built.over_cap and built.shrunk == len(SHRINK_LEVELS)
    assert len(built.data) == len(b"%PDF-1.7\n") + 3 * MB  # the smallest of them
    assert any("한도 1 MB를 넘어요. 그림은 하나도 빼지 않았어요" in note for note in built.notes)
    assert len({page.count("<img ") for page in pages}) == 1  # no figure was dropped on the way


def test_a_page_without_raster_figures_is_not_printed_again(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig2.svg)\n",
                                              files={"outputs/fig2.svg": SVG.encode("utf-8")})
    with_cap(settings, 1)
    seen = fake_printer(monkeypatch, [3 * MB])
    built = build_report_file(request, settings, tasks)
    assert seen == [0] and built.kind == "pdf" and built.over_cap  # shrinking cannot help vector figures


def test_a_print_that_times_out_is_retried_with_smaller_figures_but_a_crash_is_not(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    timeout = "브라우저가 60초 안에 PDF를 끝내지 못해 HTML로 드립니다."
    seen = fake_printer(monkeypatch, [1024], failures={0: timeout, 1: timeout})
    built = build_report_file(request, settings, tasks)
    assert seen == [0, 1, 2] and built.kind == "pdf" and built.shrunk == 2
    crash = "브라우저가 PDF를 만들지 못해 HTML로 드립니다(종료 코드 1)."
    seen = fake_printer(monkeypatch, [1024], failures={0: crash})
    built = build_report_file({**request, "report": request["report"] + "\n\n추가"}, settings, tasks)
    assert seen == [0] and built.kind == "html" and "종료 코드 1" in built.reason


def test_print_within_cap_without_a_browser_result_returns_the_reason(tmp_path, monkeypatch):
    fake_printer(monkeypatch, [1024], failures={0: NO_BROWSER})
    pdf, reason, step, over = print_within_cap("b", "<html><body></body></html>", 1 * MB)
    assert (pdf, reason, step, over) == (b"", NO_BROWSER, 0, False)


def test_shrink_page_adds_only_the_script_the_policy_and_the_marker():
    page = ('<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'img-src data:; style-src \'unsafe-inline\'"></head><body><p>본문</p></body></html>\n')
    shrunk = shrink_page(page, 2)
    assert shrunk.count("<script>") == 1 and shrunk.index("<script>") < shrunk.index("</body>")
    assert "MAX=2000,Q=0.75" in shrunk and 'name="labhq-shrink" content="2"' in shrunk
    assert "script-src 'unsafe-inline'" in shrunk and "connect-src" not in shrunk and "default-src 'none'" in shrunk
    assert shrunk.replace(shrunk[shrunk.index("<script>"):shrunk.index("</script>") + 9], "").count("<p>본문</p>") == 1
    assert "http" not in shrunk[shrunk.index("<script>"):shrunk.index("</script>")]  # no network in the script
    assert shrink_page(page, 99) == shrink_page(page, len(SHRINK_LEVELS))  # clamped to the last step


def test_the_cap_is_part_of_the_cache_key(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    seen = fake_printer(monkeypatch, [1024])
    with_cap(settings, 50)
    build_report_file(request, settings, tasks)
    assert build_report_file(request, settings, tasks).cached and len(seen) == 1
    with_cap(settings, 20)
    assert not build_report_file(request, settings, tasks).cached and len(seen) == 2


@pytest.mark.skipif(REAL_FIND_BROWSER() is None, reason="no Edge or Chrome on this PC")
def test_a_real_browser_shrinks_large_figures_under_the_cap(tmp_path, monkeypatch):
    def noise_png(width, height, seed):  # incompressible pixels: the PNG is as big as its pixels
        block = os.urandom(width * 3)
        raw = b"".join(b"\x00" + bytes((byte + row * seed) & 255 for byte in block) for row in range(height))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))

    probe = tmp_path / "probe.html"
    probe.write_text("<!doctype html><title>probe</title><p>probe</p>", encoding="utf-8")
    failed = print_pdf(REAL_FIND_BROWSER(), probe, tmp_path / "probe.pdf", timeout=90)
    if failed:  # a browser that cannot print at all here (a CI sandbox) says nothing about the shrink loop
        pytest.skip(f"the browser cannot print on this machine: {failed}")
    monkeypatch.setattr("labhq.report_pdf.find_browser", REAL_FIND_BROWSER)
    files = {f"outputs/f{index}.png": noise_png(1500, 1000, index + 2) for index in range(3)}
    settings, request, tasks, _bundle = build(tmp_path, figure_report(3), files=files)
    with_cap(settings, 2)
    built = build_report_file(request, settings, tasks, timeout=120)
    assert built.kind == "pdf" and built.data.startswith(b"%PDF-"), built.reason
    assert sum(len(payload) for payload in files.values()) > 10 * MB  # the figures alone are far over the cap
    assert len(built.data) <= 2 * MB and built.shrunk >= 1 and not built.over_cap
    assert built.data.count(b"/Subtype /Image") + built.data.count(b"/Subtype/Image") >= 3  # none was left out


# --- 3. at most 11 figures ---------------------------------------------------------------------------------------------

def test_never_more_than_eleven_figures(tmp_path):
    files = {f"outputs/f{index}.png": PNG for index in range(14)}
    settings, request, _tasks, bundle = build(tmp_path, figure_report(14), files=files)
    page, missing = build_report_html(request, bundle)
    assert MAX_REPORT_FIGURES == MAX_LINKED_FIGURES == 11
    assert page.count("<img ") == 11  # 10 result figures and the graphical abstract labhq draws
    assert len(missing) == 4 and all("11장까지만" in item for item in missing)
    assert "그림 4개를 넣지 못했어요" in page and [alt for _src, alt in image_sources(page)][1:] == [
        f"그림 {index}" for index in range(10)]  # the first ten, in order, after the abstract
    problems = bundle_figure_check(bundle)["problems"]
    # #611 review: no abstract of its own, so the drawn one takes the 11th place and 10 result figures are the most
    assert len(problems) == 1 and "14장" in problems[0] and "결과 그림은 10장까지" in problems[0]
    assert len(linked_figure_paths((bundle / "report.md").read_text(encoding="utf-8"))) == 11
    assert FigureEmbedder(bundle, limit=1).markup("a", "steps/s1/outputs/f0.png", block=False).startswith("<img")


def test_eleven_figures_with_the_writers_own_abstract_are_all_kept(tmp_path):
    files = {f"outputs/f{index}.png": PNG for index in range(10)}
    files["outputs/graphical_abstract.png"] = PNG
    report = ("# 결론과 권고\n\n![Graphical abstract](WD/outputs/graphical_abstract.png)\n\n"
              + "\n\n".join(f"![그림 {index}](WD/outputs/f{index}.png)" for index in range(10)) + "\n")
    _settings, request, _tasks, bundle = build(tmp_path, report, files=files)
    page, missing = build_report_html(request, bundle)
    assert missing == [] and page.count("<img ") == 11 and "data:image/svg+xml" not in page  # no drawn abstract
    assert bundle_figure_check(bundle)["problems"] == []


# --- 4. the abstract is first, and drawn when the report has none ------------------------------------------------------

def test_the_drawn_abstract_is_the_first_figure_and_carries_the_plan_and_the_conclusion(tmp_path):
    report = "# 결론과 권고\n\n- TP53 변이는 사례군에서 두 배 흔하다 [[claim:s1/c1]].\n\n## 결과\n\n![군별 빈도](WD/outputs/fig1.png)\n"
    plan = {"steps": [{"id": "s1", "agent_id": "analyst", "instruction": "변이 빈도를 비교한다", "depends_on": [],
                       "outputs": ["outputs/fig1.png"]}]}
    _settings, request, _tasks, bundle = build(tmp_path, report, extra={"plan": plan})
    page, missing = build_report_html(request, bundle)
    sources = image_sources(page)
    assert missing == [] and len(sources) == 2
    assert sources[0][0].startswith("data:image/svg+xml;base64,") and sources[0][1].startswith("Graphical abstract")
    assert sources[1][0].startswith("data:image/png;base64,")
    assert page.index("Graphical abstract") < page.index("군별 빈도") and page.index("<h2>결론과 권고</h2>") < page.index("Graphical abstract")
    svg = base64.b64decode(sources[0][0].split(",", 1)[1]).decode("utf-8")
    ElementTree.fromstring(svg)
    assert "TP53 변이는 사례군에서" in svg and "흔하다." in svg and "claim" not in svg and "분석" in svg


def test_a_request_with_no_figures_still_gets_an_abstract_in_the_pdf(tmp_path, monkeypatch):
    """The 10-13 trial request had no figure at all."""
    settings, request, tasks, bundle = build(
        tmp_path, "# 결론과 권고\n\n요약 문장입니다. 두 번째 문장입니다.\n\n## 방법 요약\n\n방법.\n", files={"outputs/t.tsv": b"a\tb\n"})
    pages: list[str] = []
    fake_printer(monkeypatch, [2048], pages)
    built = build_report_file(request, settings, tasks)
    assert built.kind == "pdf" and built.notes == ()
    sources = image_sources(pages[0])
    assert len(sources) == 1 and sources[0][0].startswith("data:image/svg+xml;base64,")
    assert "요약 문장입니다" in base64.b64decode(sources[0][0].split(",", 1)[1]).decode("utf-8")
    assert "<img " in (bundle.parent / "req_pdf-report" / "report.html").read_text(encoding="utf-8")


def test_a_writers_abstract_in_the_bundle_replaces_the_drawn_one_and_a_dead_link_is_dropped(tmp_path):
    files = {"outputs/graphical_abstract.svg": SVG.encode("utf-8"), "outputs/fig1.png": PNG}
    report = "# 결론과 권고\n\n![Graphical abstract](WD/outputs/graphical_abstract.svg)\n\n![그림](WD/outputs/fig1.png)\n"
    _settings, request, _tasks, bundle = build(tmp_path, report, files=files)
    page, _missing = build_report_html(request, bundle)
    sources = image_sources(page)
    assert [alt for _src, alt in sources] == ["Graphical abstract", "그림"]  # the writer's file, still first
    # a link to an abstract nobody made (the writer could not run code) never reaches the bundle or the PDF as a dead link
    dead = "# 결론과 권고\n\n![Graphical abstract](outputs/graphical_abstract.png)\n\n![그림](WD/outputs/fig1.png)\n"
    _settings, request, _tasks, bundle = build(tmp_path / "dead", dead, files={"outputs/fig1.png": PNG})
    text = (bundle / "report.md").read_text(encoding="utf-8")
    assert "graphical_abstract" not in text and bundle_figure_check(bundle) == {"linked": 1, "problems": []}
    page, missing = build_report_html(request, bundle)
    assert missing == [] and "그림 없음" not in page and image_sources(page)[0][1].startswith("Graphical abstract")
    assert image_sources(page)[0][0].startswith("data:image/svg+xml;base64,")  # drawn instead
    page, missing = build_report_html(request, None)  # even with no bundle: a drawn abstract, no "missing" note for it
    assert not any("graphical_abstract" in item for item in missing)


def test_with_abstract_link_goes_under_the_first_heading_outside_code():
    link = "![Graphical abstract — 질문 → 데이터 → 방법 → 핵심 결과](graphical_abstract.svg)"
    assert with_abstract_link("# 결론과 권고\n\n본문\n").splitlines()[:3] == ["# 결론과 권고", "", link]
    fenced = with_abstract_link("```\n# 코드 안\n```\n\n## 첫 제목\n본문")
    assert fenced.index(link) > fenced.index("## 첫 제목")
    assert with_abstract_link("제목 없는 본문").startswith(link + "\n\n제목 없는 본문")


async def test_the_edition_uses_a_writers_abstract_and_draws_one_when_it_is_too_large(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    outputs = bundle / "steps/s1/outputs"
    (outputs / "graphical_abstract.png").write_bytes(b"\x89PNG small abstract")
    with (bundle / "MANIFEST.tsv").open("a", encoding="utf-8") as handle:
        handle.write("steps/s1/outputs/graphical_abstract.png\tcopied\n")
    request = hub.requests["req_test"]
    request["report"] = "# 결과\n\n![Graphical abstract](wd1/outputs/graphical_abstract.png)\n\n" + request["report"].split("\n", 2)[2]
    request["results"]["s1"]["outputs"].append("outputs/graphical_abstract.png")
    seen = []
    from labhq import result_storage
    monkeypatch.setattr(result_storage, "build_report_file", builder(seen))
    approve_all(hub, [])
    await publish(hub, [])
    report = (edition / "report.md").read_text(encoding="utf-8")
    assert [link.target for link in image_links(report)][0] == "steps/s1/outputs/graphical_abstract.png"
    assert not (edition / "graphical_abstract.svg").exists()  # the writer's own abstract is first, nothing is drawn
    assert (edition / "steps/s1/outputs/graphical_abstract.png").is_file()
    assert "(steps/s1/outputs/graphical_abstract.png)" in seen[0]["report"]  # and the PDF prints the bundle's file

    big_hub, big_edition = prepare(tmp_path / "big", monkeypatch)
    big_bundle = big_edition.with_name("req_test")
    (big_bundle / "steps/s1/outputs/graphical_abstract.png").write_bytes(b"\x89PNG" + b"\x00" * 1_500_000)  # over 1 MB
    with (big_bundle / "MANIFEST.tsv").open("a", encoding="utf-8") as handle:
        handle.write("steps/s1/outputs/graphical_abstract.png\tcopied\n")
    big_request = big_hub.requests["req_test"]
    big_request["report"] = "# 결과\n\n![Graphical abstract](wd1/outputs/graphical_abstract.png)\n\n" + big_request["report"].split("\n", 2)[2]
    big_request["results"]["s1"]["outputs"].append("outputs/graphical_abstract.png")
    seen = []
    monkeypatch.setattr(result_storage, "build_report_file", builder(seen))
    approve_all(big_hub, [])
    await publish(big_hub, [])
    big_report = (big_edition / "report.md").read_text(encoding="utf-8")
    assert [link.target for link in image_links(big_report)][0] == "graphical_abstract.svg"  # drawn: the edition cannot hold it
    assert (big_edition / "graphical_abstract.svg").is_file()
    assert "(steps/s1/outputs/graphical_abstract.png)" in seen[0]["report"]  # the PDF still prints the original


async def test_the_edition_draws_the_abstract_from_masked_text_and_uploads_it(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    hub.requests["req_test"].update(text="비교 요청 C:/Users/alice/secret/cohort.tsv 를 쓴다",
                                    plan={"steps": [{"id": "s1", "agent_id": "analyst", "depends_on": [],
                                                     "instruction": "C:/Users/alice/secret/cohort.tsv 를 분석한다"}]})
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder([]))
    approve_all(hub, [])
    calls = []
    await publish(hub, calls)
    svg = (edition / "graphical_abstract.svg").read_text(encoding="utf-8")
    ElementTree.fromstring(svg)
    assert "alice" not in svg and "&lt;local-path&gt;" in svg
    sent = puts(calls)["labhq/results/req_test/graphical_abstract.svg"]
    assert base64.b64decode(sent["content"]).decode("utf-8").startswith("<svg ")
    assert "graphical_abstract.svg" in (edition / "README.md").read_text(encoding="utf-8")


# --- 5. the drawn abstract ---------------------------------------------------------------------------------------------

def test_the_drawn_abstract_escapes_text_is_well_formed_and_works_offline():
    request = {
        "text": "<script>alert(1)</script> & \"인용\" 'x' 를 비교해 주세요 \x00\x0b",
        "references": [{"kind": "path", "value": "C:/Users/alice/private/cohort_v2"},
                       {"kind": "url", "value": "https://example.org/data?token=SECRET"}, {"kind": "doi", "value": "10.1/x"}],
        "project_dirs": ["/home/alice/projects/tp53"],
        "plan": {"steps": [
            {"id": "s1", "agent_id": "lit_scout", "instruction": "문헌을 찾는다. <b>두 번째</b> 문장", "depends_on": []},
            {"id": "s2", "agent_id": "analyst", "instruction": "x" * 400, "depends_on": ["s1"]},
            {"id": "s3", "agent_id": "unknown_agent", "instruction": "\"quoted\" & 'single'", "depends_on": []},
            {"id": "s4", "agent_id": "qc_reviewer", "instruction": "검토", "depends_on": []},
            {"id": "s5", "agent_id": "editor", "instruction": "정리", "depends_on": []},
            {"id": "s6", "agent_id": "engineer", "instruction": "여섯째", "depends_on": []}]}}
    report = ("# 결론과 권고\n\n- 결과는 <img src=x onerror=alert(1)> & 같다 [[claim:s1/c1]] ![그림](a.png) [링크](javascript:alert(1)).\n"
              "- 두 번째 결론 `code` **굵게**.\n- 세 번째 결론.\n- 넷째는 보이지 않는다.\n")
    svg = build_graphical_abstract(request, report)
    root = ElementTree.fromstring(svg)  # well formed XML, so every label was escaped
    assert root.tag == SVG_NS + "svg" and root.get("viewBox")
    assert "<script" not in svg and "<b>" not in svg and "<img" not in svg and "onerror=alert(1)>" not in svg
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in svg and "&amp;" in svg and "&quot;" in svg
    assert "javascript:" not in svg and "claim" not in svg and "a.png" not in svg and "넷째는" not in svg
    assert "alice" not in svg and "SECRET" not in svg and "example.org" in svg and "cohort_v2" in svg
    assert svg.replace('xmlns="http://www.w3.org/2000/svg"', "").count("http") == 0  # no external reference
    assert "href" not in svg and "@import" not in svg and "url(" not in svg and "<style" not in svg
    assert "Malgun Gothic" in svg and "#2848A6" in svg and "#1D6B40" in svg and "#7A3E9D" in svg and "#7A4A00" in svg
    texts = ["".join(node.itertext()) for node in root.iter(SVG_NS + "text")]
    assert all(_width(text) <= UNITS + 6 for text in texts if not text.startswith("labhq가"))  # wrapped, never run off
    assert {"1 질문", "2 데이터", "3 방법·단계", "4 핵심 결과"} <= set(texts)
    assert "외 1단계" in texts and any(text.startswith("5. ") for text in texts)
    assert sum(text.startswith("· ") for text in texts) >= 5  # data sources and findings


def test_the_drawn_abstract_masks_labels_and_survives_empty_input():
    request = {"text": "SECRET-1 질문", "plan": {"steps": [{"id": "s1", "agent_id": "analyst", "instruction": "SECRET-1 분석"}]}}
    svg = build_graphical_abstract(request, "# 결론\n\n- SECRET-1 결과입니다.\n",
                                   scrub=lambda text: text.replace("SECRET-1", "<redacted>"))
    ElementTree.fromstring(svg)
    assert "SECRET-1" not in svg and "&lt;redacted&gt;" in svg
    for empty in ({}, {"plan": None, "references": None}, {"text": None, "plan": {"steps": [None, 3, {}]}}):
        text = build_graphical_abstract(empty, "")
        ElementTree.fromstring(text)
        assert "요청 내용이 기록되지 않았어요" in text and "결론 문장을 찾지 못했어요" in text
    ElementTree.fromstring(build_graphical_abstract({"text": "q"}, "표만 있는 보고서\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"))


def test_the_drawn_abstract_clips_very_long_text():
    request = {"text": "가" * 600, "plan": {"steps": [{"id": f"s{i}", "agent_id": "analyst", "instruction": "나" * 300}
                                                    for i in range(40)]}}
    svg = build_graphical_abstract(request, "# 결론\n\n" + "다 " * 800)
    root = ElementTree.fromstring(svg)
    assert "…" in svg and len(svg) < 40_000 and "외 35단계" in svg
    assert all(_width("".join(node.itertext())) <= UNITS + 6 for node in root.iter(SVG_NS + "text")
               if not "".join(node.itertext()).startswith("labhq가"))


# --- 6. the writer prompts ---------------------------------------------------------------------------------------------

def test_writer_prompts_say_eleven_and_ask_for_the_graphical_abstract():
    rule = cso.REPORT_FIGURE_RULE
    for prompt in (cso.SYNTH_PROMPT, cso.RESEARCH_SYNTH_PROMPT):
        assert rule in prompt
    assert "at most 11 figures" in rule and "up to 10 key result figures" in rule and "at most 6" not in rule
    assert "graphical abstract" in rule and "question -> data -> methods and steps -> key findings" in rule
    assert "graphical_abstract.png (or .svg)" in rule and "read-only synthesis" in rule
    assert "first figure just below the title" in rule and "fallback flowchart" in rule
    assert "<workdir_id>/outputs/<file>" in rule and "never a path that is not listed" in rule
    cso.SYNTH_PROMPT.format(request="r", results="x", review="v", warnings="w", assumptions="a")  # still formats


# --- 7. the edition's PDF goes up to GitHub up to 50 MB ----------------------------------------------------------------

async def test_a_50_mb_pdf_goes_up_in_one_put_with_a_long_write_timeout():
    seen = {}

    def handle(request):
        if request.method == "GET":
            return httpx.Response(404, json={})
        body = json.loads(request.content)
        seen["bytes"] = base64.b64decode(body["content"])
        seen["timeout"] = request.extensions["timeout"]
        seen["wire"] = len(request.content)
        return httpx.Response(201, json={"content": {"html_url": "https://github.com/o/r"}})

    client = GitHubClient("token-for-tests", "https://api.github.test", httpx.MockTransport(handle))
    pdf = b"%PDF-1.7\n" + bytes(50 * MB - 9)
    await client.put_binary("o/r", "labhq/results/req/report.pdf", pdf, "main", "연구 결과 PDF")
    await client.http.aclose()
    assert seen["bytes"] == pdf and seen["wire"] > 66 * MB  # base64 is a third bigger than the bytes
    assert seen["timeout"]["write"] >= 300 and seen["timeout"]["connect"] == 30


async def test_a_file_too_big_for_the_contents_api_is_refused_before_anything_is_sent(monkeypatch):
    def handle(request):
        raise AssertionError("nothing is sent for a file the API would refuse")

    monkeypatch.setattr("labhq.integrations.github.CONTENTS_API_MAX_BYTES", 1000)
    client = GitHubClient("token-for-tests", "https://api.github.test", httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="contents API"):
        await client.put_binary("o/r", "p.pdf", b"x" * 751, "main", "m")  # 751 bytes are 1004 as base64
    await client.http.aclose()
    sent = []
    ok = GitHubClient("token-for-tests", "https://api.github.test", httpx.MockTransport(
        lambda request: sent.append(request.method) or httpx.Response(404 if request.method == "GET" else 201, json={})))
    await ok.put_binary("o/r", "p.pdf", b"x" * 750, "main", "m")  # exactly 1000 as base64
    await ok.http.aclose()
    assert sent == ["GET", "PUT"]


async def test_the_edition_uploads_a_pdf_at_the_default_cap(tmp_path, monkeypatch):
    hub, _edition = prepare(tmp_path, monkeypatch)
    assert hub.s.github.edition_pdf_max_mb == 50
    data = b"%PDF-1.7\n" + b"0" * (5 * MB)
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder([], data))
    approve_all(hub, [])
    calls = []
    await publish(hub, calls)
    assert base64.b64decode(puts(calls)["labhq/results/req_test/report.pdf"]["content"]) == data
    assert hub.requests["req_test"]["result_storage"]["pdf"]["bytes"] == len(data)


def test_example_config_and_manual_state_the_new_default():
    root = Path(__file__).resolve().parent.parent
    for name in ("config/labhq.example.yaml", "labhq/config/labhq.example.yaml"):
        assert re.search(r"edition_pdf_max_mb:\s+50\b", (root / name).read_text(encoding="utf-8")), name
    manual = (root / "docs/manual.md").read_text(encoding="utf-8")
    assert "`github.edition_pdf_max_mb`, 기본 `50`" in manual and "최대 6개" not in manual
    assert "graphical abstract" in manual and "11장" in manual


def test_gateway_report_pdf_says_when_the_figures_were_shrunk(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    settings, request, _tasks, _bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    with_cap(settings, 1)
    fake_printer(monkeypatch, [3 * MB, 500_000])
    app = create_app(settings)
    app.state.hub.requests["req_pdf"] = request
    response = TestClient(app).get("/api/requests/req_pdf/report-pdf",
                                   headers={"Authorization": f"Bearer {settings.gateway.client_token}"})
    assert response.status_code == 200 and response.headers["X-Labhq-Report-Kind"] == "pdf"
    assert "그림을 1단계 줄여 PDF를" in unquote(response.headers["X-Labhq-Reason"])  # the page and the CLI show it
    assert len(response.content) <= 1 * MB
