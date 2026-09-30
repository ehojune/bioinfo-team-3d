import asyncio
import json

from labhq import bench


def _saved_run():
    return {
        "engine": "sol-ultra",
        "mode": "real",
        "status": "done",
        "model": "fixture",
        "effort": "ultra",
        "usage": {},
        "cost_usd": None,
        "cost_known": False,
        "duration_s": 0.1,
    }


def _block(values):
    return ("<!-- LABHQ_BENCH_RESULT -->\n```json\n" +
            json.dumps(values, ensure_ascii=False) +
            "\n```\n<!-- /LABHQ_BENCH_RESULT -->")


def _score(case, directory, text):
    (directory / "answer.md").write_text(text, encoding="utf-8")
    return asyncio.run(bench._score(case, directory, _saved_run()))


def test_structured_result_is_primary_and_wording_independent(tmp_path):
    case = bench.load_case("public-protein-qc")
    values = {
        "row_count": 4,
        "unique_accession_count": 3,
        "duplicate_accessions": ["P01116"],
        "maximum_length_aa": 1863,
        "all_lengths_positive": True,
    }
    first = _score(case, tmp_path, "짧은 한국어 설명이다.\n" + _block(values))
    second = _score(case, tmp_path, "A differently worded report.\n" + _block(values))
    assert first["format_passed"] and first["content_passed"] and first["checks_passed"]
    assert second["format_passed"] and second["content_passed"] and second["checks_passed"]


def test_structured_result_separates_format_failure_from_wrong_value(tmp_path):
    case = bench.load_case("public-protein-qc")
    correct = {
        "row_count": 4,
        "unique_accession_count": 3,
        "duplicate_accessions": ["P01116"],
        "maximum_length_aa": 1863,
        "all_lengths_positive": True,
    }
    malformed = _score(case, tmp_path, "<!-- LABHQ_BENCH_RESULT -->\n{bad json}\n"
                       "<!-- /LABHQ_BENCH_RESULT -->")
    wrong = dict(correct, row_count=5)
    incorrect = _score(case, tmp_path, _block(wrong))
    assert malformed["format_passed"] is False
    assert malformed["content_passed"] is None
    assert incorrect["format_passed"] is True
    assert incorrect["content_passed"] is False
    assert not malformed["checks_passed"] and not incorrect["checks_passed"]


def test_prompt_gives_every_arm_the_same_case_key_contract(tmp_path):
    case = bench.load_case("public-protein-qc")
    prompt = bench._prompt(case)
    commands = bench._real_commands(case, tmp_path)
    assert "LABHQ_BENCH_RESULT" in prompt
    assert all(key in prompt for key in (
        "row_count", "unique_accession_count", "duplicate_accessions",
        "maximum_length_aa", "all_lengths_positive",
    ))
    assert commands["sonnet-max"][commands["sonnet-max"].index("-p") + 1] == prompt
    assert commands["sol-ultra"][-1] == prompt
    assert commands["astra-ultra"][-1] == prompt


def test_baseline_commands_include_engine_extra_args_at_adapter_position(tmp_path):
    from labhq.settings import Settings

    settings = Settings()
    settings.engines.claude_code.extra_args = ["--proxy", "claude-proxy"]
    settings.engines.codex.extra_args = ["-c", 'profile="bench"']
    commands = bench._real_commands(bench.load_case("public-protein-qc"), tmp_path,
                                    settings=settings)

    claude = commands["sonnet-max"]
    assert claude[claude.index("--proxy"):claude.index("--proxy") + 2] == [
        "--proxy", "claude-proxy"]
    assert claude.index("--proxy") < claude.index("--allowedTools")
    for arm in ("sol-ultra", "astra-ultra"):
        codex = commands[arm]
        assert codex[-3:-1] == ["-c", 'profile="bench"']


def test_test_agent_counts_format_and_content_failures_separately(tmp_path, monkeypatch):
    async def fake(case_id, *_args, **_kwargs):
        row = {"engine": "labhq", "status": "done", "artifact_exists": True,
               "checks_passed": True, "format_passed": True, "content_passed": True,
               "within_budget": True, "unscripted_approvals": 0}
        if case_id == "inco-kras-g12c":
            row.update(checks_passed=False, format_passed=False, content_passed=None)
        elif case_id == "plastome-structure":
            row.update(checks_passed=False, content_passed=False)
        return {"case_id": case_id, "run_id": "fixture", "rows": [row]}

    monkeypatch.setattr(bench, "run_case", fake)
    summary = asyncio.run(bench.run_test_agent(tmp_path, engines="mock"))
    assert summary["format_failed"] == 1
    assert summary["content_failed"] == 1
    assert summary["passed"] == 3 and summary["failed"] == 2
    report = (tmp_path / "test-agent-summary.md").read_text(encoding="utf-8")
    assert "| inco-kras-g12c | FAIL | 1 | 0 |" in report
    assert "| plastome-structure | FAIL | 0 | 1 |" in report


import pytest


@pytest.mark.parametrize("submitted,passed", [
    ([181, 230], True),
    ([200, 210], False),  # inside the observed range but not the observed range
    ([182, 230], False),
    ([181, 229], False),
])
def test_observed_range_requires_both_exact_endpoints(tmp_path, submitted, passed):
    case = bench.load_case("public-penguins-qc")
    values = {"row_count": 5, "species_count": 2, "body_mass_missing_count": 1,
              "flipper_length_mm": submitted}
    assert _score(case, tmp_path, _block(values))["content_passed"] is passed


@pytest.mark.parametrize("submitted,passed", [([25500, 26500], True), ([24000, 26500], False)])
def test_design_tolerance_range_still_accepts_any_subrange(tmp_path, submitted, passed):
    case = bench.load_case("plastome-structure")
    values = {key: rule.get("equals", [rule.get("minimum"), rule.get("maximum")])
              for key, rule in case["result_fields"].items()}
    values["ir_single_copy_bp"] = submitted
    assert _score(case, tmp_path, _block(values))["content_passed"] is passed


def test_range_rule_must_pick_one_kind(tmp_path):
    text = (bench.CASES_ROOT / "public-penguins-qc.yaml").read_text(encoding="utf-8")
    both = "  flipper_length_mm: {type: integer_range, equals: [181, 230], minimum: 181, maximum: 230}"
    lines = [both if line.startswith("  flipper_length_mm:") else line for line in text.splitlines()]
    (tmp_path / "public-penguins-qc.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_case("public-penguins-qc", tmp_path)
