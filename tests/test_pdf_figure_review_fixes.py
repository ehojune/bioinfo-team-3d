"""Three P2 findings of the review of #611 (follow-up to #608).

1. The published PDF shows only figures the reviewed edition may carry (reported outputs of the manifest, a type the edition
   keeps): the size limit is lifted, the eligibility is not.
2. A graphical abstract the report links is moved to be the first figure, under the first heading.
3. `labhq verify` reserves the drawn abstract's place: 10 result figures at most when the report has no abstract of its own.
"""
import base64

from labhq.evidence.audit import verify_request
from labhq.report_abstract import abstract_first
from labhq.report_figures import bundle_figure_check, image_links
from labhq.report_pdf import build_report_html
from labhq.result_storage import edition_assets, edition_candidates, pdf_report_text
from tests.test_pdf_figure_policy import fake_printer, figure_report, image_sources
from tests.test_report_pdf import PNG, build
from tests.test_result_storage_pdf import approve_all, prepare, publish


def b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def add_bundle_file(bundle, name, payload, status):
    """A file in the request bundle and its MANIFEST row (``status`` as `labhq` writes it: copied, copied (unreported))."""
    (bundle / "steps/s1/outputs" / name).write_bytes(payload)
    if status:
        with (bundle / "MANIFEST.tsv").open("a", encoding="utf-8") as handle:
            handle.write(f"steps/s1/outputs/{name}\t{status}\n")


# --- 1. the PDF may show only what the edition may carry ---------------------------------------------------------------

async def test_the_published_pdf_drops_figures_the_reviewed_edition_leaves_out(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    private, gif, big = b"\x89PNG private synthetic", b"GIF89a synthetic animation", b"\x89PNG big " + b"\x00" * 1_500_000
    add_bundle_file(bundle, "private.png", private, None)  # in the bundle, in no manifest row, in no step's outputs
    add_bundle_file(bundle, "shot.gif", gif, "copied")  # reported, but a type the edition does not keep
    add_bundle_file(bundle, "big.png", big, "copied")  # reported and only too large for the edition
    request = hub.requests["req_test"]
    request["report"] += ("\n\n![비공개 그림](steps/s1/outputs/private.png)\n\n![움직이는 그림](wd1/outputs/shot.gif)\n\n"
                          "![큰 그림](wd1/outputs/big.png)\n")
    request["results"]["s1"]["outputs"] += ["outputs/shot.gif", "outputs/big.png"]
    pages = []
    fake_printer(monkeypatch, [4096], pages)
    approve_all(hub, [])
    await publish(hub, [])
    page = pages[0]
    for omitted in (private, gif, b"\x89PNG synthetic extra"):  # extra.png is an unreported output in the manifest
        assert b64(omitted) not in page
    assert b64(big) in page and b64(b"\x89PNG synthetic plot") in page  # the size limit is lifted, nothing else
    for note in ("비공개 그림", "움직이는 그림", "미보고 그림"):
        assert f"(그림 생략: {note})" in page  # dropped with a note, as in the edition
    edition_report = (edition / "report.md").read_text(encoding="utf-8")
    for note in ("비공개 그림", "움직이는 그림", "미보고 그림"):
        assert f"*(그림 생략: {note})*" in edition_report
    assert "![큰 그림](steps/s1/outputs/big.png)" in edition_report


async def test_cleaned_markdown_and_pdf_use_the_same_figure_set(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    pages = []
    fake_printer(monkeypatch, [4096], pages)
    approve_all(hub, [])

    await publish(hub, [])

    report_path = edition / "report.md"
    markdown = report_path.read_text(encoding="utf-8")
    markdown_figures = {
        (edition / link.target).read_bytes()
        for link in image_links(markdown)
        if (edition / link.target).is_file()
    }
    pdf_figures = {
        base64.b64decode(source.split(",", 1)[1])
        for source, _alt in image_sources(pages[0])
        if source.startswith("data:")
    }
    assert {body for body in pdf_figures if not body.startswith(b"<svg")} == {
        body for body in markdown_figures if not body.startswith(b"<svg")}
    run_pictures = {(edition / name).read_bytes() for name in ("run/pipeline_diagram.svg", "run/run_timeline.svg")}
    assert run_pictures <= pdf_figures  # the edition carries the run pictures the PDF shows (#756)
    assert sum(body.startswith(b"<svg") for body in pdf_figures - run_pictures) == 1
    assert sum(body.startswith(b"<svg") for body in markdown_figures) == 1


def test_edition_candidates_is_the_eligibility_edition_assets_copies_by(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    add_bundle_file(bundle, "private.png", b"\x89PNG p", None)
    add_bundle_file(bundle, "shot.gif", b"GIF89a", "copied")
    add_bundle_file(bundle, "big.png", b"\x89PNG big " + b"\x00" * 1_500_000, "copied")
    add_bundle_file(bundle, "notes.md", b"# notes", "copied")
    request = hub.requests["req_test"]
    names = set(edition_candidates(request, hub.s))
    assert names == {"steps/s1/outputs/plot.png", "steps/s1/outputs/big.png", "steps/s1/outputs/notes.md"}
    assets, images = edition_assets(request, hub.s)
    assert set(images) == {"steps/s1/outputs/plot.png", "steps/s1/outputs/big.png"}
    assert set(assets) == {"steps/s1/outputs/notes.md"}
    text = pdf_report_text({**request, "report": "![a](steps/s1/outputs/big.png) ![b](steps/s1/outputs/private.png)"}, hub.s)
    assert "![a](steps/s1/outputs/big.png)" in text and "private.png" not in text and "(그림 생략: b)" in text


async def test_an_unreported_abstract_is_not_printed_and_a_drawn_one_takes_its_place(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    secret = b"\x89PNG unreported abstract"
    add_bundle_file(bundle, "graphical_abstract.png", secret, "copied (unreported)")
    request = hub.requests["req_test"]
    request["report"] = request["report"].replace("# 결과\n\n", "# 결과\n\n![Graphical abstract](steps/s1/outputs/graphical_abstract.png)\n\n", 1)
    pages = []
    fake_printer(monkeypatch, [4096], pages)
    approve_all(hub, [])
    await publish(hub, [])
    sources = image_sources(pages[0])
    assert b64(secret) not in pages[0] and sources[0][0].startswith("data:image/svg+xml;base64,")
    assert "graphical_abstract.png" not in (edition / "report.md").read_text(encoding="utf-8")


# --- 2. the report's own abstract comes first --------------------------------------------------------------------------

def test_the_pdf_moves_the_reports_own_abstract_to_the_first_figure_under_the_first_heading(tmp_path):
    files = {"outputs/fig1.png": PNG, "outputs/graphical_abstract.png": PNG + b"abstract"}
    report = ("# 결론과 권고\n\n결론 문단.\n\n## 결과\n\n![첫 결과 그림](WD/outputs/fig1.png)\n\n"
              "![Graphical abstract](WD/outputs/graphical_abstract.png)\n\n마지막 문단.\n")
    _settings, request, _tasks, bundle = build(tmp_path, report, files=files)
    page, missing = build_report_html(request, bundle)
    sources = image_sources(page)
    assert missing == [] and [alt for _src, alt in sources] == ["Graphical abstract", "첫 결과 그림"]  # no drawn one added
    assert sources[0][0] == f"data:image/png;base64,{b64(PNG + b'abstract')}"
    assert page.index("<h2>결론과 권고</h2>") < page.index("Graphical abstract") < page.index("결론 문단.")
    assert "마지막 문단." in page and page.count("<figure>") == 2
    assert (bundle / "report.md").read_text(encoding="utf-8").index("fig1.png") < (
        bundle / "report.md").read_text(encoding="utf-8").index("graphical_abstract.png")  # the bundle's text is untouched


async def test_the_edition_opens_with_the_reports_own_abstract_too(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    add_bundle_file(edition.with_name("req_test"), "graphical_abstract.png", b"\x89PNG own abstract", "copied")
    request = hub.requests["req_test"]
    request["report"] += "\n\n![Graphical abstract](wd1/outputs/graphical_abstract.png)\n"
    request["results"]["s1"]["outputs"].append("outputs/graphical_abstract.png")
    fake_printer(monkeypatch, [4096])
    approve_all(hub, [])
    await publish(hub, [])
    report_targets = [link.target for link in image_links((edition / "report.md").read_text(encoding="utf-8"))]
    readme_targets = [link.target for link in image_links((edition / "README.md").read_text(encoding="utf-8"))]
    assert report_targets[0] == "steps/s1/outputs/graphical_abstract.png"
    assert readme_targets[0] == "figures/graphical_abstract.svg"
    assert (edition / "figures/graphical_abstract.svg").exists()  # the portfolio publishes SVG only


def test_abstract_first_moves_only_a_valid_abstract_and_keeps_everything_else():
    holds = {"steps/s1/outputs/graphical_abstract.png", "steps/s1/outputs/a.png"}.__contains__
    report = ("# 제목\n\n본문 하나.\n\n![그림 A](steps/s1/outputs/a.png)\n\n본문 둘.\n\n"
              '![Graphical abstract](steps/s1/outputs/graphical_abstract.png "제목")\n\n끝.\n')
    moved = abstract_first(report, holds)
    assert moved == ('# 제목\n\n![Graphical abstract](steps/s1/outputs/graphical_abstract.png "제목")\n\n본문 하나.\n\n'
                     "![그림 A](steps/s1/outputs/a.png)\n\n본문 둘.\n\n끝.\n")
    assert [link.target for link in image_links(moved)] == ["steps/s1/outputs/graphical_abstract.png",
                                                            "steps/s1/outputs/a.png"]
    assert abstract_first(moved, holds) == moved  # already first: nothing moves
    assert abstract_first(report, {"steps/s1/outputs/a.png"}.__contains__) == report  # no such file: left alone
    assert abstract_first("# 제목\n\n![A](steps/s1/outputs/a.png)\n", holds) == "# 제목\n\n![A](steps/s1/outputs/a.png)\n"
    inline = "# 제목\n\n![A](steps/s1/outputs/a.png)\n\n문장 속 ![abs](steps/s1/outputs/graphical_abstract.png) 그림.\n"
    assert abstract_first(inline, holds) == ("# 제목\n\n![abs](steps/s1/outputs/graphical_abstract.png)\n\n"
                                             "![A](steps/s1/outputs/a.png)\n\n문장 속  그림.\n")
    fenced = ("```\n# 코드 안 제목\n```\n\n## 첫 제목\n\n![A](steps/s1/outputs/a.png)\n\n"
              "![abs](steps/s1/outputs/graphical_abstract.png)\n")
    shifted = abstract_first(fenced, holds)  # a heading inside a code fence is not the first heading
    assert shifted == ("```\n# 코드 안 제목\n```\n\n## 첫 제목\n\n![abs](steps/s1/outputs/graphical_abstract.png)\n\n"
                       "![A](steps/s1/outputs/a.png)\n")
    code = "# 제목\n\n![A](steps/s1/outputs/a.png)\n\n`![abs](steps/s1/outputs/graphical_abstract.png)`\n"
    assert abstract_first(code, holds) == code  # a link inside code is not a figure
    assert abstract_first("그림만 있는 글 ![abs](steps/s1/outputs/graphical_abstract.png)", holds) == (
        "그림만 있는 글 ![abs](steps/s1/outputs/graphical_abstract.png)")


# --- 3. verify reserves the drawn abstract's place -----------------------------------------------------------------------

def test_verify_counts_the_drawn_abstract_against_the_eleven(tmp_path):
    ten = {f"outputs/f{index}.png": PNG for index in range(10)}
    eleven = {f"outputs/f{index}.png": PNG for index in range(11)}
    _s, request, _t, bundle = build(tmp_path / "ten", figure_report(10), files=ten)
    assert bundle_figure_check(bundle)["problems"] == []  # 10 results and the drawn abstract make 11
    page, missing = build_report_html(request, bundle)
    assert page.count("<img ") == 11 and missing == []

    settings, request, _t, bundle = build(tmp_path / "eleven", figure_report(11), files=eleven)
    problems = bundle_figure_check(bundle)["problems"]
    assert len(problems) == 1 and "11장이에요" in problems[0] and "결과 그림은 10장까지" in problems[0]
    assert verify_request(request, settings)["exit_code"] == 1  # WARN does not turn a completed problem check into exit 2
    page, missing = build_report_html(request, bundle)
    assert page.count("<img ") == 11 and len(missing) == 1 and "11장까지만" in missing[0]


def test_an_abstract_of_its_own_keeps_all_eleven_places(tmp_path):
    own = {f"outputs/f{index}.png": PNG for index in range(10)} | {"outputs/graphical_abstract.png": PNG}
    report = ("# 결론과 권고\n\n![Graphical abstract](WD/outputs/graphical_abstract.png)\n\n"
              + "\n\n".join(f"![그림 {index}](WD/outputs/f{index}.png)" for index in range(10)) + "\n")
    settings, request, _t, bundle = build(tmp_path / "own", report, files=own)
    checked = bundle_figure_check(bundle)
    assert checked["problems"] == [] and checked["warnings"]
    assert verify_request(request, settings)["exit_code"] == 0
    twelve = own | {"outputs/f10.png": PNG}
    report12 = report + "\n![그림 10](WD/outputs/f10.png)\n"
    _s, _r, _t, bundle12 = build(tmp_path / "own12", report12, files=twelve)
    problems = bundle_figure_check(bundle12)["problems"]
    assert len(problems) == 1 and "12장이에요" in problems[0] and "11장까지(결과 그림 10장과 graphical abstract 1장)" in problems[0]
    # a link to an abstract the bundle does not hold is no abstract of its own
    (bundle / "report.md").write_text(report.replace("WD/outputs/graphical_abstract.png", "steps/s1/outputs/gone.png")
                                      .replace("![그림 9]", "![그림 9](steps/s1/outputs/f9.png) ![그림 10]"), encoding="utf-8")
    assert any("결과 그림은 10장까지" in problem for problem in bundle_figure_check(bundle)["problems"])

