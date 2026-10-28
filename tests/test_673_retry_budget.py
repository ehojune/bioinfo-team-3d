"""Regression for parallel JSON repair against a shared request budget."""
import asyncio

import pytest

from labhq.models import Task, TaskResult
from labhq.models import AgentSpec
from labhq.adapters.base import RunContext
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.structured_output import run_with_json_retries
from labhq.settings import Settings
from tests.test_cso import FakeHub
from tests.test_structured_output_retry import ERROR, make_runner


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", [None, 0, float("nan"), "invalid", "raise"])
async def test_retry_admission_failure_keeps_failed_cost(tmp_path, grant):
    calls = []
    async def emit(*_args):
        pass

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx.task.resume_session_id)
            await ctx.emit("agent.tool_error", {"text": ERROR})
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True,
                              session_id="session", cost_usd=0.1)

    async def admit(result, remaining):
        assert result.cost_usd == 0.1
        assert remaining == pytest.approx(0.1)
        if grant == "raise":
            raise RuntimeError("admission unavailable")
        return grant

    ctx = RunContext(task=Task(agent_id="worker", prompt="original", budget_usd=0.2),
                     agent=AgentSpec(id="worker", name="worker", role="test"),
                     workdir=tmp_path, settings=Settings(), mcp_servers=[], env={}, emit=emit, prompt="original")
    result = await run_with_json_retries(Adapter(), ctx, admit_retry=admit)
    assert calls == [None]
    assert not result.ok and result.error_kind == "structured_output_format"
    assert result.cost_usd == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_retry_admission_caps_resumed_session(tmp_path):
    calls = []
    async def emit(*_args):
        pass

    class Adapter:
        async def run(self, ctx):
            calls.append((ctx.task.resume_session_id, ctx.task.budget_usd))
            if len(calls) == 1:
                await ctx.emit("agent.tool_error", {"text": ERROR})
                return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True,
                                  session_id="session", cost_usd=0.1)
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True,
                              session_id="session", cost_usd=0.04, structured={"answer": "done"})

    async def admit(_result, _remaining):
        return 0.05

    ctx = RunContext(task=Task(agent_id="worker", prompt="original", budget_usd=0.2),
                     agent=AgentSpec(id="worker", name="worker", role="test"),
                     workdir=tmp_path, settings=Settings(), mcp_servers=[], env={}, emit=emit, prompt="original")
    result = await run_with_json_retries(Adapter(), ctx, admit_retry=admit)
    assert calls == [(None, 0.2), ("session", 0.05)]
    assert result.ok and result.cost_usd == pytest.approx(0.14)


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
