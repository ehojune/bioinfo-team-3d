"""#1026: a re-plan must never drop or rebind a stored runner environment binding (#674)."""
import copy

import pytest

from labhq.orchestrator.cso import (ENV_LOCK_OUTPUT, Orchestrator, split_runner_environments)
from tests.test_cso import FakeHub, result


def bound_plan():
    steps = [
        {"id": "env", "agent_id": "worker", "instruction": "Install approved packages",
         "outputs": [ENV_LOCK_OUTPUT], "depends_on": []},
        {"id": "run", "agent_id": "worker", "instruction": "analyze", "outputs": [], "depends_on": ["env"]},
    ]
    return split_runner_environments(steps, {"worker": "r1"}, {"r1": "linux"}, lambda _: True)


@pytest.mark.asyncio
async def test_failure_with_bound_environment_does_not_replan_or_touch_bindings():
    stored = bound_plan()
    before = copy.deepcopy(stored)
    assert any("environment_binding" in s for s in stored)
    assert any("environment_bindings" in s for s in stored)

    async def dispatch(task):
        kind = task.meta["kind"]
        assert kind != "replan", "a bound plan must not reach the CSO re-plan"
        if kind == "step":
            if task.meta["step_id"] == "run":
                return result(task, ok=False, error="analysis failed")
            return result(task, text="env ready", outputs=task.meta["outputs"])
        assert kind == "synthesis", kind
        return result(task, text="final")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.max_replans = 1
    hub.s.orchestrator.max_failure_replans = 1
    hub.s.orchestrator.reviewer_agent = None
    hub.requests["r"]["plan"] = {"steps": copy.deepcopy(stored)}
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert not [t for t in hub.calls if t.meta["kind"] == "replan"]
    assert "replan_history" not in req
    for kept, old in zip(req["plan"]["steps"], before):
        for key in ("id", "agent_id", "environment_binding", "environment_bindings"):
            assert kept.get(key) == old.get(key), key
    assert [s["id"] for s in req["plan"]["steps"]] == [s["id"] for s in before]
