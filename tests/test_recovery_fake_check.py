"""Real process death, restart and replay; all staff work remains deterministic fake."""
from pathlib import Path

import pytest

from scripts.recovery_fake_check import run_case, validate


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
    # A truthful receipt must reject the same completed step/task, a renewed cap, or missing accounting.
    for changed, expected in (
        ({"invocations": receipt["invocations"] + [receipt["invocations"][0]]}, "same task executed twice"),
        ({"after_contract": {**receipt["after_contract"], "budget_usd": 2}}, "contract changed"),
        ({"cost_usd": receipt["cost_usd"] * 2}, "cost counted twice or missing"),
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
    ):
        assert expected in validate({**receipt, **changed})
