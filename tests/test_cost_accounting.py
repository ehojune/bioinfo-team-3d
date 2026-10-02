from datetime import date

import pytest

from labhq.costs import (aggregate_costs, classify_cost, cost_text, format_cost, format_engines, format_warnings,
                         request_cost_summary, task_cost_item)
from labhq.models import TaskResult


def codex_usage(input_tokens, output_tokens, cached=0, written=0):
    return {"input_tokens": input_tokens, "cached_input_tokens": cached,
            "cache_write_input_tokens": written, "output_tokens": output_tokens}


def test_exact_model_usage_is_estimated_with_versioned_price_metadata():
    item = classify_cost(
        engine="codex", model="gpt-6.1-sol",
        usage={**codex_usage(1_000_000, 100_000, cached=500_000, written=250_000), "reasoning_output_tokens": 40_000},
        usage_known=True, cost_usd=None, cost_known=False,
        task_id="estimated", as_of=date(2026, 10, 2),
    )

    # Cached and written tokens are part of input_tokens, reasoning is part of output_tokens:
    # 250k x $2 + 500k x $0.10 + 250k x $2.50 + 100k x $10 per 1M.
    assert item["status"] == "estimated"
    assert item["usd"] == pytest.approx(2.175)
    assert item["price"]["model"] == "gpt-6.1-sol"
    assert item["price"]["catalog_version"] == "2026-10-02"
    assert item["price"]["applied_on"] == "2026-10-02"
    assert item["price"]["source"].startswith("https://developers.openai.com/")
    assert item["price"]["stale"] is False


def test_alias_without_exact_price_and_incomplete_usage_stay_unknown():
    alias = classify_cost(engine="claude_code", model="opus",
                          usage={"input_tokens": 100, "output_tokens": 20}, usage_known=True,
                          cost_usd=None, cost_known=False, task_id="alias")
    incomplete = classify_cost(engine="codex", model="gpt-6.1-sol",
                               usage=codex_usage(100, 10), usage_known=False,
                               cost_usd=None, cost_known=False, task_id="incomplete")
    no_cache_write = classify_cost(engine="codex", model="gpt-6.1-sol",
                                   usage={"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10},
                                   usage_known=True, cost_usd=None, cost_known=False, task_id="old-cli")
    inconsistent = classify_cost(engine="codex", model="gpt-6.1-sol", usage=codex_usage(10, 1, cached=20),
                                 usage_known=True, cost_usd=None, cost_known=False, task_id="bad")
    no_model = classify_cost(engine="codex", model=None, usage=codex_usage(100, 10), usage_known=True,
                             cost_usd=None, cost_known=False, task_id="default-model")

    assert alias["status"] == "unknown" and alias["reason"] == "price_missing"
    assert incomplete["status"] == "unknown" and incomplete["reason"] == "usage_incomplete"
    assert no_cache_write["status"] == "unknown" and no_cache_write["reason"] == "usage_incomplete"
    assert inconsistent["status"] == "unknown" and inconsistent["reason"] == "usage_incomplete"
    assert no_model["status"] == "unknown" and no_model["reason"] == "price_missing"
    assert all(item["usd"] is None for item in (alias, incomplete, no_cache_write, inconsistent, no_model))


def test_reported_cost_wins_and_mock_engine_is_a_real_zero():
    reported = classify_cost(engine="codex", model="gpt-6.1-sol", usage=codex_usage(1_000_000, 0),
                             usage_known=True, cost_usd=0.5, cost_known=True, task_id="reported")
    mock = classify_cost(engine="mock", model=None, usage={}, usage_known=True,
                         cost_usd=None, cost_known=False, task_id="mock")
    invalid = classify_cost(engine="claude_code", model="opus", usage={}, usage_known=True,
                            cost_usd=float("nan"), cost_known=True, task_id="nan")

    assert (reported["status"], reported["usd"]) == ("actual", 0.5)
    assert (mock["status"], mock["usd"]) == ("actual", 0.0)
    assert invalid["status"] == "unknown"


def test_task_item_prices_the_model_the_runner_recorded():
    result = TaskResult(task_id="t1", agent_id="engineer", ok=True, usage=codex_usage(1_000_000, 0),
                        provenance={"engine": "codex", "model": "gpt-6.1-sol",
                                    "runs": {"t1": {"model_id": "gpt-6-astra"}}})
    roster = {"engine": "codex", "model": "gpt-6-luna"}

    assert task_cost_item(result, roster)["usd"] == pytest.approx(10.0)
    assert task_cost_item(result.model_copy(update={"provenance": {}}), roster)["usd"] == pytest.approx(0.1)
    assert task_cost_item(result.model_copy(update={"provenance": {}}), None)["status"] == "unknown"


def test_task_item_uses_the_runs_own_engine_over_the_workdirs_first_one():
    # The workdir was made by a Claude task; this task ran Codex in it.
    result = TaskResult(task_id="t2", agent_id="engineer", ok=True, usage=codex_usage(1_000_000, 0),
                        provenance={"engine": "claude_code", "model": "opus",
                                    "runs": {"t2": {"engine": "codex", "model": "gpt-6.1-sol"}}})

    item = task_cost_item(result, None)
    assert (item["engine"], item["model"], item["status"]) == ("codex", "gpt-6.1-sol", "estimated")
    assert item["usd"] == pytest.approx(2.0)


def test_mixed_costs_keep_engine_subtotals_and_unknown_count_separate():
    items = {
        "actual": classify_cost(engine="claude_code", model="claude-opus-5-5", usage={},
                                usage_known=True, cost_usd=1.25, cost_known=True, task_id="actual"),
        "estimated": classify_cost(engine="codex", model="gpt-6.1-sol",
                                   usage=codex_usage(1_000_000, 100_000),
                                   usage_known=True, cost_usd=None, cost_known=False, task_id="estimated"),
        "unknown": classify_cost(engine="gemini", model="unpriced-model",
                                 usage={"input_tokens": 50, "output_tokens": 10}, usage_known=True,
                                 cost_usd=None, cost_known=False, task_id="unknown"),
    }

    summary = aggregate_costs(items)
    assert summary["actual_usd"] == 1.25
    assert summary["estimated_usd"] == pytest.approx(3.0)
    assert summary["subtotal_usd"] == pytest.approx(4.25)
    assert summary["unknown_count"] == 1 and summary["unknown_tasks"] == ["unknown"]
    assert summary["by_engine"]["claude_code"]["actual_usd"] == 1.25
    assert summary["by_engine"]["codex"]["estimated_usd"] == pytest.approx(3.0)
    assert summary["by_engine"]["gemini"]["unknown_count"] == 1
    assert summary["prices"][0]["model"] == "gpt-6.1-sol" and summary["prices"][0]["catalog_version"]
    assert format_cost(summary) == "확인 $1.25 + 추정 $3.00 + 미집계 1건"
    assert format_engines(summary) == "claude_code 확인 $1.25 · codex 추정 $3.00 · gemini 미집계 1건"


def test_unknown_only_is_never_shown_as_zero_dollars():
    unknown = classify_cost(engine="codex", model=None, usage={}, usage_known=False,
                            cost_usd=None, cost_known=False, task_id="u")
    summary = aggregate_costs({"u": unknown})

    assert format_cost(summary) == "미집계 1건"
    assert "$0" not in cost_text(0, False, summary)
    assert cost_text(0, False) == "비용 미집계" and cost_text(1.5, False) == "$1.50 + 비용 미집계"
    assert cost_text(0, True) == "$0.00"
    assert format_cost(aggregate_costs({})) == "확인 $0.00"


def test_old_price_catalog_is_flagged():
    item = classify_cost(engine="codex", model="gpt-6.1-sol",
                         usage=codex_usage(10, 10), usage_known=True,
                         cost_usd=None, cost_known=False, task_id="old", as_of=date(2027, 2, 1))
    summary = aggregate_costs({"old": item})
    assert item["price"]["stale"] is True
    assert summary["warnings"] == ["price_stale:codex:gpt-6.1-sol"]
    assert format_warnings(summary) == "가격표 오래됨: codex gpt-6.1-sol (2026-10-02 확인)"


def test_request_summary_waits_for_every_counted_task_to_be_classified():
    item = classify_cost(engine="claude_code", model="opus", usage={}, usage_known=True,
                         cost_usd=0.2, cost_known=True, task_id="t1")

    assert request_cost_summary({"cost_by_task": {"t0": 0.1, "t1": 0.2}, "cost_items": {"t1": item}}) is None
    assert request_cost_summary({"cost_by_task": {"t1": 0.2}, "cost_items": {"t1": item}})["actual_usd"] == 0.2
