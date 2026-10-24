"""An approved recovery starts a new turn after an acknowledged interrupted result."""
import time
from unittest.mock import AsyncMock

import pytest

from labhq.models import Task, TaskResult
from tests.test_shutdown_interrupted import make_hub


@pytest.mark.parametrize("kind,checkpoint_only", [("step", False), ("direct", False), ("direct", True)])
@pytest.mark.parametrize("interrupted", [True, False])
async def test_recovery_reuses_success_but_does_not_replay_a_shutdown(tmp_path, monkeypatch, kind, interrupted,
                                                                  checkpoint_only):
    hub = make_hub(tmp_path)
    hub.requests["r"].update(status="running", cost_usd=0.05, cost_known=True,
                             cost_by_task={"old": 0.05}, budget_usd=10)
    hub.save_request("r")
    hub.recovery_steps.add("r")
    hub.recovery_started["r"] = time.time()
    hub.agent_runner["worker"] = "local"
    hub.runners["local"] = AsyncMock()
    hub.runner_incarnations["local"] = "incarnation"
    old_result = TaskResult(task_id="old", agent_id="worker", ok=not interrupted, cost_usd=0.05,
                            text="previous turn", shutdown_evidence={"source": "runner-recovery", "stopping": True}
                            if interrupted else {})
    meta = {"kind": kind, **({"step_id": "s2"} if kind == "step" else {})}
    task = Task(id="new", request_id="r", agent_id="worker", prompt="remaining work", meta=meta)
    hub.store.put("task", "old", {"request_id": "r", "kind": kind, "step_id": meta.get("step_id"),
                                   "completed": True, "accepted": True, "dispatched_at": time.time() - 2,
                                   "result": old_result.model_dump(mode="json")})
    if kind == "direct":
        hub.store.put("step_checkpoint", "r:direct", {"task_id": "old", "result": old_result.model_dump(mode="json")})
    if checkpoint_only:
        hub.store.delete("task", "old")
    # The budget reservation and dispatch must agree about whether this is a new charged turn.
    assert hub.recovery_task_id(task) == (None if interrupted else "old")
    dispatched = []

    async def send(runner_id, message):
        if message["type"] != "task.dispatch":
            return
        dispatched.append(message["task"]["id"])
        result = TaskResult(task_id="new", agent_id="worker", ok=True, text="finished", cost_usd=0.02)
        await hub.on_runner_message(runner_id, {"type": "task.result", "task_id": "new", "request_id": "r",
                                               "data": result.model_dump(mode="json")})

    monkeypatch.setattr(hub, "send_runner", send)
    result = await hub.dispatch(task)
    assert result.ok
    assert result.task_id == ("new" if interrupted else "old")
    assert dispatched == (["new"] if interrupted else [])
    assert hub.requests["r"]["cost_by_task"]["old"] == 0.05
    assert hub.requests["r"]["cost_usd"] == pytest.approx(0.07 if interrupted else 0.05)


def test_recovery_keeps_live_tasks_and_unknown_cost_history(tmp_path):
    hub = make_hub(tmp_path)
    hub.recovery_steps.add("r")
    hub.recovery_started["r"] = time.time()
    hub.requests["r"].update(cost_known=False, cost_by_task={"old": None}, cost_usd=0)
    task = Task(id="new", request_id="r", agent_id="worker", prompt="remaining", meta={"kind": "step", "step_id": "s2"})
    entry = {"request_id": "r", "kind": "step", "step_id": "s2", "completed": False,
             "accepted": True, "dispatched_at": time.time() - 2,
             "result": {"ok": False, "shutdown_evidence": {"source": "runner-recovery"}}}
    hub.store.put("task", "old", entry)
    assert hub.recovery_task_id(task) == "old"
    assert hub._recovery_choice(task) == ("old", entry)
    hub.store.put("task", "old", {**entry, "completed": True})
    assert hub.recovery_task_id(task) is None
    assert hub.requests["r"]["cost_by_task"] == {"old": None}
    assert hub.requests["r"]["cost_known"] is False
