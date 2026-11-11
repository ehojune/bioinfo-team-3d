import json

from labhq import bench


def _legacy_score(engine, passed):
    return {
        "engine": engine, "mode": "real", "status": "done", "model": engine,
        "effort": "ultra", "artifact_exists": True, "checks_passed": passed,
        "pi_interventions": 0, "pi_questions_observable": False,
        "unscripted_approvals": 0, "budget_approvals": 0,
        "cost_usd": 0.1, "cost_budget_ratio": 0.1, "within_budget": True,
        "token_total": 10, "duration_s": 1.0,
    }


def test_report_preserves_legacy_checks_passed_and_recommends_rescore(tmp_path):
    case_id = "public-protein-qc"
    for engine, passed in (("sol-ultra", True), ("astra-ultra", False)):
        directory = tmp_path / case_id / "legacy-run" / engine
        directory.mkdir(parents=True)
        (directory / "score.json").write_text(
            json.dumps(_legacy_score(engine, passed)), encoding="utf-8")

    result = bench.report_case(case_id, tmp_path, "real")
    report = (tmp_path / case_id / "comparison.md").read_text(encoding="utf-8")
    assert [row["checks_passed"] for row in result["rows"]] == [True, False]
    assert "legacy 결과" in report
    assert "| sol-ultra |" in report and "| PASS | N/A | N/A | N/A |" in report
    assert "| astra-ultra |" in report and "| FAIL | N/A | N/A | N/A |" in report
    assert "labhq bench rescore public-protein-qc --all" in report
def test_report_marker_and_appendix_composition_preserves_existing_behavior():
    from labhq.orchestrator.cso import (
        _append_report_metadata, _before_bench_block, _execution_warning_summary,
        _split_report_appendix,
    )

    marker = '<!-- LABHQ_BENCH_RESULT -->\n{"score": 1}\n<!-- /LABHQ_BENCH_RESULT -->'
    report = '본문\n\n' + marker
    combined = _append_report_metadata(report, ['기록'])
    assert combined == '본문\n\n## 부록: 실행 기록\n\n기록\n\n' + marker
    assert _split_report_appendix(combined) == (report, '## 부록: 실행 기록\n\n기록')
    assert _before_bench_block(report, '참고') == '본문\n\n참고\n\n' + marker
    assert _execution_warning_summary(report) == '본문\n\n실행 경고가 있습니다. 실행 기록 참고.\n\n' + marker
    assert _append_report_metadata('본문', ['기록']) == '본문\n\n## 부록: 실행 기록\n\n기록'
    # Multiple blocks and non-trailing text must not be silently moved.
    for original in ('본문\n\n' + marker + '\n\n후기', marker + '\n' + marker):
        assert _before_bench_block(original, '참고') == original + '\n\n참고'
