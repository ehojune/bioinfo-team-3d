"""The repository edition's report.pdf and figure links (#584 item 4). GitHub is a mock transport; the PDF builder is
mocked or, where noted, runs for real with no browser found.

#608 changed the contract these tests pin: the PDF is printed from the request bundle's original figures under the
edition's cleaned text (not from the size-limited edition copies), is never dropped for its size, and the edition opens
with a graphical abstract."""
import base64
from pathlib import Path

import pytest

from labhq.report_figures import classify, image_links
from labhq.report_pdf import NO_BROWSER, ReportFile
from labhq.result_storage import ResultStorage
from labhq.settings import DataZone, GitHubSettings
from tests.test_result_storage import Hub, fake_github

PDF = b"%PDF-1.7\n" + b"synthetic report body " * 80
REPORT = ("# 결과\n\n핵심 그림입니다.\n\n![핵심 그림](wd1/outputs/plot.png)\n\n![미보고 그림](wd1/outputs/extra.png)\n\n"
          "![묶음 밖](../../secret.png)\n\n![로컬 경로](C:/Users/alice/plot.png)\n\n![원격](https://example.org/a.png)\n\n"
          "코드 안의 `![예](wd1/outputs/none.png)`는 그대로 둡니다.\n")


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    bundle = Path(hub.s.runner.workspace_root) / "requests/req_test"
    outputs = bundle / "steps/s1/outputs"
    outputs.mkdir(parents=True)
    (outputs / "plot.png").write_bytes(b"\x89PNG synthetic plot")
    (outputs / "extra.png").write_bytes(b"\x89PNG synthetic extra")
    (bundle / "MANIFEST.tsv").write_text("relative_path\tstatus\nsteps/s1/outputs/plot.png\tcopied\n"
                                         "steps/s1/outputs/extra.png\tcopied (unreported)\n", encoding="utf-8")
    hub.requests["req_test"].update(
        status="done", pi_reviewed_at=1, report=REPORT,
        results={"s1": {"workdir_id": "wd1", "outputs": ["outputs/plot.png", "outputs/extra.png"]}})
    return hub, Path(hub.s.runner.workspace_root) / "requests/req_test-editor"


def builder(seen, data=PDF, **flags):
    def build(req, settings, tasks=None, *, source=None, report_text=None, scrub=None, **kwargs):
        seen.append({"source": source, "req": req, "report": report_text, "scrub": scrub,
                     "files": sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file())})
        return ReportFile("pdf", data, "", "req_test-report.pdf", "application/pdf", **flags)
    return build


def approve_all(hub, cards):
    async def approve(**kw):
        cards.append(kw)
        return {"approved": True}
    hub.request_approval = approve


def puts(calls):
    return {path.split("/contents/")[1]: body for method, path, body in calls if method == "PUT"}


async def publish(hub, calls):
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    await service.finish("req_test")
    await service.close()


async def test_pdf_is_in_the_edition_and_uploaded_as_binary(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    seen, calls, cards = [], [], []
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder(seen))
    approve_all(hub, cards)
    await publish(hub, calls)
    bundle = edition.with_name("req_test")
    assert len(seen) == 1 and seen[0]["source"] == bundle  # #608: the bundle's original figures, not the edition's copies
    assert "steps/s1/outputs/plot.png" in seen[0]["files"] and "steps/s1/outputs/extra.png" in seen[0]["files"]
    assert not (edition / "steps/s1/outputs/extra.png").exists()  # an unreported output: the edition leaves it out
    # #611 review: and so does the PDF, whose figures are the edition's reported outputs, only without the size limit
    assert "(steps/s1/outputs/plot.png)" in seen[0]["report"] and "extra.png" not in seen[0]["report"]
    assert "*(그림 생략: 미보고 그림)*" in seen[0]["report"]
    assert "(wd1/outputs/plot.png)" not in seen[0]["report"]
    assert seen[0]["req"]["report_appendix"] == "" and "alice" not in seen[0]["report"]  # cleaned like the edition
    assert (edition / "report.pdf").read_bytes() == PDF
    sent = puts(calls)
    pdf = sent["labhq/results/req_test/report.pdf"]
    assert base64.b64decode(pdf["content"]) == PDF and pdf["message"].startswith("연구 결과 PDF: labhq/results/req_test/report.pdf")
    assert "(report.pdf)" in base64.b64decode(sent["labhq/results/req_test/README.md"]["content"]).decode()
    assert "(report.pdf)" in (edition / "README.md").read_text(encoding="utf-8")
    assert cards[0]["detail"]["pdf"].startswith("포함: report.pdf")
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    assert hub.requests["req_test"]["result_storage"]["pdf"]["included"] is True


@pytest.mark.parametrize("case, reason", [
    ("no_browser", "Edge나 Chrome을 찾지 못했어요"),
    ("print_failed", "브라우저가 PDF를 만들지 못했어요"),
    ("raises", "오류가 나서"),
    ("real_builder_no_browser", "Edge나 Chrome을 찾지 못했어요"),
])
async def test_pdf_skipped_with_a_reason_and_publishing_still_goes_through(tmp_path, monkeypatch, case, reason):
    hub, edition = prepare(tmp_path, monkeypatch)
    calls, cards = [], []
    if case == "no_browser":
        monkeypatch.setattr("labhq.result_storage.build_report_file", lambda *a, **k: ReportFile(
            "html", b"<html></html>", NO_BROWSER, "x.html", "text/html"))
    elif case == "print_failed":
        monkeypatch.setattr("labhq.result_storage.build_report_file", lambda *a, **k: ReportFile(
            "html", b"<html></html>", "브라우저가 PDF를 만들지 못해 HTML로 드립니다(종료 코드 1).", "x.html", "text/html"))
    elif case == "raises":
        def boom(*a, **k):
            raise RuntimeError("printer exploded at C:/Users/alice/secret")
        monkeypatch.setattr("labhq.result_storage.build_report_file", boom)
    else:  # the real builder; a browser may exist on this PC, so none is found
        monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: None)
    approve_all(hub, cards)
    await publish(hub, calls)
    line = cards[0]["detail"]["pdf"]
    assert line.startswith("PDF는 포함하지 않았습니다: ") and reason in line and "alice" not in line
    assert not (edition / "report.pdf").exists()
    sent = puts(calls)
    assert "labhq/results/req_test/report.pdf" not in sent
    readme = base64.b64decode(sent["labhq/results/req_test/README.md"]["content"]).decode()
    assert line in readme and "(report.pdf)" not in readme
    assert line in (edition / "README.md").read_text(encoding="utf-8")
    assert "labhq/results/req_test/report.md" in sent  # the rest of the edition is published
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"


async def test_pdf_over_the_warning_threshold_is_kept_without_shrinking(tmp_path, monkeypatch):
    """#690: a configured size is warning-only."""
    hub, edition = prepare(tmp_path, monkeypatch)
    assert hub.s.github.edition_pdf_max_mb is None
    hub.s.github.edition_pdf_max_mb = 1
    data = b"%PDF-1.7\n" + b"0" * (1024 * 1024)  # one byte over 1 MB
    seen, calls, cards = [], [], []
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder(seen, data, shrunk=0, over_cap=True))
    approve_all(hub, cards)
    await publish(hub, calls)
    note = "PDF가 1.0 MB로 설정 알림 기준 1 MB를 넘지만 그림과 PDF를 줄이지 않았습니다."
    assert cards[0]["detail"]["pdf"] == f"포함: report.pdf (1.0 MB) · {note}"
    assert base64.b64decode(puts(calls)["labhq/results/req_test/report.pdf"]["content"]) == data
    assert note in (edition / "README.md").read_text(encoding="utf-8") and (edition / "report.pdf").read_bytes() == data
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "published" and saved["pdf"]["included"] and saved["pdf"]["size_note"] == note


async def test_result_storage_has_no_separate_pdf_size_rejection(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    seen, calls, cards = [], [], []
    monkeypatch.setattr("labhq.result_storage.build_report_file",
                        builder(seen, b"%PDF-1.7\n" + b"0" * (1024 * 1024)))
    approve_all(hub, cards)
    await publish(hub, calls)
    assert cards[0]["detail"]["pdf"].startswith("포함: report.pdf")
    assert "labhq/results/req_test/report.pdf" in puts(calls) and (edition / "report.pdf").exists()
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"


async def test_pdf_exactly_at_the_cap_is_kept(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    hub.s.github.edition_pdf_max_mb = 1
    data = b"%PDF-1.7\n" + b"0" * (1024 * 1024 - 9)
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder([], data))
    calls = []
    approve_all(hub, [])
    await publish(hub, calls)
    assert base64.b64decode(puts(calls)["labhq/results/req_test/report.pdf"]["content"]) == data


def test_cap_setting_is_positive():
    assert GitHubSettings().edition_pdf_max_mb is None
    assert GitHubSettings(edition_pdf_max_mb=76).edition_pdf_max_mb == 76
    for bad in (0, -1):
        with pytest.raises(ValueError):
            GitHubSettings(edition_pdf_max_mb=bad)


async def test_figure_links_resolve_inside_the_edition(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: None)
    service = ResultStorage(hub, fake_github([], exists=True))
    await service.select("req_test", {"mode": "local"})
    await service.finish("req_test")
    await service.close()
    kept = 0
    for name in ("report.md", "README.md"):
        text = (edition / name).read_text(encoding="utf-8")
        for link in image_links(text):
            kind, value = classify(link.target)
            if kind == "remote":
                assert link.target.startswith("https://example.org/")  # not a path into the bundle
                continue
            assert kind == "local" and (edition / value).is_file(), (name, link.target)
            kept += 1
    assert kept == 4  # plot.png and the graphical abstract (#608), in report.md and in the README excerpt
    report = (edition / "report.md").read_text(encoding="utf-8")
    assert "![핵심 그림](steps/s1/outputs/plot.png)" in report
    assert [link.target for link in image_links(report)][:2] == ["graphical_abstract.svg", "steps/s1/outputs/plot.png"]
    assert "*(그림 생략: 미보고 그림)*" in report and "*(그림 생략: 묶음 밖)*" in report
    assert "*(그림 생략: 로컬 경로)*" in report and "alice" not in report
    assert "`![예](wd1/outputs/none.png)`" in report  # code stays as written
    assert (edition / "steps/s1/outputs/plot.png").is_file() and not (edition / "steps/s1/outputs/extra.png").exists()


async def test_controlled_request_is_not_published_or_printed(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    seen, calls = [], []
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder(seen))
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    hub.s.policy.data_zones = [DataZone(path="/controlled", level="restricted")]
    hub.requests["req_test"]["text"] = "Analyze /controlled/cohort.tsv"
    async def never(**kw):
        raise AssertionError("no publish card for a controlled-data request")
    hub.request_approval = never
    await service.finish("req_test")
    await service.close()
    assert hub.requests["req_test"]["result_storage"]["status"] == "local"
    assert seen == [] and not edition.exists()
    assert not any(c[0] in {"POST", "PUT"} for c in calls)


async def test_resumed_draft_uploads_the_pdf_it_already_printed(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    seen, calls, answers = [], [], iter([{"approved": False, "state": "timed_out"}, {"approved": True}])
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder(seen))
    cards = []
    async def answer(**kw):
        cards.append(kw)
        return next(answers)
    hub.request_approval = answer
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft" and not puts(calls)
    await service.finish("req_test")  # reads the folder back, binary PDF included
    await service.close()
    assert len(seen) == 1 and hub.requests["req_test"]["result_storage"]["status"] == "published"
    assert base64.b64decode(puts(calls)["labhq/results/req_test/report.pdf"]["content"]) == PDF
    assert cards[1]["detail"]["pdf"].startswith("포함: report.pdf")


async def test_resumed_legacy_draft_uses_report_md_for_korean_qc(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    edition.mkdir(parents=True)
    (edition / "README.md").write_text("# 정리본\n\n[전체 결과·그림·표](report.md)\n", encoding="utf-8")
    (edition / "report.md").write_text("# 결과\n\n기존 정리본입니다.\n", encoding="utf-8")
    saved = hub.requests["req_test"]["result_storage"] = {
        "mode": "new", "repo": "owner/study", "status": "draft", "draft_path": str(edition),
    }
    cards = []
    approve_all(hub, cards)
    calls = []
    service = ResultStorage(hub, fake_github(calls, exists=True))

    await service.finish("req_test")
    await service.close()

    assert "report/report.md" in saved["korean_qc"]
    assert cards[0]["detail"]["korean_qc"] == saved["korean_qc"]
    assert not (edition / "report.md").exists()
    assert (edition / "report" / "report.md").is_file()
    assert "](report/report.md)" in (edition / "README.md").read_text(encoding="utf-8")
    assert "](report.md)" not in (edition / "README.md").read_text(encoding="utf-8")
    assert "README.md" in puts(calls)
    uploaded_readme = base64.b64decode(puts(calls)["README.md"]["content"]).decode()
    assert "](report/report.md)" in uploaded_readme and "](report.md)" not in uploaded_readme
    assert "labhq/results/req_test/report/report.md" not in puts(calls)
    assert saved["report_url"].endswith("/report/report.md")


async def test_pdf_uses_the_canonical_revised_report_candidate(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    bundle = edition.with_name("req_test")
    final = ("# 최종 과학 보고서\n\n## 요약(Abstract)\n\n최종 원고의 결론은 B다.\n\n## 서론\n\n질문을 검토했다.\n\n"
             "## 방법\n\n공개 자료를 분석했다.\n\n## 결과\n\n![최종 그림](wd1/outputs/final.svg)\n\n"
             "## 논의\n\n한계를 확인했다.\n\n## 결론\n\n최종 원고의 결론은 B다.\n")
    report = bundle / "steps/s1/outputs/report/final_report.md"
    figure = bundle / "steps/s1/outputs/final.svg"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(final, encoding="utf-8")
    figure.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><text>final</text></svg>')
    with (bundle / "MANIFEST.tsv").open("a", encoding="utf-8") as handle:
        handle.write("steps/s1/outputs/report/final_report.md\tcopied\n")
        handle.write("steps/s1/outputs/final.svg\tcopied\n")
    request = hub.requests["req_test"]
    request.update(outcome="research_review_revise", review={"verdict": "revise", "issues": []})
    request["results"]["s1"]["outputs"].extend(["outputs/report/final_report.md", "outputs/final.svg"])
    seen, calls = [], []
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder(seen))
    approve_all(hub, [])

    await publish(hub, calls)

    canonical = (edition / "report" / "report.md").read_text(encoding="utf-8")
    assert "최종 원고의 결론은 B다." in canonical
    assert "최종 원고의 결론은 B다." in seen[0]["report"]
    assert "steps/s1/outputs/final.svg" in seen[0]["report"]
    assert "../figures/final.svg" not in seen[0]["report"]


async def test_resumed_draft_refuses_a_file_that_is_not_a_pdf(tmp_path, monkeypatch):
    hub, edition = prepare(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("labhq.result_storage.build_report_file", builder([]))
    answers = iter([{"approved": False, "state": "timed_out"}, {"approved": True}])
    async def answer(**kw):
        return next(answers)
    hub.request_approval = answer
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    await service.finish("req_test")
    (edition / "report.pdf").write_bytes(b"<html>not a pdf</html>")
    await service.finish("req_test")
    await service.close()
    assert hub.requests["req_test"]["result_storage"]["status"] == "failed" and not puts(calls)
