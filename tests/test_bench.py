import asyncio
import json
from pathlib import Path

from labhq import bench
from labhq.cli import main


def test_case_catalog_has_five_bounded_cases():
    cases = bench.load_cases()
    assert [case["id"] for case in cases] == [
        "inco-kras-g12c",
        "plastome-structure",
        "geo-gastric-summary",
        "public-protein-qc",
        "public-penguins-qc",
    ]
    for case in cases:
        assert case["request"].strip()
        assert case["references"]
        assert case["scripted_pi_answers"]
        assert case["check"]["script"].endswith(".py")
        assert 0 < case["budget_usd"] <= 10


def test_bench_list_and_dry_run_print_three_arms_without_running(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("labhq.cli.Settings.load", lambda *_: (_ for _ in ()).throw(AssertionError("no config")))
    main(["bench", "list"])
    listed = capsys.readouterr().out
    assert "inco-kras-g12c" in listed and "public-penguins-qc" in listed

    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(bench, "run_case", forbidden)
    main(["bench", "run", "inco-kras-g12c", "--dry-run", "--output", str(tmp_path)])
    output = capsys.readouterr().out
    assert "labhq" in output
    assert "claude -p" in output and "claude-opus-5-5" in output
    assert "codex exec" in output and "gpt-6-astra" in output
    assert "--ephemeral" in output and "--ignore-user-config" in output
    assert "--disallowedTools" in output and "SendMessage" in output
    assert not called and not any(tmp_path.iterdir())


def test_mock_case_runs_all_arms_scores_and_records_case_id(tmp_path):
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock"))
    assert result["case_id"] == "public-protein-qc"
    assert [row["engine"] for row in result["rows"]] == ["labhq", "opus-5.5", "gpt-6-astra"]
    assert all(row["artifact_exists"] and row["checks_passed"] for row in result["rows"])
    assert all(row["cost_usd"] == 0 and row["token_total"] == 0 for row in result["rows"])
    run_dir = tmp_path / "public-protein-qc" / result["run_id"]
    assert (run_dir / "comparison.md").is_file()
    saved = json.loads((run_dir / "comparison.json").read_text(encoding="utf-8"))
    assert saved == result

    labhq_run = json.loads((run_dir / "labhq" / "run.json").read_text(encoding="utf-8"))
    round_record = json.loads(Path(labhq_run["round_json"]).read_text(encoding="utf-8"))
    assert round_record["request"]["meta"]["case_id"] == "public-protein-qc"
    assert "PI가 질문을 받으면" not in round_record["request"]["text"]


def test_scripted_pi_selects_the_matching_answer():
    case = bench.load_case("public-protein-qc")
    assert bench._scripted_answer(case, "중복 accession을 제거할까요?") == (
        True, "중복은 제거하지 말고 원본 4행과 unique 3개를 함께 보고한다.")


def test_test_agent_runs_cases_in_order_and_writes_summary(tmp_path, monkeypatch):
    seen = []

    async def fake(case_id, output, engines="real", **_kwargs):
        seen.append(case_id)
        return {"case_id": case_id, "run_id": f"run-{len(seen)}", "rows": [{"engine": "labhq", "status": "done",
                                                 "checks_passed": True, "artifact_exists": True,
                                                 "within_budget": True}]}

    monkeypatch.setattr(bench, "run_case", fake)
    summary = asyncio.run(bench.run_test_agent(tmp_path, engines="mock"))
    assert seen == [case["id"] for case in bench.load_cases()]
    assert summary["passed"] == 5 and summary["failed"] == 0
    assert (tmp_path / "test-agent-summary.md").is_file()
    assert json.loads((tmp_path / "test-agent-summary.json").read_text(encoding="utf-8")) == summary


def test_one_failed_arm_is_scored_and_does_not_stop_the_next(tmp_path, monkeypatch):
    async def fake(case, arm, arm_dir, engines, command, settings):
        if arm == "opus-5.5":
            raise OSError("missing CLI")
        (arm_dir / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
        return {"engine": arm, "status": "done", "pi_interventions": 0, "cost_usd": None,
                "cost_known": False, "usage": {}, "duration_s": 0.01}

    monkeypatch.setattr(bench, "_run_baseline", fake)
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="real",
                                        arms=("opus-5.5", "gpt-6-astra")))
    assert [row["status"] for row in result["rows"]] == ["failed", "done"]
    assert result["rows"][0]["artifact_exists"] is False
    assert result["rows"][1]["checks_passed"] is True
