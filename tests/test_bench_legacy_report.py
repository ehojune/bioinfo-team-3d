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
