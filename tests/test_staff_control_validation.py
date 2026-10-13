"""Third verification: reconnect convergence, sends racing step mutations, and request boundaries."""
import asyncio

import pytest

from labhq.hooks import control as hook
from labhq.runner.control import Controls
from test_staff_control_review import _hub
from test_staff_control_steps import FakeWs


@pytest.mark.asyncio
async def test_reconnect_applies_resume_sent_while_runner_was_offline(tmp_path):
    hub = _hub(tmp_path)
    controls = Controls(tmp_path / "controls")
    controls.start("t1", "worker", hooks=True)
    controls.start("t2", "worker", hooks=False)
    await controls.apply({"task_id": "t2", "action": "pause"})
    ws = FakeWs()
    hub.register_runner("pc-a", ws, [{"id": "worker", "engine": "codex"}], "inc-1", {}, "proc-1")
    await hub.control_task("t1", "pause")
    await controls.apply(ws.frames[-1])
    hub.unregister_runner("pc-a", ws)
    assert not (await hub.control_task("t1", "resume"))["sent"]
    ws = FakeWs()
    hub.register_runner("pc-a", ws, [{"id": "worker", "engine": "codex"}], "inc-1", {}, "proc-1")
    await hub.flush_task_controls("pc-a")
    for frame in ws.frames:
        if frame.get("type") == "task.control":
            await controls.apply(frame)
    assert not controls.paused("t1")
    assert not hook.locked_state(controls.path("t1"))["paused"]
    assert controls.paused("t2"), "reconnect must leave another task of the same staff member alone"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["steer", "pause", "settle", "close"])
async def test_state_flush_does_not_overwrite_changes_while_send_waits(tmp_path, monkeypatch, mutation):
    hub = _hub(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    block = False

    async def push(_tid, message):
        if block and message["action"] == "steer" and not entered.is_set():
            entered.set()
            await release.wait()
        return True

    monkeypatch.setattr(hub, "_push", push)
    await hub.control_task("t1", "steer", "first")
    entry = hub.store.get("task", "t1")
    key = hub._entry_step("t1", entry)
    first = hub._step(key)["steers"][0]["id"]
    block = True
    flush = asyncio.create_task(hub._push_task_state("t1"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if mutation == "steer":
            await hub.control_task("t1", "steer", "second")
        elif mutation == "pause":
            await hub.control_task("t1", "pause")
        elif mutation == "settle":
            hub._settle_steers("t1", entry, {"delivered": [first]})
        else:
            await hub._close_step_controls("r", "A")
        expected = hub.store.get("step_control", key)
    finally:
        release.set()
        await asyncio.wait_for(flush, 2)
    assert hub.store.get("step_control", key) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "done", "failed", "interrupted"])
@pytest.mark.parametrize("action", ["steer", "pause", "resume", "halt"])
async def test_terminal_request_refuses_controls_even_before_task_result(tmp_path, monkeypatch, status, action):
    hub = _hub(tmp_path)
    hub.requests["r"]["status"] = status
    sent = []

    async def push(tid, message):
        sent.append((tid, message))
        return True

    monkeypatch.setattr(hub, "_push", push)
    with pytest.raises(ValueError, match="끝나"):
        await hub.control_task("t1", action, "late direction" if action == "steer" else None)
    assert not sent and not hub.store.all("step_control")
