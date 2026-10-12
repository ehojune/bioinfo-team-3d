"""PR #532 review round 2: a control stays on disk until consumed, carries are per staff member, a queued task halts
at once, and every end of a halt wait closes the halted row."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from labhq.gateway.server import Hub
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _runner(tmp_path, monkeypatch, run, engine=Engine.codex) -> Runner:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / "runner" / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            started = getattr(ctx, "on_started", None)
            if started:  # what AgentAdapter.run does once the CLI process exists
                started()
            return await run(ctx)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    return runner


def _task(tid, session=None) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="표를 만드세요", resume_session_id=session,
                meta={"kind": "step", "step_id": "s1"})


def _msg(tid, action, seq, text=None, agent="worker"):
    return {"type": "task.control", "id": f"ctl-{seq}", "seq": seq, "task_id": tid, "action": action,
            "agent_id": agent, **({"text": text} if text else {})}


async def _settle(seconds=0.2):
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_a_pause_and_steer_survive_a_runner_restart_while_the_next_turn_waits(tmp_path, monkeypatch):
    started = []

    async def run(ctx):
        started.append((ctx.task.id, ctx.prompt))
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="S")

    first = _runner(tmp_path, monkeypatch, run)
    # An earlier turn of session S ended with a steer and a pause the PI sent.
    first.controls.start("t1", "worker", hooks=False, session_id="S")
    await first._on_message(_msg("t1", "steer", 1, "QC 기준을 0.9로"))
    await first._on_message(_msg("t1", "pause", 2))
    first.controls.finish("t1", "S")
    # The session's next turn starts and waits for resume.
    held = asyncio.create_task(first.run_task(_task("t2", session="S")))
    await _settle()
    assert started == [] and not held.done()

    # The runner process stops here (nothing of it cleans up) and a new one starts on the same state.
    second = _runner(tmp_path, monkeypatch, run)
    retry = asyncio.create_task(second.run_task(_task("t3", session="S")))
    await _settle()
    assert started == [] and not retry.done(), "the pause still holds the session's turn"
    await second._on_message(_msg("t3", "resume", 3))
    await asyncio.wait_for(retry, 5)
    [(task_id, prompt)] = started
    assert task_id == "t3" and prompt.count("QC 기준을 0.9로") == 1
    # Consumed: the steer went into a started prompt and the pause was lifted, so nothing is carried any more.
    again = asyncio.create_task(second.run_task(_task("t4", session="S")))
    await asyncio.wait_for(again, 5)
    assert [entry[0] for entry in started] == ["t3", "t4"] and "QC 기준" not in started[1][1]
    held.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await held


@pytest.mark.asyncio
async def test_a_halt_sent_before_a_runner_restart_parks_the_interrupted_task(tmp_path, monkeypatch):
    async def run(ctx):
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True)

    first = _runner(tmp_path, monkeypatch, run, engine=Engine.claude_code)
    first.store.put("accepted_task", "t1", {"state": "running", "task": _task("t1", "S").model_dump(mode="json")})
    first.controls.start("t1", "worker", hooks=True, session_id="S")
    await first._on_message(_msg("t1", "halt", 1))
    second = _runner(tmp_path, monkeypatch, run, engine=Engine.claude_code)
    [result] = [e["data"] for e in second.store.pending() if e["type"] == "task.result" and e["task_id"] == "t1"]
    assert result["error_kind"] == "halted" and result["session_id"] == "S" and result["ok"] is False


@pytest.mark.asyncio
async def test_two_staff_with_the_same_session_string_keep_their_own_carry(tmp_path, monkeypatch):
    async def run(ctx):
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True)

    runner = _runner(tmp_path, monkeypatch, run)
    runner.controls.start("a1", "alice", hooks=False, session_id="default")
    await runner._on_message(_msg("a1", "steer", 1, "alice에게만", agent="alice"))
    runner.controls.finish("a1", "default")
    # A late pause for alice's ended task goes to alice's carry too.
    await runner._on_message(_msg("a1", "pause", 2, agent="alice"))

    bob_steers, _dropped, bob_paused = runner.controls.start("b1", "bob", hooks=False, session_id="default")
    assert bob_steers == [] and bob_paused is False
    alice_steers, _dropped, alice_paused = runner.controls.start("a2", "alice", hooks=False, session_id="default")
    assert [s["text"] for s in alice_steers] == ["alice에게만"] and alice_paused is True
    assert json.dumps(["alice", "default"]) in runner.store.all("control_carry")


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
    await _settle(0.1)
    queued = asyncio.create_task(runner.run_task(_task("t2", session="S2")))
    await _settle(0.1)
    assert started == ["t1"] and not queued.done()

    await runner._on_message(_msg("t2", "halt", 1))
    result = await asyncio.wait_for(queued, 2)
    assert result.error_kind == "halted" and result.session_id == "S2" and started == ["t1"]
    logs = [e["data"]["text"] for e in runner.store.pending() if e["type"] == "agent.log" and e["task_id"] == "t2"]
    assert any("시작하기 전에 멈췄어요" in text for text in logs)
    assert not any("프로세스를 끝냅니다" in text for text in logs)  # nothing claims it stopped before it did
    release.set()
    assert (await asyncio.wait_for(first, 2)).ok
    # The slot the halted task never took is still there for the next one.
    assert (await asyncio.wait_for(runner.run_task(_task("t3")), 2)).ok


def _hub(tmp_path) -> Hub:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True, "step_id": "A", "dispatched_at": 1.0})
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}
    hub.save_request("r")
    return hub


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
    # A result that ends the task for good drops it.
    await hub.on_runner_message("pc-a", {"type": "task.result", "request_id": "r", "task_id": "t1",
                                         "data": {"task_id": "t1", "agent_id": "worker", "ok": True}}, None, None)
    assert "t1" not in hub.task_controls
