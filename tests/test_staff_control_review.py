"""PR #532 review rounds 1-2, in the step-owned design: a control the runner could not apply is NACKed and kept on the
step, a queued task halts at once, and every end of a halt wait closes the halted row."""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from labhq.gateway.server import Hub
from labhq.hooks import control as hook
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _runner(tmp_path, monkeypatch, run=None, engine=Engine.codex) -> Runner:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / "runner" / name))
    runner = Runner(settings)
    runner.CONTROL_RETRY_S = (0.0, 0.0)  # the bounded local retries, without waiting in a test
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            if ctx.on_started:
                ctx.on_started()
            return await run(ctx)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    return runner


def _hub(tmp_path) -> Hub:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True, "step_id": "A", "dispatched_at": 1.0})
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}
    hub.save_request("r")
    return hub


def _task(tid, session=None) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="표를 만드세요", resume_session_id=session,
                meta={"kind": "step", "step_id": "s1"})


def _msg(tid, action, steer_id=None, text=None):
    return {"type": "task.control", "task_id": tid, "action": action, "agent_id": "worker",
            **({"steer": {"id": steer_id, "text": text}} if steer_id else {})}


def _sent(runner, kind):
    return [event for event in runner.store.pending() if event["type"] == kind]


@pytest.mark.asyncio
async def test_a_control_the_runner_could_not_apply_is_nacked_and_its_steer_stays_on_the_step(tmp_path, monkeypatch):
    runner, hub = _runner(tmp_path, monkeypatch), _hub(tmp_path)
    runner.controls.start("t1", "worker", hooks=True)
    attempts = []

    def busy(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError("control file lock is busy")

    async def send_runner(_runner_id, message):
        await runner._on_message(message)

    hub.send_runner = send_runner
    monkeypatch.setattr(hook, "update_state", busy)
    assert (await hub.control_task("t1", "steer", "chr1만 보세요"))["sent"] is True
    assert len(attempts) == 3  # the first try and two bounded retries
    [nack] = _sent(runner, "control.nack")
    assert nack["reason"].startswith("not applied") and nack["action"] == "steer"
    await hub.on_runner_message("pc-a", nack, None, None)
    [steer] = hub._step(hub.step_key("r", "A", None, "t1"))["steers"]
    assert steer["text"] == "chr1만 보세요" and steer["task"] is None  # waiting for the step's next dispatch


@pytest.mark.asyncio
async def test_a_halt_for_a_task_waiting_for_a_run_slot_takes_effect_at_once(tmp_path, monkeypatch):
    release, started = asyncio.Event(), []

    async def run(ctx):
        started.append(ctx.task.id)
        if ctx.task.id == "t1":
            await release.wait()
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="S1")

    runner = _runner(tmp_path, monkeypatch, run)
    runner.sem = asyncio.Semaphore(1)  # runner.max_parallel: 1, taken by t1
    first = asyncio.create_task(runner.run_task(_task("t1")))
    await asyncio.sleep(0.1)
    queued = asyncio.create_task(runner.run_task(_task("t2", session="S2")))
    await asyncio.sleep(0.1)
    assert started == ["t1"] and not queued.done()

    await runner._on_message(_msg("t2", "halt"))
    result = await asyncio.wait_for(queued, 2)
    assert result.error_kind == "halted" and result.session_id == "S2" and started == ["t1"]
    logs = [e["data"]["text"] for e in runner.store.pending() if e["type"] == "agent.log" and e["task_id"] == "t2"]
    assert any("시작하기 전에 멈췄어요" in text for text in logs)
    assert not any("프로세스를 끝냅니다" in text for text in logs)  # nothing claims it stopped before it did
    release.set()
    assert (await asyncio.wait_for(first, 2)).ok
    # The slot the halted task never took is still there for the next one.
    assert (await asyncio.wait_for(runner.run_task(_task("t3")), 2)).ok


@pytest.mark.asyncio
async def test_a_halt_nobody_resumes_tells_open_pages_it_expired(tmp_path):
    hub = _hub(tmp_path)
    hub.s.policy.approvals.pi_decision_timeout_s = 0.05
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="sess-1")
    assert await hub.wait_halted("r", "A", halted) is False
    [expired] = [e for e in hub.events if e["type"] == "request.step_halt_expired"]
    assert expired["task_id"] == "t1" and expired["agent_id"] == "worker"
    assert expired["data"] == {"step_id": "A", "reason": "timed_out"}
    assert not [e for e in hub.events if e["type"] == "request.step_resumed"]
    assert hub.requests["r"]["status"] == "running" and not hub.halted_waits and "t1" not in hub.task_controls


@pytest.mark.asyncio
async def test_a_halt_wait_ended_by_request_cancel_still_closes_the_halted_row(tmp_path):
    hub = _hub(tmp_path)
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="S")
    waiter = asyncio.create_task(hub.wait_halted("r", "A", halted))
    for _ in range(100):
        if hub.halted_waits:
            break
        await asyncio.sleep(0.01)
    waiter.cancel()  # what cancel_request does to the request's orchestration
    with contextlib.suppress(asyncio.CancelledError):
        await waiter
    [closed] = [e for e in hub.events if e["type"] == "request.step_halt_expired"]
    assert closed["task_id"] == "t1" and closed["data"] == {"step_id": "A", "reason": "cancelled"}
    assert not hub.halted_waits


@pytest.mark.asyncio
async def test_a_paused_task_that_goes_into_an_hpc_wait_keeps_its_pause_after_a_reload(tmp_path):
    hub = _hub(tmp_path)
    await hub.on_runner_message("pc-a", {"type": "agent.control", "task_id": "t1", "agent_id": "worker",
                                         "request_id": "r", "data": {"paused": True, "mode": "next_turn"}}, None, None)
    await hub.on_runner_message("pc-a", {"type": "task.result", "request_id": "r", "task_id": "t1",
                                         "data": {"task_id": "t1", "agent_id": "worker", "ok": True,
                                                  "pending_jobs": ["42"]}}, None, None)
    [task] = hub.snapshot()["data"]["running_tasks"]
    assert task["state"] == "hibernating" and task["control"]["paused"] is True
    await hub.on_runner_message("pc-a", {"type": "task.result", "request_id": "r", "task_id": "t1",
                                         "data": {"task_id": "t1", "agent_id": "worker", "ok": True}}, None, None)
    assert "t1" not in hub.task_controls
