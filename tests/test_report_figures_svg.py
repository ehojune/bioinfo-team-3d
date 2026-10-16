"""SVG-first research figures and lossless report PDF policy (#683, #690)."""

from pathlib import Path

from labhq.cli import _verify
from labhq.evidence.audit import verify_request
from labhq.orchestrator import cso
from labhq.report_figures import bundle_figure_check
from labhq.report_pdf import CSS, RENDER_VERSION, build_report_file
from labhq.settings import GitHubSettings
from tests.test_report_pdf import PNG, build


HANGUL_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="12">핵심 결과</text></svg>'.encode()
PATH_ONLY_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h10v10z"/></svg>'''


def _bundle(tmp_path, report, files):
    for relative, payload in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (tmp_path / "report.md").write_text(report, encoding="utf-8")
    (tmp_path / "MANIFEST.tsv").write_text(
        "relative_path\tstatus\n" + "".join(f"{name}\tcopied\n" for name in files), encoding="utf-8")
    return bundle_figure_check(tmp_path)


def test_plan_and_worker_prompts_declare_svg_analysis_figures():
    for prompt in (cso.PLAN_PROMPT, cso.REPLAN_PROMPT, cso.RESEARCH_PLAN_PROMPT):
        assert "analysis step" in prompt and "1–2" in prompt and "outputs" in prompt
        for label in ("volcano", "PCA", "forest", "trend", "GSEA NES", "funnel"):
            assert label in prompt
    for prompt in (cso.SOLO_PROMPT, cso.RESEARCH_STEP_PROMPT, cso.STEP_PROMPT):
        assert 'svg.fonttype' in prompt and '"none"' in prompt
        assert "Malgun Gothic" in prompt and "Apple SD Gothic Neo" in prompt and "Noto Sans KR" in prompt
        assert "rasterized=True" in prompt and "PNG" in prompt and "screen" in prompt


def test_report_rule_requires_declared_figures_short_alt_and_svg_default():
    rule = cso.REPORT_FIGURE_RULE
    assert "must embed" in rule and "one-line reason" in rule
    assert "그림 N. 이름" in rule and "SVG" in rule
    assert "graphical_abstract.svg" in rule and "graphical_abstract.png" not in rule
    for prompt in (cso.REVIEW_PROMPT, cso.RESEARCH_REVIEW_PROMPT):
        assert "analysis steps declared key figures" in prompt
        assert "Korean <text>" in prompt and "no-capture" in prompt


def test_figure_qc_warns_when_outputs_exist_but_results_have_no_figure(tmp_path):
    checked = _bundle(tmp_path, "# 보고서\n\n## 결과\n\n글만 있습니다.\n",
                      {"steps/s1/outputs/volcano.svg": HANGUL_SVG})
    assert checked["warnings"] == [
        "steps/s1/outputs/volcano.svg: 보고서 결과 절에 그림 링크나 한 줄 생략 사유가 없습니다."]


def test_figure_qc_warns_for_each_declared_figure_missing_from_results(tmp_path):
    files = {"steps/s1/outputs/volcano.svg": HANGUL_SVG, "steps/s1/outputs/pca.svg": HANGUL_SVG}
    report = "# 보고서\n\n## 결과\n\n![그림 1. 화산도](steps/s1/outputs/volcano.svg)\n"
    checked = _bundle(tmp_path, report, files)
    assert checked["warnings"] == [
        "steps/s1/outputs/pca.svg: 보고서 결과 절에 그림 링크나 한 줄 생략 사유가 없습니다."]

    report += "\n- pca.svg는 표본 수가 부족해 생략했습니다.\n"
    assert _bundle(tmp_path, report, files)["warnings"] == []


def test_figure_qc_warns_for_png_without_same_name_svg(tmp_path):
    checked = _bundle(tmp_path, "# 보고서\n\n## 결과\n\n![그림 1. 화산도](steps/s1/outputs/volcano.png)\n",
                      {"steps/s1/outputs/volcano.png": PNG})
    assert any("같은 이름의 SVG가 없습니다" in warning for warning in checked["warnings"])


def test_figure_qc_warns_for_svg_without_hangul_text(tmp_path):
    checked = _bundle(tmp_path, "# 보고서\n\n## 결과\n\n![그림 1. 화산도](steps/s1/outputs/volcano.svg)\n",
                      {"steps/s1/outputs/volcano.svg": PATH_ONLY_SVG})
    assert any("한글이 <text>에 없습니다" in warning for warning in checked["warnings"])


def test_figure_qc_accepts_svg_with_hangul_text(tmp_path):
    checked = _bundle(tmp_path, "# 보고서\n\n## 결과\n\n![그림 1. 화산도](steps/s1/outputs/volcano.svg)\n",
                      {"steps/s1/outputs/volcano.svg": HANGUL_SVG})
    assert checked == {"linked": 1, "problems": [], "warnings": []}


def test_figure_warn_does_not_change_exit_or_skip_audit_bundle(tmp_path, monkeypatch):
    settings, request, _tasks, _bundle_path = build(
        tmp_path, "# 결과\n\n![그림 1. 결과](WD/outputs/fig1.png)\n", files={"outputs/fig1.png": PNG})
    checked = verify_request(request, settings)
    assert checked["exit_code"] == 0 and checked["reasons"] == []
    assert any("같은 이름의 SVG가 없습니다" in warning for warning in checked["warnings"])
    monkeypatch.setattr("labhq.cli._api", lambda *_args, **_kwargs: request)
    audit = tmp_path / "audit.zip"
    assert _verify(settings, request["id"], as_json=False, bundle=str(audit)) == 0
    assert audit.is_file()


def test_pdf_over_configured_50_mb_is_printed_once_without_shrinking(tmp_path, monkeypatch):
    settings, request, tasks, _bundle_path = build(
        tmp_path, "# 결과\n\n![그림 1. 결과](WD/outputs/fig1.png)\n")
    settings.github.edition_pdf_max_mb = 50
    seen = []

    def printer(_browser, html_path, pdf_path, *, timeout):
        seen.append(Path(html_path).read_text(encoding="utf-8"))
        with Path(pdf_path).open("wb") as handle:
            handle.write(b"%PDF-1.7\n")
            handle.seek(51 * 1024 * 1024)
            handle.write(b"x")
        return None

    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: "C:/fake/msedge.exe")
    monkeypatch.setattr("labhq.report_pdf.print_pdf", printer)
    built = build_report_file(request, settings, tasks)
    assert len(seen) == 1 and "data:image/png;base64" in seen[0]
    assert built.kind == "pdf" and built.shrunk == 0 and built.over_cap
    assert any("줄이지 않았습니다" in note for note in built.notes)


def test_pdf_fits_tall_figures_without_resampling_and_invalidates_old_cache():
    assert "max-height: 220mm" in CSS
    assert RENDER_VERSION == "3"
    assert GitHubSettings().edition_pdf_max_mb is None
