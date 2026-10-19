"""Figures in the report and the on-demand PDF report (#584)."""
import base64
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import httpx
import pytest

from labhq.cli import _verify, main
from labhq.evidence.audit import render_verify, verify_request
from labhq.orchestrator import cso
from labhq.report_figures import (bundle_figure_check, classify, image_links, link_workdir_figures, linked_figure_paths,
                                  output_exists)
from labhq.report_pdf import (NO_BROWSER, FigureEmbedder, ReportRefused, build_report_file, build_report_html,
                              find_browser, markdown_to_html, print_pdf)
from labhq.request_bundle import build_request_bundle
from labhq.settings import DataZone, Settings

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><text x="4" y="24">한글 그림</text></svg>'
FAKE_PDF = b"%PDF-1.4\n" + b"0" * 3000


def configured(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    return settings


def build(tmp_path: Path, report: str, *, settings: Settings | None = None, files: dict[str, bytes] | None = None,
          extra: dict | None = None):
    """A finished one-step request whose step wrote the given files; returns (settings, request, tasks, bundle)."""
    settings = settings or configured(tmp_path)
    workdir = Path(settings.runner.workspace_root) / "2026-10-14" / "task_s1_analyst"
    workdir.mkdir(parents=True)
    (workdir / "manifest.json").write_text(json.dumps({"host": platform.node()}), encoding="utf-8")
    files = files or {"outputs/fig1.png": PNG, "outputs/fig2.svg": SVG.encode("utf-8")}
    for name, payload in files.items():
        (workdir / name).parent.mkdir(parents=True, exist_ok=True)
        (workdir / name).write_bytes(payload)
    result = {"task_id": "t1", "agent_id": "analyst", "ok": True, "workdir": str(workdir),
              "workdir_id": workdir.name, "outputs": list(files),
              "output_sha256": {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()}}
    request = {"id": "req_pdf", "text": "TP53 변이를 사례군과 대조군에서 비교해 주세요.", "mode": "orchestrate",
               "status": "done", "created_at": 1760400000.0, "report": report.replace("WD", workdir.name),
               "report_appendix": "## 부록\n\n- 과학 리뷰 완료\n", "execution_record": "- s1 완료", "results": {"s1": result},
               "plan": {"steps": [{"id": "s1", "depends_on": [], "outputs": list(files)}]}, **(extra or {})}
    tasks = {"t1": {"request_id": "req_pdf", "kind": "step", "completed": True, "result": result}}
    built = build_request_bundle(request, settings, tasks)
    return settings, request, tasks, Path(built["path"])


REPORT = """# 결론과 권고

TP53 변이가 두 군에서 다르다 [[claim:s1/c1]]. **굵게** *기울임* `code`.

## 결과

| 군 | 개수 |
|---|---:|
| 사례 | 12 |
| 대조 | a \\| b |

![군별 변이 빈도](WD/outputs/fig1.png)

![한글 그림](WD/outputs/fig2.svg)

- 첫째
  - 중첩
- 둘째

## 한계

표본이 작다. <script>alert(1)</script>
"""


def fake_browser(monkeypatch, *, calls: list | None = None, returncode: int = 0, write: bool = True):
    """A stand-in for the headless browser: it writes the PDF where --print-to-pdf says."""
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: "C:/fake/msedge.exe")

    def run(command, **kwargs):
        if calls is not None:
            calls.append((command, kwargs))
        target = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--print-to-pdf="))
        if write:
            Path(target).write_bytes(FAKE_PDF)
        return subprocess.CompletedProcess(command, returncode, b"", b"")

    monkeypatch.setattr("labhq.report_pdf.subprocess.run", run)


# --- figures in the bundle and in verify -----------------------------------------------------------------------------

def test_bundle_rewrites_workdir_figure_links_and_verify_accepts_them(tmp_path):
    settings, request, _tasks, bundle = build(tmp_path, REPORT + "\n```\n![code](WD/outputs/fig1.png)\n```\n")
    report = (bundle / "report.md").read_text(encoding="utf-8")
    assert "![군별 변이 빈도](steps/s1/outputs/fig1.png)" in report
    assert "![한글 그림](steps/s1/outputs/fig2.svg)" in report
    assert "![code](task_s1_analyst/outputs/fig1.png)" in report  # inside a code fence it stays text
    assert (bundle / "steps/s1/outputs/fig1.png").read_bytes() == PNG
    check = bundle_figure_check(bundle)
    assert check["linked"] == 2 and check["problems"] == []
    assert any("같은 이름의 SVG가 없습니다" in warning for warning in check["warnings"])
    verified = verify_request(request, settings)
    assert verified["exit_code"] == 0, verified
    assert verified["warnings"] and verified["reasons"] == []
    assert "보고서 그림: 링크 2개, 모두 묶음 안에 있음" in render_verify(verified)


def test_bundle_keeps_a_linked_figure_past_the_total_limit_but_not_other_files(tmp_path):
    settings = configured(tmp_path)
    settings.runner.bundle_max_total_mb = 1024 / (1024 * 1024)  # 1,024 bytes in all
    files = {"outputs/a.txt": b"a" * 1000, "outputs/fig.png": PNG, "outputs/b.txt": b"b" * 600}
    _settings, _request, _tasks, bundle = build(
        tmp_path, "# 결과\n\n![그림](WD/outputs/fig.png)\n", settings=settings, files=files)
    rows = {row.split("\t")[0]: row for row in (bundle / "MANIFEST.tsv").read_text(encoding="utf-8").splitlines()}
    assert "copied" in rows["steps/s1/outputs/fig.png"] and "not copied" not in rows["steps/s1/outputs/fig.png"]
    assert (bundle / "steps/s1/outputs/fig.png").is_file()
    assert "not copied: total limit" in rows["steps/s1/outputs/b.txt"]


def test_verify_fails_when_a_linked_image_is_missing_or_points_outside_the_bundle(tmp_path):
    report = ("# 결과\n\n![없음](WD/outputs/nope.png)\n\n![밖](C:/secret/data/plot.png)\n\n"
              "![원격](https://example.com/a.png)\n\n![형식](WD/outputs/fig1.png)\n")
    settings, request, _tasks, bundle = build(tmp_path, report)
    verified = verify_request(request, settings)
    problems = [p for p in verified["problems"] if p.startswith("보고서 그림")]
    assert verified["exit_code"] == 1
    assert any("묶음에 없습니다: steps/s1/outputs/nope.png" in p for p in problems)
    assert any("묶음 밖 경로" in p and "plot.png" in p for p in problems)
    assert not any("secret" in p or "example.com" in p for p in problems)  # no private path is echoed; remote is skipped
    assert len(problems) == 2
    assert "보고서 그림: 링크 3개, 문제 2건" in render_verify(verified)
    appendix = (bundle / "execution_record.md").read_text(encoding="utf-8")
    assert "## 보고서 그림 링크" in appendix and "nope.png" in appendix


def test_verify_notices_a_linked_image_deleted_from_the_bundle_and_an_empty_one(tmp_path):
    settings, request, _tasks, bundle = build(tmp_path, REPORT)
    (bundle / "steps/s1/outputs/fig1.png").write_bytes(b"")
    (bundle / "steps/s1/outputs/fig2.svg").unlink()
    problems = bundle_figure_check(bundle)["problems"]
    assert any("비어 있습니다" in p and "fig1.png" in p for p in problems)
    assert any("묶음에 없습니다" in p and "fig2.svg" in p for p in problems)
    assert any(p.startswith("보고서 그림") for p in verify_request(request, settings)["problems"])


def test_figure_link_rewrite_only_touches_listed_output_paths():
    owners = {"task_a": "s1"}
    text = "![x](task_a/outputs/my fig.png) ![y](<task_a/outputs/한글 그림.png>) ![z](other/outputs/q.png) ![w](task_a/outputs/../x.png)"
    out = link_workdir_figures(text, owners)
    assert "![x](task_a/outputs/my fig.png)" in out  # a bare path with a space is not markdown: untouched
    assert "![y](steps/s1/outputs/%ED%95%9C%EA%B8%80%20%EA%B7%B8%EB%A6%BC.png)" in out
    assert "![z](other/outputs/q.png)" in out and "![w](task_a/outputs/../x.png)" in out
    assert linked_figure_paths("![a](steps/s1/outputs/a.png) ![b](steps/s1/outputs/b.csv) ![c](http://x/y.png)") == [
        "steps/s1/outputs/a.png"]


def test_synthesis_prompts_ask_for_listed_figures_only():
    for prompt in (cso.SYNTH_PROMPT, cso.RESEARCH_SYNTH_PROMPT):
        assert cso.REPORT_FIGURE_RULE in prompt
    assert "<workdir_id>/outputs/<file>" in cso.REPORT_FIGURE_RULE and "never a path that is not listed" in cso.REPORT_FIGURE_RULE


# --- the HTML page ---------------------------------------------------------------------------------------------------

def test_html_embeds_figures_korean_text_and_allows_no_network(tmp_path):
    _settings, request, _tasks, bundle = build(tmp_path, REPORT)
    page, missing = build_report_html(request, bundle)
    assert missing == []
    assert page.count("data:image/png;base64,") == 1 and "data:image/svg+xml;base64," in page
    assert base64.b64encode(PNG).decode() in page
    for text in ("결론과 권고", "TP53 변이가 두 군에서 다르다", "한글 그림", "labhq 결과 보고서", "요청 번호", "완료"):
        assert text in page
    assert '<html lang="ko">' in page and "Malgun Gothic" in page and "Noto Sans CJK KR" in page
    assert "@page" in page and "break-after: page" in page
    assert "default-src 'none'; img-src data:" in page  # nothing is fetched when the page is printed
    assert "<table>" in page and "<ul><li>첫째<ul><li>중첩</li></ul></li><li>둘째</li></ul>" in page
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "[[claim:" not in page and "[근거 1]" in page
    assert "과학 부록" in page and "과학 리뷰 완료" in page
    assert page.index("<h1>결론과 권고</h1>") < page.index("TP53 변이를 사례군")
    assert page.index("<main class=\"report\">") < page.index("과학 부록")


def test_cover_uses_the_complete_report_title_and_print_css_keeps_figures_readable(tmp_path):
    title = "아버지의 컴퓨터 직업과 자녀 성비: 인구 출생 자료, 기전 문헌, 정자 메틸화 분석"
    _settings, request, _tasks, bundle = build(
        tmp_path,
        f"# {title}\n\n![그래픽 초록](steps/s1/outputs/graphical_abstract.svg)\n\n그림 설명입니다.\n",
        files={"outputs/graphical_abstract.svg": SVG.encode("utf-8")},
    )
    request["text"] = "이 문장은 정식 연구 제목이 아니라 매우 긴 원 요청입니다. " * 20

    page, missing = build_report_html(request, bundle)

    assert missing == []
    assert f"<h1>{title}</h1>" in page
    assert title + "…" not in page
    assert 'class="graphical-abstract"' in page
    assert "figure { break-inside: avoid; break-after: avoid-page;" in page
    assert "figure + p { break-before: avoid-page;" in page
    assert ".graphical-abstract { width: 100%; max-width: none; max-height: none;" in page
    assert ".cover { min-height: 0;" in page


def test_html_marks_a_figure_it_cannot_embed_and_never_reads_outside_the_bundle(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    report = (f"# 결과\n\n![밖](../outside.png)\n\n![절대]({outside.as_posix()})\n\n![원격](https://example.com/a.png)\n\n"
              "![없음](steps/s1/outputs/gone.png)\n\n![ok](WD/outputs/fig1.png)\n")
    _settings, request, _tasks, bundle = build(tmp_path, report)
    page, missing = build_report_html(request, bundle)
    assert page.count("<img ") == 2  # the figure and the graphical abstract labhq draws when the report has none (#608)
    assert len(missing) == 4 and any("묶음 밖" in item for item in missing) and any("외부 주소" in item for item in missing)
    assert "그림 4개를 넣지 못했어요" in page and "figure-missing" in page
    assert "https://example.com/a.png" not in page


def test_html_resolves_workdir_figure_links_of_a_bundle_made_before_the_rewrite(tmp_path):
    _settings, request, _tasks, bundle = build(tmp_path, "# 결과\n\n![그림](WD/outputs/fig1.png)\n")
    (bundle / "report.md").write_text(request["report"], encoding="utf-8")  # the older bundle: link as the writer wrote it
    page, missing = build_report_html(request, bundle)
    assert missing == [] and base64.b64encode(PNG).decode() in page


def test_figure_embedder_limits_and_missing_bundle(tmp_path, monkeypatch):
    (tmp_path / "steps/s1/outputs").mkdir(parents=True)
    (tmp_path / "steps/s1/outputs/big.png").write_bytes(PNG * 10)
    (tmp_path / "steps/s1/outputs/a.png").write_bytes(PNG)
    embedder = FigureEmbedder(tmp_path)
    # #608: a figure has no size limit of its own (it was 8 MB); the PDF is shrunk to its cap instead
    assert "<img " in embedder.markup("big", "steps/s1/outputs/big.png", block=False)
    assert "<img " in embedder.markup("a", "steps/s1/outputs/a.png", block=False)
    assert "요청 묶음이 없어" in FigureEmbedder(None).markup("a", "steps/s1/outputs/a.png", block=False)
    assert "그림 형식이 아님" in embedder.markup("t", "steps/s1/outputs/t.tsv", block=False)


def test_markdown_subset_converter():
    figures = FigureEmbedder(None)
    out = markdown_to_html("## 둘째 수준\n### 셋째\n\n문단 하나\n이어지는 줄\n\n1. 가\n2. 나\n\n> 인용\n\n---\n"
                           "~~~\n<b>코드</b> | 파이프\n~~~\n\n"
                           "| a | `x\\|y` |\n|:--|--:|\n| 1 | 2 |\n| 3 |\n\n"
                           "[링크](https://example.com/a?b=1&c=2) [내부](steps/s1/outputs/t.tsv) **굵게** *기울임* ~~취소~~ snake_case_name 2*3*4",
                           figures)
    assert out.startswith("<h2>둘째 수준</h2>\n<h3>셋째</h3>")  # the shallowest heading becomes h2
    assert "<p>문단 하나 이어지는 줄</p>" in out
    assert "<ol><li>가</li><li>나</li></ol>" in out and "<blockquote><p>인용</p></blockquote>" in out and "<hr>" in out
    assert "<pre><code>&lt;b&gt;코드&lt;/b&gt; | 파이프</code></pre>" in out
    assert "<th>a</th><th><code>x|y</code></th>" in out and "<td>3</td><td></td>" in out
    assert '<a href="https://example.com/a?b=1&amp;c=2">링크</a>' in out
    assert '<span class="ref">내부</span>' in out and "<a href=\"steps" not in out
    assert "<strong>굵게</strong>" in out and "<em>기울임</em>" in out and "<del>취소</del>" in out
    assert "snake_case_name" in out and "2*3*4" in out
    assert "<script" not in markdown_to_html("<script>x</script> [a](javascript:alert(1))", figures)
    assert 'href="javascript' not in markdown_to_html("[a](javascript:alert(1))", figures)


# --- the PDF ---------------------------------------------------------------------------------------------------------

def test_pdf_is_printed_by_the_browser_with_its_own_profile_and_cached(tmp_path, monkeypatch):
    settings, request, tasks, bundle = build(tmp_path, REPORT)
    calls: list = []
    fake_browser(monkeypatch, calls=calls)
    built = build_report_file(request, settings, tasks)
    assert (built.kind, built.reason, built.media_type, built.filename) == ("pdf", "", "application/pdf",
                                                                              "req_pdf-report.pdf")
    assert built.data == FAKE_PDF and len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == "C:/fake/msedge.exe" and "--headless" in command
    assert any(arg.startswith("--user-data-dir=") for arg in command)  # never attaches to the PI's open browser
    assert command[-1].startswith("file:///") and command[-1].endswith("report.html")
    assert kwargs["timeout"] > 0 and kwargs["stdin"] == subprocess.DEVNULL
    cache = bundle.parent / "req_pdf-report"
    assert (cache / "report.pdf").read_bytes() == FAKE_PDF and "data:image/png" in (cache / "report.html").read_text("utf-8")
    assert bundle_figure_check(bundle)["problems"] == [] and verify_request(request, settings)["exit_code"] == 0
    again = build_report_file(request, settings, tasks)
    assert again.cached and again.data == FAKE_PDF and len(calls) == 1
    build_report_file({**request, "report": request["report"] + "\n\n추가 문장"}, settings, tasks)
    assert len(calls) == 2  # a changed report is printed again


def test_print_pdf_reports_timeouts_crashes_and_a_missing_file(tmp_path, monkeypatch):
    page = tmp_path / "r.html"
    page.write_text("<p>x</p>", encoding="utf-8")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("labhq.report_pdf.subprocess.run", timeout)
    assert "초 안에" in print_pdf("b", page, tmp_path / "o.pdf", timeout=5)
    monkeypatch.setattr("labhq.report_pdf.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    assert "실행하지 못해" in print_pdf("b", page, tmp_path / "o.pdf")
    fake_browser(monkeypatch, returncode=3, write=False)
    assert "종료 코드 3" in print_pdf("b", page, tmp_path / "o.pdf")
    (tmp_path / "o.pdf").write_bytes(b"not a pdf" * 400)
    assert print_pdf("b", page, tmp_path / "o.pdf") is not None


def test_without_a_browser_the_html_comes_with_a_plain_reason(tmp_path, monkeypatch):
    settings, request, tasks, bundle = build(tmp_path, REPORT)
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: None)
    ran = []
    monkeypatch.setattr("labhq.report_pdf.subprocess.run", lambda *a, **k: ran.append(a))
    built = build_report_file(request, settings, tasks)
    assert built.kind == "html" and built.reason == NO_BROWSER and not ran
    assert built.media_type.startswith("text/html") and built.filename == "req_pdf-report.html"
    assert b"data:image/png;base64," in built.data and "결론과 권고".encode() in built.data
    assert "Edge나 Chrome을 찾지 못해" in built.reason and "gateway.report_browser" in built.reason
    assert not (bundle.parent / "req_pdf-report" / "report.pdf").exists()


def test_a_browser_that_fails_falls_back_to_html_and_a_later_pdf_replaces_it(tmp_path, monkeypatch):
    settings, request, tasks, bundle = build(tmp_path, REPORT)
    fake_browser(monkeypatch, returncode=1, write=False)
    built = build_report_file(request, settings, tasks)
    assert built.kind == "html" and "PDF를 만들지 못해" in built.reason and "종료 코드 1" in built.reason
    fake_browser(monkeypatch)
    assert build_report_file(request, settings, tasks).kind == "pdf"  # a failure is never cached as the answer


def test_find_browser_uses_the_setting_then_path_then_standard_folders(tmp_path, monkeypatch):
    configured_exe = tmp_path / "mine" / "browser.exe"
    configured_exe.parent.mkdir()
    configured_exe.write_bytes(b"x")
    monkeypatch.setattr("labhq.report_pdf.shutil.which", lambda name: None)
    for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / "nowhere"))
    assert find_browser(str(configured_exe)) == str(configured_exe)
    assert find_browser(str(tmp_path / "missing.exe")) is None
    monkeypatch.setattr("labhq.report_pdf.shutil.which", lambda name: "/usr/bin/chromium" if name == "chromium" else None)
    assert find_browser() == "/usr/bin/chromium"
    monkeypatch.setattr("labhq.report_pdf.shutil.which", lambda name: None)
    edge = tmp_path / "pf" / "Microsoft/Edge/Application/msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"x")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf"))
    assert Path(find_browser()) == edge


# --- who may have one ------------------------------------------------------------------------------------------------

def test_controlled_unfinished_and_empty_requests_get_no_report(tmp_path, monkeypatch):
    settings, request, tasks, _bundle = build(tmp_path, REPORT)
    ran = []
    monkeypatch.setattr("labhq.report_pdf.subprocess.run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: "C:/fake/msedge.exe")
    zone = tmp_path / "controlled"
    settings.policy.data_zones = [DataZone(path=str(zone), level="restricted")]
    for controlled in ({"text": f"{zone / 'cohort.tsv'}를 분석"}, {"project_dirs": [str(zone / "sub")]},
                       {"references": [{"kind": "path", "value": str(zone / "ref")}]}):
        with pytest.raises(ReportRefused, match="통제 데이터가 포함된 요청"):
            build_report_file({**request, **controlled}, settings, tasks)
    restricted_task = {"t1": {**tasks["t1"], "payload": {"meta": {"restricted_inputs": True}}}}
    with pytest.raises(ReportRefused, match="통제 데이터"):
        build_report_file(request, settings, restricted_task)
    with pytest.raises(ReportRefused, match="아직 끝나지"):
        build_report_file({**request, "status": "running"}, settings, tasks)
    with pytest.raises(ReportRefused, match="결과 보고서가 없어요"):
        build_report_file({**request, "report": " "}, settings, tasks)
    with pytest.raises(ReportRefused, match="묶음을 만드는 중"):
        build_report_file({**request, "bundle_pending": True}, settings, tasks)
    assert not ran and not (Path(settings.runner.workspace_root) / "requests" / "req_pdf-report").exists()


# --- the gateway and the CLI -----------------------------------------------------------------------------------------

def test_gateway_report_pdf_endpoint(tmp_path, monkeypatch):
    from urllib.parse import unquote

    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    settings, request, _tasks, _bundle = build(tmp_path, REPORT)
    app = create_app(settings)
    app.state.hub.requests["req_pdf"] = request
    client = TestClient(app)
    path = "/api/requests/req_pdf/report-pdf"
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    assert client.get(path).status_code == 401
    assert client.get("/api/requests/nope/report-pdf", headers=headers).status_code == 404

    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: None)
    html = client.get(path, headers=headers)
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    assert html.headers["X-Labhq-Report-Kind"] == "html" and unquote(html.headers["X-Labhq-Reason"]) == NO_BROWSER
    assert "req_pdf-report.html" in html.headers["content-disposition"] and "data:image/png" in html.text

    fake_browser(monkeypatch)
    pdf = client.get(path, headers=headers)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == FAKE_PDF and pdf.headers["X-Labhq-Report-Kind"] == "pdf" and "X-Labhq-Reason" not in pdf.headers

    settings.policy.data_zones = [DataZone(path=str(tmp_path / "controlled"), level="restricted")]
    app.state.hub.requests["req_pdf"] = {**request, "text": f"{tmp_path / 'controlled' / 'x.tsv'} 분석"}
    refused = client.get(path, headers=headers)
    assert refused.status_code == 409 and "통제 데이터" in refused.json()["detail"]


def _response(content: bytes, kind: str, reason: str = "") -> httpx.Response:
    from urllib.parse import quote

    headers = {"X-Labhq-Report-Kind": kind, **({"X-Labhq-Reason": quote(reason, safe="")} if reason else {})}
    return httpx.Response(200, content=content, headers=headers)


def test_cli_report_pdf_saves_the_file(tmp_path, monkeypatch, capsys):
    seen = {}

    def get(url, **kwargs):
        seen.update(url=url, headers=kwargs["headers"])
        return _response(FAKE_PDF, "pdf")

    monkeypatch.setattr(httpx, "get", get)
    target = tmp_path / "out" / "mine.pdf"
    with pytest.raises(SystemExit) as done:
        main(["report", "req_pdf", "--pdf", "--out", str(target)])
    assert done.value.code == 0 and target.read_bytes() == FAKE_PDF
    assert seen["url"].endswith("/api/requests/req_pdf/report-pdf") and seen["headers"]["Authorization"].startswith("Bearer ")
    assert f"보고서 PDF: {target}" in capsys.readouterr().out


def test_cli_report_html_fallback_names_the_reason_and_keeps_the_html_suffix(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _response(b"<html>x</html>", "html", NO_BROWSER))
    with pytest.raises(SystemExit) as done:
        main(["report", "req_pdf", "--pdf", "--out", str(tmp_path / "r.pdf")])
    captured = capsys.readouterr()
    assert done.value.code == 0 and (tmp_path / "r.html").read_bytes() == b"<html>x</html>"
    assert not (tmp_path / "r.pdf").exists()
    assert f"보고서 HTML: {tmp_path / 'r.html'}" in captured.out and NO_BROWSER in captured.err


def test_cli_report_refusal_and_plain_report(tmp_path, monkeypatch, capsys):
    refusal = httpx.Response(409, json={"detail": "통제 데이터가 포함된 요청은 PDF 보고서를 만들지 않아요."})
    monkeypatch.setattr(httpx, "get", lambda url, **kw: refusal)
    with pytest.raises(SystemExit) as done:
        main(["report", "req_pdf", "--pdf", "--out", str(tmp_path / "r.pdf")])
    assert done.value.code == 2 and "통제 데이터" in capsys.readouterr().err and not (tmp_path / "r.pdf").exists()
    monkeypatch.setattr("labhq.cli._api", lambda *a, **k: {"report": "본문", "report_appendix": "기록"})
    with pytest.raises(SystemExit) as done:
        main(["report", "req_pdf"])
    assert done.value.code == 0 and "본문" in capsys.readouterr().out


def test_cli_verify_prints_the_figure_problem(tmp_path, monkeypatch, capsys):
    settings, request, _tasks, _bundle = build(tmp_path, "# 결과\n\n![없음](WD/outputs/nope.png)\n")
    monkeypatch.setattr("labhq.cli._api", lambda *_a, **_k: request)
    code = _verify(settings, request["id"], as_json=False, bundle=None)
    out = capsys.readouterr().out
    assert code == 1 and "보고서 그림: 링크 1개, 문제 1건" in out


# --- review of PR #596 -------------------------------------------------------------------------------------------------

def test_list_items_keep_the_fences_tables_and_paragraphs_nested_under_them():
    out = markdown_to_html("1. 실행\n\n   ```bash\n   # 주석\n   python run.py\n\n   echo done\n   ```\n\n"
                           "2. 결과\n   | a | b |\n   |---|---|\n   | 1 | 2 |\n\n   이어지는 문단\n3. 끝\n",
                           FigureEmbedder(None))
    assert out.count("<ol") == 1 and out.count("<li>") == 3
    assert "<pre><code># 주석\npython run.py\n\necho done</code></pre>" in out and "```" not in out
    assert ("<table><thead><tr><th>a</th><th>b</th></tr></thead>"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>") in out
    assert "<p>이어지는 문단</p>" in out and "<h" not in out  # a '# 주석' line in the fence is not a heading
    assert out.index("<pre>") < out.index("<table>") < out.index("이어지는 문단") < out.index("끝")
    nested = markdown_to_html("- 가\n  1. 나\n\n     ```\n     <b>x</b>\n     ```\n- 다\n", FigureEmbedder(None))
    assert nested == "<ul><li>가<ol><li>나<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre></li></ol></li><li>다</li></ul>"
    # text at the margin after a closed fence ends the list instead of joining the item
    after = markdown_to_html("- 가\n  ```\n  x\n  ```\n문단\n", FigureEmbedder(None))
    assert after.endswith("<p>문단</p>") and after.count("<li>") == 1


def test_an_ordered_list_split_by_a_figure_continues_its_numbers():
    figures = FigureEmbedder(None)
    out = markdown_to_html("1. 가\n2. 나\n\n![그림](steps/s1/outputs/a.png)\n\n3. 다\n4. 라\n", figures)
    assert out.count("<ol>") == 1 and '<ol start="3"><li>다</li><li>라</li></ol>' in out
    # a number other than 1 does not interrupt a paragraph or a list item's text (CommonMark)
    dated = markdown_to_html("문단\n2026. 10. 14. 날짜\n\n- 항목\n2026. 10. 14. 날짜\n\n문단\n1. 목록\n", figures)
    assert "<p>문단 2026. 10. 14. 날짜</p>" in dated and "<li>항목 2026. 10. 14. 날짜</li>" in dated
    assert dated.count("<ol") == 1 and "<p>문단</p>\n<ol><li>목록</li></ol>" in dated


def test_italic_and_bold_work_next_to_korean_particles():
    out = markdown_to_html("*TP53*의 **BRCA1**은 *변이*가 있고 2*3*4와 a*b*c는 그대로", FigureEmbedder(None))
    assert "<em>TP53</em>의" in out and "<strong>BRCA1</strong>은" in out and "<em>변이</em>가" in out
    assert "2*3*4와" in out and "a*b*c는" in out


def test_cached_pdf_is_not_reused_after_the_cover_or_the_bundle_documents_change(tmp_path, monkeypatch):
    settings, request, tasks, bundle = build(tmp_path, REPORT)
    calls: list = []
    fake_browser(monkeypatch, calls=calls)
    build_report_file(request, settings, tasks)
    assert len(calls) == 1 and build_report_file(request, settings, tasks).cached and len(calls) == 1
    reviewing = {**request, "pi_review_pending": True}  # the cover's status line changes
    assert not build_report_file(reviewing, settings, tasks).cached and len(calls) == 2
    assert build_report_file(reviewing, settings, tasks).cached
    assert not build_report_file({**reviewing, "pi_reviewed_at": 1760500000.0}, settings, tasks).cached
    assert not build_report_file({**reviewing, "created_at": 1760500000.0}, settings, tasks).cached
    count = len(calls)
    (bundle / "report.md").write_text((bundle / "report.md").read_text("utf-8") + "\n\n고친 문장\n", encoding="utf-8")
    assert not build_report_file(reviewing, settings, tasks).cached and len(calls) == count + 1
    with (bundle / "report_appendix.md").open("a", encoding="utf-8") as handle:
        handle.write("\n- 고친 기록\n")
    assert not build_report_file(reviewing, settings, tasks).cached and len(calls) == count + 2
    assert build_report_file(reviewing, settings, tasks).cached and len(calls) == count + 2


def test_a_figure_name_with_hash_or_question_mark_is_kept_when_the_file_exists(tmp_path):
    files = {"outputs/a#1.png": PNG, "outputs/fig1.png": PNG}
    report = "# 결과\n\n![해시](WD/outputs/a#1.png)\n\n![조각](WD/outputs/fig1.png#frag)\n"
    settings, request, _tasks, bundle = build(tmp_path, report, files=files)
    text = (bundle / "report.md").read_text(encoding="utf-8")
    assert "![해시](steps/s1/outputs/a%231.png)" in text and "![조각](steps/s1/outputs/fig1.png)" in text
    checked = bundle_figure_check(bundle)
    assert checked["linked"] == 2 and checked["problems"] == [] and len(checked["warnings"]) == 2
    assert linked_figure_paths(text) == ["steps/s1/outputs/a#1.png", "steps/s1/outputs/fig1.png"]
    assert verify_request(request, settings)["exit_code"] == 0
    page, missing = build_report_html(request, bundle)
    assert missing == [] and page.count("<img ") == 3  # two figures and the drawn graphical abstract (#608)
    (bundle / "report.md").write_text(request["report"], encoding="utf-8")  # an older bundle: the link as written
    page, missing = build_report_html(request, bundle)
    assert missing == [] and page.count("<img ") == 3
    # no file to look at (a name NTFS does not allow): the step's listed outputs decide; otherwise drop the query
    owners, listed = {"task_a": "s1"}, {"s1": {"outputs": ["outputs/what?.png"]}}
    out = link_workdir_figures("![x](task_a/outputs/what?.png) ![y](task_a/outputs/b.png?raw=1)", owners,
                               output_exists(listed))
    assert "![x](steps/s1/outputs/what%3F.png)" in out and "![y](steps/s1/outputs/b.png)" in out
    assert classify("https://example.com/a.png?x=1#y") == ("remote", "https://example.com/a.png?x=1#y")
    assert classify("steps/s1/outputs/a%231.png") == ("local", "steps/s1/outputs/a#1.png")


def test_figure_targets_with_parentheses_and_angle_brackets_resolve_to_the_copied_file(tmp_path):
    found = image_links('![a](x/plot(1).png) ![b](<x/a (2).png>) ![c](x(1(2)).png "t")')
    assert [link.target for link in found] == ["x/plot(1).png", "x/a (2).png", "x(1(2)).png"]
    files = {"outputs/plot(1).png": PNG, "outputs/my plot.png": PNG, "outputs/a (2).png": PNG}
    report = ("# 결과\n\n![괄호](WD/outputs/plot(1).png)\n\n![꺾쇠](<WD/outputs/my plot.png>)\n\n"
              '![둘 다](<WD/outputs/a (2).png> "제목")\n\n그림은 ![인라인](WD/outputs/plot(1).png) 처럼도 넣는다.\n')
    settings, request, _tasks, bundle = build(tmp_path, report, files=files)
    text = (bundle / "report.md").read_text(encoding="utf-8")
    assert "![괄호](steps/s1/outputs/plot%281%29.png)" in text and "![꺾쇠](steps/s1/outputs/my%20plot.png)" in text
    assert '![둘 다](steps/s1/outputs/a%20%282%29.png "제목")' in text
    checked = bundle_figure_check(bundle)
    assert checked["linked"] == 4 and checked["problems"] == [] and len(checked["warnings"]) == 3
    assert set(linked_figure_paths(text)) == {f"steps/s1/{name}" for name in files}
    assert verify_request(request, settings)["exit_code"] == 0
    page, missing = build_report_html(request, bundle)
    assert missing == [] and page.count("<img ") == 5  # four figures and the drawn graphical abstract (#608)
    raw = "![그림](steps/s1/outputs/plot(1).png)\n\n![x](<steps/s1/outputs/a (2).png>)"  # unrewritten, as a PI may write
    embedder = FigureEmbedder(bundle)
    out = markdown_to_html(raw, embedder)
    assert out.count("<figure>") == 2 and not embedder.missing and ".png)" not in out


def test_the_bundle_figure_check_reads_the_report_after_the_absolute_path_rewrite(tmp_path):
    workdir = (tmp_path / "runs" / "2026-10-14" / "task_s1_analyst").as_posix()
    report = f"# 결과\n\n![절대]({workdir}/outputs/fig1.png)\n"
    settings, request, _tasks, bundle = build(tmp_path, report)
    assert "![절대](steps/s1/outputs/fig1.png)" in (bundle / "report.md").read_text(encoding="utf-8")
    appendix = (bundle / "execution_record.md").read_text(encoding="utf-8")
    assert "WARN:" in appendix and "묶음 밖" not in appendix
    checked = bundle_figure_check(bundle)
    assert checked["linked"] == 1 and checked["problems"] == [] and len(checked["warnings"]) == 1
    assert verify_request(request, settings)["exit_code"] == 0
    # a rewritten link to a file the bundle does not hold is still reported in the appendix
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_workdir = (other_dir / "runs" / "2026-10-14" / "task_s1_analyst").as_posix()
    missing_report = f"# 결과\n\n![없음]({other_workdir}/outputs/nope.png)\n"
    _settings, _request, _tasks, other = build(other_dir, missing_report)
    appendix = (other / "execution_record.md").read_text(encoding="utf-8")
    assert "## 보고서 그림 링크" in appendix and "묶음에 없습니다: steps/s1/outputs/nope.png" in appendix
    assert appendix.count("묶음에 없습니다") == 1
