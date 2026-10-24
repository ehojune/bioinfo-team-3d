"""Real process death, restart and replay; all staff work remains deterministic fake."""
from pathlib import Path

import pytest

from scripts.recovery_fake_check import main, run_case, validate


async def test_foreign_source_is_refused_before_any_output_or_process(tmp_path, monkeypatch):
    import sys
    source, output = tmp_path / "other-checkout", tmp_path / "receipt"
    source.mkdir()
    with pytest.raises(ValueError, match="checkout containing this script"):
        await run_case(source, output, "gateway")
    monkeypatch.setattr(sys, "argv", ["recovery_fake_check.py", "--source", str(source), "--output", str(output)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not output.exists()


async def test_foreign_import_is_refused_before_any_output_or_process(tmp_path, monkeypatch):
    import labhq
    monkeypatch.setattr(labhq, "__file__", str(tmp_path / "other-checkout/labhq/__init__.py"))
    output = tmp_path / "receipt"
    with pytest.raises(ValueError, match="imported labhq"):
        await run_case(Path(__file__).resolve().parents[1], output, "runner")
    assert not output.exists()


@pytest.mark.parametrize("target", ["gateway", "runner"])
async def test_owned_process_death_recovers_once(tmp_path, target):
    receipt = await run_case(Path(__file__).resolve().parents[1], tmp_path / target, target)
    assert receipt["passed"], {key: receipt.get(key) for key in ("failure", "errors", "status")}
    assert not receipt["remaining_processes"]
    assert receipt["cost_usd"] > 0
    assert all(receipt["after"]["results"]["s1"].get(key) == value
               for key, value in receipt["before"]["results"]["s1"].items())
    assert all(row["http_status"] == 200 for row in receipt["lookups"])
    late = next(task_id for task_id, cost in receipt["after"]["cost_by_task"].items()
                if task_id not in receipt["before"].get("cost_by_task", {}) and cost > 0)
    late_cost = receipt["after"]["cost_by_task"][late]
    incomplete = {task_id: cost for task_id, cost in receipt["after"]["cost_by_task"].items() if task_id != late}
    plan = next(row for row in receipt["invocations"] if row["kind"] == "plan")
    # Keep acceptance, execution and accounting mutually consistent: only phase identity detects this regression.
    repeated_plan = {"invocations": receipt["invocations"] + [{**plan, "task_id": "repeated-plan"}],
                     "events": receipt["events"] + [{"type": "task.accepted", "task_id": "repeated-plan"}],
                     "accepted_tasks": {**receipt["accepted_tasks"], "repeated-plan": {"state": "finished"}},
                     "after": {**receipt["after"], "cost_by_task": {**receipt["after"]["cost_by_task"], "repeated-plan": 0.01},
                               "cost_usd": receipt["cost_usd"] + 0.01},
                     "cost_usd": receipt["cost_usd"] + 0.01, "ledger_cost_usd": receipt["ledger_cost_usd"] + 0.01}
    assert validate({**receipt, **repeated_plan}) == ["completed phase repeated or replaced"]
    # A truthful receipt must reject the same completed step/task, a renewed cap, or missing accounting.
    for changed, expected in (
        ({"invocations": receipt["invocations"] + [receipt["invocations"][0]]}, "same task executed twice"),
        ({"after_contract": {**receipt["after_contract"], "budget_usd": 2}}, "contract changed"),
        ({"cost_usd": receipt["cost_usd"] * 2}, "cost counted twice or missing"),
        ({"cost_usd": receipt["cost_usd"] / 2, "ledger_cost_usd": receipt["cost_usd"] / 2,
          "after": {**receipt["after"], "cost_usd": receipt["cost_usd"] / 2}}, "cost counted twice or missing"),
        ({"lookup_count": receipt["lookup_count"] + 1}, "lookup counted twice or missing"),
        ({"resume_approvals": 2}, "resume card count"),
        ({"remaining_processes": [{"pid": 123}]}, "artifact/cleanup missing"),
        ({"events": receipt["events"] + [next(e for e in receipt["events"] if e["type"] == "task.accepted")]},
         "task acceptance missing or duplicated"),
        ({"accepted_tasks": {}}, "task acceptance missing or duplicated"),
        ({"after": {**receipt["after"], "cost_by_task": {}}}, "prior cost history changed"),
        ({"cost_usd": 2}, "cost cap exceeded"),
        ({"after": {**receipt["after"], "cost_by_task": incomplete,
                    "cost_usd": receipt["cost_usd"] - late_cost},
          "cost_usd": receipt["cost_usd"] - late_cost, "ledger_cost_usd": receipt["ledger_cost_usd"] - late_cost},
         "fixture cost ledger missing or unknown"),
        ({"cost_known": False}, "fixture cost ledger missing or unknown"),
        ({"after": {**receipt["after"], "cost_by_task": {**receipt["after"]["cost_by_task"], late: None}}},
         "fixture cost ledger missing or unknown"),
        ({"before_completed_tasks": {}}, "completed phase baseline missing"),
    ):
        assert expected in validate({**receipt, **changed})
