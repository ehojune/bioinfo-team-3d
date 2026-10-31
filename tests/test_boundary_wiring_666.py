"""Offline wiring regressions, never evidence of OS protection or real cleanup."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from labhq.adapters.base import AgentAdapter, ProcessBoundaryGate, RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.runner import process_boundary
from labhq.runner.boundary_observer import ReplayStore
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


async def test_default_adapter_preserves_existing_preflight(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    ctx.task.meta.update(process_boundary=True, protection_verified=True)
    ctx.env["LABHQ_PROCESS_BOUNDARY"] = "1"
    producer = Mock(side_effect=AssertionError("default must not call producer"))
    monkeypatch.setattr(process_boundary, "protected_launch", producer)
    adapter = StubAdapter(ctx.settings)
    adapter.staff_env = Mock(return_value={})
    adapter.preflight_error = Mock(return_value="existing policy refusal")
    result = await adapter.run(ctx)
    assert result.error == "existing policy refusal"
    adapter.staff_env.assert_called_once_with(ctx)
    producer.assert_not_called()


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


@pytest.mark.parametrize("outcome", ["exception", "success", "malformed"])
async def test_producer_error_or_success_never_falls_back(tmp_path, monkeypatch, outcome):
    receipt = process_boundary.protected_launch()
    receipt.preparation, receipt.launch, receipt.seal_status = "ready", "exited_0", "verified"
    producer = Mock(side_effect=RuntimeError("secret-path-and-key")) if outcome == "exception" else Mock(
        return_value=receipt if outcome == "success" else None)
    monkeypatch.setattr(process_boundary, "protected_launch", producer)
    ctx = context(tmp_path, required=True)
    result = await StubAdapter(ctx.settings).run(ctx)
    assert not result.ok and result.error_kind == "boundary_unsupported"
    assert "secret-path-and-key" not in result.error
    assert result.cleanup_evidence == {}


async def test_default_runner_reaches_existing_gate(monkeypatch):
    instance = runner()
    existing = RuntimeError("existing gate reached")
    instance._refuse_unprotected_environment = AsyncMock(side_effect=existing)
    producer = Mock(side_effect=AssertionError("must stay off"))
    monkeypatch.setattr(process_boundary, "protected_launch", producer)
    with pytest.raises(RuntimeError, match="existing gate reached"):
        await instance.run_task(Task(agent_id="worker", prompt="offline"))
    producer.assert_not_called()
    instance.emit_all.assert_not_called()


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


def observation(tmp_path, *, problem=None):
    fixture = json.loads((Path(__file__).parent / "fixtures/process_boundary_seal/lifecycle-v1.json").read_text(
        encoding="utf-8"))
    events = copy.deepcopy(fixture["events"])
    if problem == "tamper":
        events[1]["identity"]["birth_id"] = "different"

    def collect(phase, identity):
        if problem == "cancel":
            raise asyncio.CancelledError()
        if problem == "timeout":
            raise TimeoutError("synthetic collector timeout")
        return dict(run_id="fixture-run", nonce="fixture-nonce", phase=phase, identity=identity,
                    processes=[identity] if phase == "launch" or problem == "remaining" else [],
                    resources=[], provenance="offline-test", sequence=1 if phase == "launch" else 2,
                    complete=True)

    return events, dict(run_id="fixture-run", nonce="fixture-nonce", producer_key_id="fixture-producer",
                        producer_key=fixture["test_key_utf8"].encode(), observer_key_id="fixture-observer",
                        observer_key=b"offline-observer-key", replay_store=ReplayStore(tmp_path / "replay.sqlite"),
                        collector=collect)


def test_observer_success_cannot_authorize_engine_and_replay_is_rejected(tmp_path):
    instance = runner(required=True)
    events, expectations = observation(tmp_path)
    evidence = instance.process_boundary.observe_lifecycle(events, **expectations)
    assert evidence["scope"] == "synthetic_only"
    result = instance.process_boundary.refusal(Task(agent_id="worker", prompt="offline"))
    assert not result.ok and result.cleanup_evidence == {}
    with pytest.raises(ValueError, match="replay"):
        instance.process_boundary.observe_lifecycle(events, **expectations)


def test_real_producer_signer_stream_reaches_observer_without_launch(tmp_path):
    instance = runner(required=True)
    _, expectations = observation(tmp_path)
    delivered = []
    signer = process_boundary.DiagnosticLifecycleEvents(
        run_id=expectations["run_id"], nonce=expectations["nonce"],
        key_id=expectations["producer_key_id"], key=expectations["producer_key"],
        identity_reader=lambda pid: {"pid": pid, "birth_id": "synthetic-incarnation"}, emit=delivered.append)

    def stream():
        signer._claim_run(expectations["run_id"])
        signer._launch(123)
        yield delivered.pop()
        signer._termination()
        yield delivered.pop()

    evidence = instance.process_boundary.observe_lifecycle(stream(), **expectations)
    assert evidence["scope"] == "synthetic_only"
    assert evidence["identity"] == {"pid": 123, "birth_id": "synthetic-incarnation"}
    assert not instance.process_boundary.refusal(Task(agent_id="worker", prompt="offline")).ok


async def test_producer_cancellation_propagates_without_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(process_boundary, "protected_launch", Mock(side_effect=asyncio.CancelledError()))
    ctx = context(tmp_path, required=True)
    adapter = StubAdapter(ctx.settings)
    adapter.prepare = Mock(side_effect=AssertionError("no preparation"))
    with pytest.raises(asyncio.CancelledError):
        await adapter.run(ctx)
    adapter.prepare.assert_not_called()


@pytest.mark.parametrize("problem,error", [("tamper", ValueError), ("remaining", ValueError),
                                          ("timeout", TimeoutError), ("cancel", asyncio.CancelledError)])
def test_observer_failure_is_not_swallowed_or_reusable(tmp_path, problem, error):
    instance = runner(required=True)
    events, expectations = observation(tmp_path, problem=problem)
    with pytest.raises(error):
        instance.process_boundary.observe_lifecycle(events, **expectations)
    with pytest.raises(ValueError, match="replay"):
        instance.process_boundary.observe_lifecycle(events, **expectations)
    assert not instance.process_boundary.refusal(Task(agent_id="worker", prompt="offline")).ok
