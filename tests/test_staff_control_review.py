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
                                 "accepted": True, "step_id": "A", "kind": "step", "dispatched_at": 1.0})
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
    key = hub.step_key("r", "A", "step", "t1")
    [steer] = hub._step(key)["steers"]
    assert steer["text"] == "chr1만 보세요"  # kept on the step, still meant for the live turn
    # The turn ends without delivering it: it waits for the step's next dispatch.
    await hub.on_runner_message("pc-a", {"type": "task.result", "request_id": "r", "task_id": "t1", "data": {
        "task_id": "t1", "agent_id": "worker", "ok": True, "text": "done",
        "control": {"delivered": [], "undelivered": [], "paused": False}}}, None, None)
    [steer] = hub._step(key)["steers"]
    assert steer["task"] is None


@pytest.mark.asyncio
async def test_a_halt_for_a_task_waiting_for_a_run_slot_takes_effect_at_once(
        tmp_path, monkeypatch, queued_control_io):
    release, first_started, slot_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started = []

    class ObservedSemaphore(asyncio.Semaphore):
        async def acquire(self):
            if self.locked():
                slot_waiting.set()
            return await super().acquire()

    async def run(ctx):
        started.append(ctx.task.id)
        if ctx.task.id == "t1":
            first_started.set()
            await release.wait()
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="S1")

    runner = _runner(tmp_path, monkeypatch, run)
    runner.sem = ObservedSemaphore(1)  # runner.max_parallel: 1, taken by t1
    first = asyncio.create_task(runner.run_task(_task("t1")))
    await asyncio.wait_for(first_started.wait(), 2)
    queued = asyncio.create_task(runner.run_task(_task("t2", session="S2")))
    await asyncio.wait_for(slot_waiting.wait(), 2)
    assert started == ["t1"] and not queued.done()

    await runner._on_message(_msg("t2", "halt"))
    result = await asyncio.wait_for(queued, 2)
    assert result.error_kind == "halted" and result.session_id == "S2" and started == ["t1"]
    assert not release.is_set() and not first.done()  # halt must finish while the owner still holds the slot
    logs = [e["data"]["text"] for e in runner.store.pending() if e["type"] == "agent.log" and e["task_id"] == "t2"]
    assert any("시작하기 전에 멈췄어요" in text for text in logs)
    assert not any("프로세스를 끝냅니다" in text for text in logs)  # nothing claims it stopped before it did
    release.set()
    assert (await asyncio.wait_for(first, 2)).ok
    # The slot the halted task never took is still there for the next one.
    assert (await asyncio.wait_for(runner.run_task(_task("t3")), 2)).ok
    assert started == ["t1", "t3"] and not runner.sem.locked()


@pytest.fixture
def queued_control_io(monkeypatch):
    """This one control test measures semaphore/halt ordering, not executor/disk throughput.

    Windows CI timed out in t3's empty input scan *after* the queued halt assertions passed.
    Run this test's offloaded jobs inline; execute their real bodies, including fsync/SQLite writes.
    Native writer/executor scheduling and durability retain their separate integration coverage.
    """
    from labhq import durable

    async def offload(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def commit(_lane, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", offload)
    monkeypatch.setattr(durable, "run", commit)


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


@pytest.mark.parametrize("publication", ["request.step_halted", "request.status"])
@pytest.mark.parametrize("request_cancelled", [False, True])
async def test_cancel_during_halt_entry_cleans_every_registered_wait(
        tmp_path, monkeypatch, publication, request_cancelled):
    hub = _hub(tmp_path)
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="S")
    row = hub.store.get("task", "t1")
    hub.store.put("task", "t1", {**row, "halt_resume_requested": True})
    hub.task_controls["t1"] = {"action": "halt"}
    hub.store.put("task_control_state", "t1", {"action": "halt"})
    entered, release = asyncio.Event(), asyncio.Event()
    original_publish, blocked = hub.publish, False

    async def publish(event, *args, **kwargs):
        nonlocal blocked
        if event["type"] == publication and not blocked:
            blocked = True
            entered.set()
            await release.wait()
        return await original_publish(event, *args, **kwargs)

    monkeypatch.setattr(hub, "publish", publish)
    waiter = asyncio.create_task(hub.wait_halted("r", "A", halted))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert "t1" in hub.halted_waits and "halt:t1" in hub.requests["r"]["pi_waits"]
        if request_cancelled:
            hub.requests["r"]["status"] = "cancelled"
            hub.save_request("r")
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        release.set()
        if not waiter.done():
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
    [closed] = [event for event in hub.events if event["type"] == "request.step_halt_expired"]
    assert closed["task_id"] == "t1" and closed["data"] == {"step_id": "A", "reason": "cancelled"}
    assert not [event for event in hub.events if event["type"] == "request.step_resumed"]
    assert not hub.halted_waits and "pi_waits" not in hub.requests["r"]
    assert "t1" not in hub.task_controls and hub.store.get("task_control_state", "t1") is None
    row = hub.store.get("task", "t1")
    assert row["halt_wait_finished"] and "halt_resume_requested" not in row
    saved = hub.store.get("request", "r")
    assert "pi_waits" not in saved
    assert saved["status"] == hub.requests["r"]["status"] == ("cancelled" if request_cancelled else "running")


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
