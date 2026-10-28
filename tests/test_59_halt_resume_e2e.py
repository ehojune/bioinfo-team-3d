"""Isolated control flow across the gateway, runner hook and orchestrator."""
import asyncio

import pytest

from labhq.hooks import control as hook
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.gateway.server import Hub
from labhq.settings import Settings


@pytest.mark.asyncio
async def test_halt_resume_keeps_session_and_does_not_stop_another_request(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    settings.orchestrator.step_retry_backoff_s = 0
    hub, runner = Hub(settings), Runner(settings)
    hub.agents = {"worker": {"id": "worker", "engine": "claude_code"}}
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    for rid in ("halted-request", "other-request"):
        hub.requests[rid] = {"id": rid, "text": "test", "mode": "team", "budget_usd": 10, "status": "running"}
        hub.save_request(rid)

    started, proceed = asyncio.Event(), asyncio.Event()
    calls, tool_effects = [], []

    class FakeCli:
        async def run(self, ctx):
            if ctx.on_started:
                ctx.on_started()
            calls.append(ctx.task)
            path = ctx.env[hook.CONTROL_ENV]
            if ctx.task.request_id == "halted-request" and not ctx.task.resume_session_id:
                started.set()
                await proceed.wait()
                decision = hook.pre_tool_use(path)
                if decision is None:
                    tool_effects.append("halted tool ran")
                else:
                    assert decision == {"continue": False, "stopReason": hook.HALT_REASON}
                return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, session_id="session-59")
            assert hook.pre_tool_use(path) is None
            tool_effects.append(ctx.task.request_id)
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done",
                              session_id=ctx.task.resume_session_id or "other-session")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_: FakeCli())

    async def send_runner(_rid, message):
        await runner._on_message(message)

    async def dispatch(task):
        hub.store.put("task", task.id, {"request_id": task.request_id, "runner_id": "local",
                      "accepted": True, "step_id": task.meta["step_id"], "kind": "step",
                      "payload": {"agent_id": "worker"}})
        result = await runner.run_task(task)
        entry = hub.store.get("task", task.id)
        hub.store.put("task", task.id, {**entry, "completed": True, "result": result.model_dump(mode="json")})
        return result

    hub.send_runner, hub.dispatch = send_runner, dispatch
    step = asyncio.create_task(Orchestrator(hub).run_step(Task(
        agent_id="worker", request_id="halted-request", prompt="test", meta={"kind": "step", "step_id": "s1"})))
    try:
        await asyncio.wait_for(started.wait(), 20)
        first_id = calls[0].id
        assert (await hub.control_task(first_id, "halt"))["sent"]
        proceed.set()
        for _ in range(400):
            if first_id in hub.halted_waits:
                break
            await asyncio.sleep(0.05)
        assert first_id in hub.halted_waits
        assert not step.done()
        other = await asyncio.wait_for(Orchestrator(hub).run_step(Task(
            agent_id="worker", request_id="other-request", prompt="other",
            meta={"kind": "step", "step_id": "s2"})), 30)
        assert other.ok and tool_effects == ["other-request"]
        assert (await hub.control_task(first_id, "resume"))["resumed_halted"]
        result = await asyncio.wait_for(step, 30)
        resumed = [task for task in calls if task.request_id == "halted-request"]
        assert result.ok and result.session_id == "session-59"
        assert len(resumed) == 2 and resumed[1].resume_session_id == "session-59"
        assert resumed[1].meta["parent_task"] == first_id
        assert tool_effects == ["other-request", "halted-request"]
    finally:
        proceed.set()
        if not step.done():
            step.cancel()
        await asyncio.gather(step, return_exceptions=True)
