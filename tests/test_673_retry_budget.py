"""Regression for parallel JSON repair against a shared request budget."""
import asyncio

import pytest

from labhq.models import Task, TaskResult
from labhq.models import AgentSpec
from labhq.adapters.base import RunContext
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.structured_output import GATEWAY_RETRY, GATEWAY_RETRY_META, run_with_json_retries
from labhq.settings import Settings
from tests.test_cso import FakeHub
from tests.test_structured_output_retry import ERROR, make_runner


def _ctx(tmp_path, *, kind="step", budget=0.2):
    async def emit(*_args):
        pass

    return RunContext(task=Task(agent_id="worker", prompt="original", budget_usd=budget, meta={
                          "kind": kind, GATEWAY_RETRY_META: GATEWAY_RETRY}),
                      agent=AgentSpec(id="worker", name="worker", role="test"),
                      workdir=tmp_path, settings=Settings(), mcp_servers=[], env={}, emit=emit, prompt="original")


def _failing_adapter(calls, *, session="session", **error_data):
    class Adapter:
        async def run(self, ctx):
            calls.append(ctx.task.resume_session_id)
            await ctx.emit("agent.tool_error", {"text": ERROR, **error_data})
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, session_id=session, cost_usd=0.1)
    return Adapter()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,data,hint", [("step", {}, "plain"),
                                            ("plan", {"structured_output_closed": False}, "compact"),
                                            ("replan", {"structured_output_near_limit": True}, "compact"),
                                            ("step", {"structured_output_closed": False}, "plain")])
async def test_a_dispatch_marked_for_the_gateway_makes_one_attempt_and_offers_the_repair(tmp_path, kind, data, hint):
    calls = []
    result = await run_with_json_retries(_failing_adapter(calls, **data), _ctx(tmp_path, kind=kind))
    assert calls == [None]  # no second adapter call inside this turn
    assert not result.ok and result.error_kind == "structured_output_format" and result.structured is None
    assert result.format_retry == hint and result.session_id == "session"
    assert result.cost_usd == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_without_a_session_nothing_is_offered_and_an_unmarked_task_still_repairs_in_the_runner(tmp_path):
    calls = []
    result = await run_with_json_retries(_failing_adapter(calls, session=None), _ctx(tmp_path))
    assert calls == [None] and result.format_retry is None and result.error_kind == "structured_output_format"
    unmarked, calls = _ctx(tmp_path, budget=5.0), []
    unmarked.task = unmarked.task.model_copy(update={"meta": {"kind": "step"}})
    result = await run_with_json_retries(_failing_adapter(calls), unmarked)
    assert len(calls) == Settings().runner.structured_output_retries + 1 and result.format_retry is None


@pytest.mark.asyncio
async def test_parallel_format_retries_cannot_exceed_request_budget(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, monkeypatch, 0)
    first_calls = 0
    both_started = asyncio.Event()
    adapter_calls = []
    actual_spend = []

    class Adapter:
        async def run(self, ctx):
            nonlocal first_calls
            adapter_calls.append((ctx.task.id, ctx.task.resume_session_id))
            actual_spend.append(0.1)
            if not ctx.task.resume_session_id:
                first_calls += 1
                if first_calls == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=5)
                await ctx.emit("agent.tool_error", {"text": ERROR})
                return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True,
                                  session_id=ctx.task.id, cost_usd=0.1)
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True,
                              session_id=ctx.task.resume_session_id, cost_usd=0.1,
                              structured={"answer": "done"})

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_: Adapter())
    hub = FakeHub(runner.run_task)
    hub.requests["r"]["budget_usd"] = 0.2
    orch = Orchestrator(hub)
    tasks = [Task(agent_id="worker", request_id="r", prompt="original", budget_usd=0.2,
                  output_schema={"type": "object"}, meta={"kind": "step", "step_id": step})
             for step in ("A", "B")]

    results = await asyncio.gather(*(orch.run_step(task) for task in tasks), return_exceptions=True)

    # Inspect every paid adapter invocation, including those whose dispatch later raises.
    assert sum(actual_spend) <= 0.2
    assert len(adapter_calls) <= 2
    assert all(not isinstance(result, Exception) for result in results)


@pytest.mark.asyncio
async def test_a_repair_is_a_new_dispatch_of_the_same_session_with_the_rest_of_the_turn_budget(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 1)
    hub = FakeHub(runner.run_task)
    hub.requests["r"]["budget_usd"] = 5.0
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="original", budget_usd=0.5,
                                                  output_schema={"type": "object"}, meta={"kind": "step"}))
    first, repair = hub.calls
    assert len(calls) == 2 and first.id != repair.id and repair.resume_session_id == "same-session"
    assert repair.meta["parent_task"] == first.id and repair.meta[GATEWAY_RETRY_META] == GATEWAY_RETRY
    assert repair.budget_usd == pytest.approx(0.5 - 0.1)  # the turn's own cap minus what the first attempt spent
    assert result.ok and result.cost_usd == pytest.approx(0.2) and hub.approvals == []


@pytest.mark.asyncio
async def test_a_repair_never_asks_the_pi_for_more_budget(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 10)
    hub = FakeHub(runner.run_task)
    hub.requests["r"]["budget_usd"] = 0.1  # the first attempt spends all of it
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="original",
                                                  output_schema={"type": "object"}, meta={"kind": "step"}))
    assert len(calls) == 1 and len(hub.calls) == 1
    assert not result.ok and result.error_kind == "structured_output_format"
    assert [row["kind"] for row in hub.approvals] == ["clarify"]  # only the format-error card, no budget card


@pytest.mark.asyncio
async def test_the_request_cost_is_the_sum_of_each_dispatch_and_a_merged_repair_result_is_not_added_again(
        tmp_path, monkeypatch):
    """#673 review note: the step result of a repaired turn carries every attempt's cost (_sum_repair_turns), but the
    request total counts each dispatch once, by task id, so the merged result must never add that cost a second time."""
    runner, calls = make_runner(tmp_path, monkeypatch, 2)  # two format errors, then success: three dispatches
    hub = FakeHub(runner.run_task)
    hub.requests["r"]["budget_usd"] = 5.0
    orch = Orchestrator(hub)
    result = await orch.run_step(Task(agent_id="worker", request_id="r", prompt="original", budget_usd=1.0,
                                      output_schema={"type": "object"}, meta={"kind": "step"}))
    assert len(calls) == 3 and len(hub.calls) == 3 and result.ok
    per_dispatch = 0.1 * 3
    assert result.cost_usd == pytest.approx(per_dispatch)  # the step result reports every attempt
    assert orch.cost["r"] == pytest.approx(per_dispatch)  # the request total counts each dispatch once
    # Counting the merged result again (what a later caller adding step costs would do) changes nothing: its task id,
    # the last repair's, was already counted.
    orch._count_cost("r", result.task_id, result)
    orch._count_recorded_costs("r")
    assert orch.cost["r"] == pytest.approx(per_dispatch)
    assert {call.id for call in hub.calls} >= {result.task_id}
