"""Regression for parallel JSON repair against a shared request budget."""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from labhq.models import Task, TaskResult
from labhq.models import AgentSpec
from labhq.adapters.base import RunContext
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.structured_output import GATEWAY_RETRY, GATEWAY_RETRY_META, run_with_json_retries
from labhq.settings import Settings
from tests.test_cso import FakeHub
from tests.test_structured_output_retry import ERROR, make_runner


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected_calls", [("success", 2), ("unknown_cost", 1),
                                                  ("no_evidence", 1), ("second_failure", 2),
                                                  ("exhausted", 1), ("halted", 1), ("shutdown", 1),
                                                  ("already_claimed", 1), ("second_other_failure", 2)])
async def test_early_exit_retry_uses_gateway_budget_and_never_repeats(case, expected_calls):
    async def dispatch(task):
        if task.meta.get("early_exit_retry"):
            return TaskResult(task_id=task.id, agent_id="worker", ok=case == "success",
                              text="done" if case == "success" else "", cost_usd=0.1,
                              exit_code=0 if case == "success" else 1 if case == "second_other_failure" else 0xC000013A,
                              error=None if case == "success" else "No module named missing" if case == "second_other_failure" else "timeout after failure",
                              early_exit_retry={"deadline": time.monotonic() + 100, "clock_id": "runner"})
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, exit_code=0xC000013A,
                          error="empty CLI stream: timeout", cost_usd=None if case == "unknown_cost" else 0.1,
                          error_kind="halted" if case == "halted" else None,
                          shutdown_evidence={"stopping": False} if case == "shutdown" else {},
                          cost_known=case != "unknown_cost",
                          early_exit_retry=None if case == "no_evidence" else
                          {"deadline": time.monotonic() + 100, "clock_id": "runner"})

    hub = FakeHub(dispatch)
    if case == "second_other_failure":
        async def forbidden(*args, **kwargs):
            pytest.fail("a second failure must not launch facilities recovery")
        hub.request_facilities_diagnosis = forbidden
        hub.request_facilities_fix = forbidden
    hub.requests["r"]["budget_usd"] = 0.1 if case == "exhausted" else 5
    orch = Orchestrator(hub)
    task = Task(agent_id="worker", request_id="r", prompt="work", budget_usd=0.5,
                meta={"kind": "step", "step_id": "A"})
    if case == "already_claimed":
        hub.requests["r"]["early_exit_retries"] = {task.id: "earlier-task"}
    result = await orch.run_step(task)
    assert len(hub.calls) == expected_calls
    assert result.ok is (case == "success")
    if expected_calls == 2:
        assert hub.calls[1].budget_usd == pytest.approx(0.4)
        assert result.cost_usd == pytest.approx(0.2)
        assert orch.cost["r"] == pytest.approx(0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize("reserve", [False, True])
@pytest.mark.parametrize("task_cap,agent_cap,policy_cap,spent,left", [
    (30, 20, 5, 4, 1),
    (30, 5, 20, 4, 1),
    (5, 20, 30, 4, 1),
    (30, 0, 5, 4, 1),
    (30, 5, 0, 4, 1),
    (30, 20, 5, 5, 0),
    (30, 20, 5, 6, 0),
])
async def test_early_exit_retry_respects_smallest_turn_cap(reserve, task_cap, agent_cap, policy_cap, spent, left):
    async def dispatch(task):
        if task.meta.get("early_exit_retry"):
            return TaskResult(task_id=task.id, agent_id="worker", ok=True, cost_usd=left)
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, exit_code=0xC000013A,
                          cost_usd=spent, early_exit_retry={"deadline": time.monotonic() + 100,
                                                            "clock_id": "runner"})

    hub = FakeHub(dispatch)
    hub.requests["r"]["budget_usd"] = 100
    hub.agents["worker"]["max_budget_usd"] = agent_cap
    hub.s.policy.budget.per_task_usd = policy_cap
    hub.s.policy.budget.reserve_before_dispatch = reserve
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                                  budget_usd=task_cap, meta={"kind": "step", "step_id": "A"}))
    assert len(hub.calls) == (2 if left else 1)
    if left:
        assert hub.calls[1].budget_usd == pytest.approx(left)
        assert result.cost_usd == pytest.approx(spent + left)
    else:
        assert not result.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("halt_cost", [0.0, 0.1, 0.4, None])
async def test_halt_during_early_exit_retry_waits_for_explicit_resume(resume, halt_cost):
    calls = []
    waits = []
    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, exit_code=0xC000013A,
                              error="exit", cost_usd=0.1,
                              early_exit_retry={"deadline": time.monotonic() + 100, "clock_id": "runner"})
        if len(calls) == 2:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error_kind="halted",
                              error="halted", cost_usd=halt_cost, cost_known=halt_cost is not None)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done", cost_usd=0.1)

    hub = FakeHub(dispatch)
    async def wait_halted(*args):
        waits.append(args)
        return resume
    hub.wait_halted = wait_halted
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                                  budget_usd=0.5))
    dispatched_resume = resume and halt_cost is not None and halt_cost < 0.4
    assert len(waits) == 1 and len(calls) == (3 if dispatched_resume else 2)
    assert result.ok is dispatched_resume
    if dispatched_resume:
        assert "early_exit_retry" not in calls[2].meta
        assert calls[2].budget_usd == pytest.approx(0.4 - halt_cost)
        assert result.cost_usd == pytest.approx(0.2 + halt_cost)
    if halt_cost is None:
        assert result.cost_usd is None and result.cost_known is False


@pytest.mark.asyncio
@pytest.mark.parametrize("halt_count", [1, 2])
async def test_resumed_early_retry_does_not_repeat_transient_failure(halt_count):
    calls = []
    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, exit_code=0xC000013A,
                              error="exit", cost_usd=0.1,
                              early_exit_retry={"deadline": time.monotonic() + 100, "clock_id": "runner"})
        if len(calls) <= halt_count + 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error_kind="halted",
                              error="halted", cost_usd=0.0, cost_known=True)
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error="HTTP 503 overloaded", cost_usd=0.1)

    hub = FakeHub(dispatch)
    async def resume(*_args):
        return True
    hub.wait_halted = resume
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                                  budget_usd=0.5))
    assert len(calls) == halt_count + 2
    assert not result.ok and result.cost_usd == pytest.approx(0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize("early_retry", [False, True])
async def test_early_retry_halted_while_waiting_for_slot_has_zero_cost(tmp_path, monkeypatch, early_retry):
    runner, calls = make_runner(tmp_path, monkeypatch, 0)
    async def halted_slot(*_args):
        return False
    monkeypatch.setattr(runner, "_slot_or_halt", halted_slot)
    task = Task(agent_id="worker", request_id="r", prompt="work",
                meta={"early_exit_retry": {"deadline": time.monotonic() + 100, "clock_id": runner.process}}
                if early_retry else {})
    result = await runner.run_task(task)
    assert not calls and result.error_kind == "halted"
    assert (result.cost_known, result.cost_usd) == ((True, 0) if early_retry else (False, None))


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["wait_ready", "halt_flag"])
@pytest.mark.parametrize("early_retry", [False, True])
async def test_early_retry_halted_before_adapter_spawn_has_zero_cost(tmp_path, monkeypatch, where, early_retry):
    from labhq.adapters.claude_code import ClaudeCodeAdapter
    from labhq.models import Engine

    ctx = _ctx(tmp_path)
    ctx.agent.engine = Engine.claude_code
    if early_retry:
        ctx.task.meta["early_exit_retry"] = {"deadline": time.monotonic() + 100, "clock_id": "runner"}
    ctx.settings.engines.claude_code.bin = sys.executable
    adapter = ClaudeCodeAdapter(ctx.settings)
    monkeypatch.setattr(adapter, "prepare", lambda _ctx: None)
    monkeypatch.setattr(adapter, "build_command", lambda _ctx: [sys.executable])
    async def ready():
        return where != "wait_ready"
    async def write_meta(*_args):
        pass
    ctx.wait_ready = ready
    ctx.awrite_meta = write_meta
    ctx.halt = asyncio.Event()
    if where == "halt_flag":
        ctx.halt.set()
    result = await adapter.run(ctx)
    assert result.error_kind == "halted" and ctx.started_at is None
    assert (result.cost_known, result.cost_usd) == ((True, 0) if early_retry else (False, None))


@pytest.mark.skipif(os.name != "nt", reason="Windows Job object and CONTROL_C_EXIT")
@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [("success", 2), ("unknown_cost", 1), ("tools", 1),
                                           ("outputs", 1), ("scan", 1), ("second_failure", 2)])
async def test_fake_cli_early_exit_uses_original_runner_and_budget(tmp_path, monkeypatch, case, expected):
    from labhq.adapters.claude_code import ClaudeCodeAdapter
    from labhq.models import Engine

    runner, _ = make_runner(tmp_path, monkeypatch, 0)
    # The wrapper runs from the isolated workspace, not the test checkout's current directory.
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code)
    runner.s.runner.step_memory_limit_gb = 0.5
    script = tmp_path / "fake_staff.py"
    marker = tmp_path / "attempts.txt"
    script.write_text(
        "import json, os, pathlib, time\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        f"case = {case!r}\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "if n == 1 and case == 'tools':\n"
        " print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'x','name':'Read','input':{}}]}}), flush=True)\n"
        "if n == 1 and case == 'outputs':\n"
        " pathlib.Path('outputs/written.txt').write_text('changed')\n"
        "print(json.dumps({'type':'result','subtype':'success','result':'done',"
        "'session_id':'fake','total_cost_usd':None if case == 'unknown_cost' else 0.1}), flush=True)\n"
        "time.sleep(0.25)\n"
        "os._exit(-1073741510 if n == 1 or case == 'second_failure' else 0)\n", encoding="utf-8")
    runner.s.engines.claude_code.bin = sys.executable
    runner.s.engines.claude_code.prefix_args = [str(script)]
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_: ClaudeCodeAdapter(runner.s))
    if case == "scan":
        from labhq.runner.workspace import TaskWorkspace
        original_scan = TaskWorkspace.scan_output_records
        def incomplete_scan(self, *args, **kwargs):
            rows, _note = original_scan(self, *args, **kwargs)
            return rows, "fixture: incomplete scan"
        monkeypatch.setattr(TaskWorkspace, "scan_output_records", incomplete_scan)
    hub = FakeHub(runner.run_task)
    hub.agents["worker"]["engine"] = "claude_code"
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work", budget_usd=0.5,
                                                   meta={"kind": "step", "step_id": "A"}))
    assert marker.read_text() == str(expected)
    assert len(hub.calls) == expected and result.ok is (case == "success")
    if expected == 2:
        assert result.cost_usd == pytest.approx(0.2)
        assert hub.calls[1].meta["early_exit_retry"]["clock_id"] == runner.process


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
