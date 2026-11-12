"""Offline wiring regressions, never evidence of OS protection or real cleanup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from labhq.adapters.base import AgentAdapter, ProcessBoundaryGate, RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.runner.daemon import Runner
from labhq.settings import Settings


class StubAdapter(AgentAdapter):
    engine = "claude_code"

    def build_command(self, ctx):
        raise AssertionError("command construction must not be reached")

    async def handle_line(self, line, st, ctx):
        raise AssertionError("no CLI stream expected")


def context(tmp_path, *, required=False):
    settings = Settings()
    return RunContext(task=Task(agent_id="worker", prompt="offline"),
                      agent=AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code),
                      workdir=tmp_path, settings=settings, mcp_servers=[], env={},
                      emit=AsyncMock(), prompt="offline", process_boundary=ProcessBoundaryGate(required))


def runner(*, required=False):
    instance = Runner.__new__(Runner)
    instance.process_boundary = ProcessBoundaryGate(required)
    instance.emit_all = AsyncMock()
    return instance


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true"])
def test_opt_in_requires_an_explicit_bool(value):
    with pytest.raises(ValueError, match="bool"):
        Runner(Settings(), process_boundary=value)


async def test_default_adapter_preserves_existing_preflight(tmp_path):
    ctx = context(tmp_path)
    ctx.task.meta.update(process_boundary=True, protection_verified=True)
    ctx.env["LABHQ_PROCESS_BOUNDARY"] = "1"
    adapter = StubAdapter(ctx.settings)
    adapter.staff_env = Mock(return_value={})
    adapter.preflight_error = Mock(return_value="existing policy refusal")
    result = await adapter.run(ctx)
    assert result.error == "existing policy refusal"
    adapter.staff_env.assert_called_once_with(ctx)


async def test_default_runner_reaches_existing_gate():
    instance = runner()
    instance._refuse_unprotected_environment = AsyncMock(side_effect=RuntimeError("existing gate reached"))
    with pytest.raises(RuntimeError, match="existing gate reached"):
        await instance.run_task(Task(agent_id="worker", prompt="offline"))
    instance.emit_all.assert_not_called()


@pytest.mark.parametrize("entry", ["run_task", "_run_task"])
async def test_legacy_runner_double_without_boundary_reaches_existing_gate(entry):
    instance = SimpleNamespace(_refuse_unprotected_environment=AsyncMock(
        side_effect=RuntimeError("existing gate reached")))
    with pytest.raises(RuntimeError, match="existing gate reached"):
        await getattr(Runner, entry)(instance, Task(agent_id="worker", prompt="offline"))


async def test_cancellation_during_refusal_does_not_start_task():
    instance = runner(required=True)
    reached = asyncio.Event()

    async def pending(_events):
        reached.set()
        await asyncio.Event().wait()

    instance.emit_all.side_effect = pending
    instance._refuse_unprotected_environment = AsyncMock(side_effect=AssertionError("no preparation"))
    task = asyncio.create_task(instance.run_task(Task(agent_id="worker", prompt="offline")))
    await asyncio.wait_for(reached.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    instance._refuse_unprotected_environment.assert_not_called()


@pytest.mark.parametrize("entry", ["run_task", "_run_task"])
async def test_opt_in_runner_refuses_before_workspace_or_adapter(entry):
    instance = runner(required=True)
    task = Task(agent_id="worker", prompt="offline", resume_session_id="old-session",
                meta={"process_boundary": False, "capability": "verified"})
    result = await getattr(instance, entry)(task)
    assert not result.ok and result.error_kind == "boundary_unsupported"
    assert result.cost_known and result.cost_usd == 0
    assert result.cleanup_evidence == {}
    events = instance.emit_all.await_args.args[0]
    assert [event.type for event in events] == ["agent.status", "task.result"]
    assert events[-1].data["task_id"] == task.id
    assert not events[-1].data["ok"]


async def test_opt_in_adapter_cannot_prepare_or_spawn(tmp_path, monkeypatch):
    ctx = context(tmp_path, required=True)
    adapter = StubAdapter(ctx.settings)
    adapter.staff_env = Mock(side_effect=AssertionError("no credentials read"))
    adapter.prepare = Mock(side_effect=AssertionError("no files written"))
    spawn = AsyncMock(side_effect=AssertionError("no child"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await adapter.run(ctx)
    assert result.error_kind == "boundary_unsupported"
    assert ctx.started_command is None and not ctx.commands_ran
    adapter.staff_env.assert_not_called()
    adapter.prepare.assert_not_called()
    spawn.assert_not_called()
    assert list(tmp_path.iterdir()) == []
