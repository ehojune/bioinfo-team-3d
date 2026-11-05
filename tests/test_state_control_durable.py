"""Mock-only regressions for control writes queued behind task activity (#606)."""
import asyncio
from contextlib import contextmanager
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from labhq import durable
from labhq.gateway.server import Hub
from labhq.settings import Settings
from labhq.store import StateStore


async def test_halt_after_activity_read_keeps_committed_tool(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    initial = {"runner_id": "r", "completed": False, "kind": "direct",
               "activity": {"state": "queued"}}
    hub.store.put("task", "t", initial)
    entered, control_read = threading.Event(), threading.Event()
    transaction = hub.store._transaction
    get = hub.store.get
    block_once = True
    sent = []

    @contextmanager
    def held_commit():
        nonlocal block_once
        with transaction() as conn:
            yield conn
            if block_once:
                block_once = False
                entered.set()
                assert control_read.wait(3)

    def observe_read(kind, key):
        value = get(kind, key)
        if kind == "task" and key == "t" and entered.is_set():
            control_read.set()
        return value

    async def send(*args):
        sent.append(args)

    monkeypatch.setattr(hub.store, "_transaction", held_commit)
    monkeypatch.setattr(hub.store, "get", observe_read)
    monkeypatch.setattr(hub, "controllable", lambda *args: True)
    monkeypatch.setattr(hub, "_step_alive", lambda *args: True)
    monkeypatch.setattr(hub, "_turn_waits", lambda *args: False)
    monkeypatch.setattr(hub, "_save_step", AsyncMock())
    monkeypatch.setattr(hub, "_push", AsyncMock(return_value=True))
    async def publish(ev, runner_id=None, runner_seq=None, activity=None):
        # The real append: the runner message's task activity is saved with its event in one transaction (#1132).
        await hub.store.aappend_event(ev, 100, runner_id, runner_seq, activity=activity)

    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    activity = asyncio.create_task(hub.on_runner_message("r", {
        "type": "agent.tool", "task_id": "t", "runner_seq": 1,
        "data": {"id": "tool", "name": "mock"}, "ts": 1}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        result = await hub.control_task("t", "halt")
        await activity
        after = hub.store.get("task", "t")
        print(json.dumps({"result": result, "after": after, "sent": sent}, sort_keys=True))
        assert after["halt_requested"] is True
        assert any(args[1].get("type") == "runner.ack" for args in sent)
        assert "tool" in after["activity"].get("tools", {}), "committed and ACKed activity was lost"
    finally:
        control_read.set()
        await asyncio.gather(activity, return_exceptions=True)
        hub.store.close()


@pytest.fixture
def control_hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    monkeypatch.setattr(hub, "controllable", lambda *args: True)
    monkeypatch.setattr(hub, "_step_alive", lambda *args: True)
    monkeypatch.setattr(hub, "_turn_waits", lambda *args: False)
    monkeypatch.setattr(hub, "_save_step", AsyncMock())
    monkeypatch.setattr(hub, "_push", AsyncMock(return_value=True))
    monkeypatch.setattr(hub, "_publish_control", AsyncMock())
    yield hub
    hub.store.close()


def row(action):
    return {"runner_id": "r", "kind": "direct", "completed": action == "resume",
            "activity": {"state": "queued"},
            **({"result": {"error_kind": "halted"}} if action == "resume" else {})}


@pytest.mark.parametrize("action", ["halt", "resume"])
@pytest.mark.parametrize("active_lane", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_control_commit_keeps_heartbeat_and_orders_notifications(
        control_hub, monkeypatch, action, active_lane, fail):
    hub = control_hub
    initial = row(action)
    hub.store.put("task", "t", initial)
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    original = hub.store._transaction
    entered, release = threading.Event(), threading.Event()
    beat = threading.Event()
    observations = []
    loop = asyncio.get_running_loop()

    @contextmanager
    def blocked_commit():
        with original() as conn:
            yield conn
            entered.set()
            assert release.wait(3)
            if fail:
                raise OSError("mock control rollback")

    def controller():
        try:
            if entered.wait(3):
                loop.call_soon_threadsafe(beat.set)
                time.sleep(0.2)
                observations.append((beat.is_set(), hub.store.get("task", "t"),
                                     hub._push.call_count, hub._publish_control.call_count))
        finally:
            release.set()

    monkeypatch.setattr(hub.store, "_transaction", blocked_commit)
    worker = threading.Thread(target=controller, daemon=True)
    worker.start()
    try:
        if fail:
            with pytest.raises(OSError, match="mock control rollback"):
                await hub.control_task("t", action)
        else:
            result = await hub.control_task("t", action)
            assert result.get("sent") is (action == "halt")
        worker.join(1)
        assert not worker.is_alive()
        assert observations == [(True, initial, 0, 0)]
        after = hub.store.get("task", "t")
        field = "halt_requested" if action == "halt" else "halt_resume_requested"
        assert after == (initial if fail else {**initial, field: True})
        assert hub._push.call_count == int(not fail and action == "halt")
        assert hub._publish_control.call_count == int(not fail and action == "halt")
        reopened = StateStore(hub.store.path)
        try:
            assert reopened.get("task", "t") == after
        finally:
            reopened.close()
    finally:
        release.set()
        worker.join(1)


@pytest.mark.parametrize("action", ["halt", "resume"])
@pytest.mark.parametrize("change", ["activity", "deleted", "terminal", "abandoned"])
async def test_control_merges_latest_queued_row(control_hub, monkeypatch, action, change):
    hub = control_hub
    initial = row(action)
    hub.store.put("task", "t", initial)
    entered, release = threading.Event(), threading.Event()
    queued = asyncio.Event()
    original = hub.store.aupdate_task_control

    def block():
        entered.set()
        assert release.wait(3)

    blocker = durable.submit(hub.store.lane, block)
    latest = {**initial, "activity": {"tools": {"kept": {"name": "mock"}}}, "other": [1, 2]}
    if change == "terminal":
        latest.update(completed=True, result={"ok": True}, halt_wait_finished=True)
    elif change == "abandoned":
        latest.update(completed=True, abandoned=True)

    async def observed(*args):
        # The control call has read its old row. Place a mutation ahead of its writer job.
        if change == "deleted":
            durable.submit(hub.store.lane, hub.store._delete_now, "task", "t")
        else:
            durable.submit(hub.store.lane, hub.store._put_now, "task", "t", json.dumps(latest))
        queued.set()
        return await original(*args)

    monkeypatch.setattr(hub.store, "aupdate_task_control", observed)
    pending = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        pending = asyncio.create_task(hub.control_task("t", action))
        await asyncio.wait_for(queued.wait(), 3)
        release.set()
        if change in {"deleted", "terminal"}:
            with pytest.raises(KeyError if change == "deleted" else ValueError):
                await pending
            assert hub.store.get("task", "t") == (None if change == "deleted" else latest)
            hub._push.assert_not_called()
            hub._publish_control.assert_not_called()
        else:
            await pending
            field = "halt_requested" if action == "halt" else "halt_resume_requested"
            assert hub.store.get("task", "t") == {**latest, field: True}
    finally:
        release.set()
        await asyncio.wrap_future(blocker)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("action", ["halt", "resume"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
@pytest.mark.parametrize("cancel_kind", ["asyncio", "anyio"])
async def test_cancel_control_settles_commit_and_suppresses_notifications(
        control_hub, monkeypatch, action, fail, cancel_kind):
    import anyio

    hub = control_hub
    initial = row(action)
    hub.store.put("task", "t", initial)
    entered, release = threading.Event(), threading.Event()
    original = hub.store._transaction

    @contextmanager
    def blocked_commit():
        with original() as conn:
            yield conn
            entered.set()
            assert release.wait(3)
            if fail:
                raise OSError("cancelled control rollback")

    monkeypatch.setattr(hub.store, "_transaction", blocked_commit)
    scope = anyio.CancelScope()
    cancelled = []

    async def run():
        with scope:
            try:
                return await hub.control_task("t", action)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    pending = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if cancel_kind == "asyncio":
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
        else:
            scope.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        assert hub.store.get("task", "t") == initial
        release.set()
        if fail:
            with pytest.raises(OSError, match="cancelled control rollback"):
                await pending
        else:
            try:
                await pending
            except asyncio.CancelledError:
                pass
            assert cancelled == [True]
        field = "halt_requested" if action == "halt" else "halt_resume_requested"
        assert hub.store.get("task", "t") == (initial if fail else {**initial, field: True})
        hub._push.assert_not_called()
        hub._publish_control.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("action", ["halt", "resume"])
@pytest.mark.parametrize("change", ["deleted", "terminal"])
async def test_completed_after_control_commit_is_not_signalled(control_hub, monkeypatch, action, change):
    hub = control_hub
    hub.store.put("task", "t", row(action))
    original = hub.store.aupdate_task_control
    terminal = {"completed": True, "result": {"ok": True}, "halt_wait_finished": True}

    async def overtaken(*args):
        result = await original(*args)
        if change == "deleted":
            await hub.store.adelete("task", "t")
        else:
            await hub.store.aput("task", "t", terminal)
        return result

    monkeypatch.setattr(hub.store, "aupdate_task_control", overtaken)
    with pytest.raises(ValueError):
        await hub.control_task("t", action)
    assert hub.store.get("task", "t") == (None if change == "deleted" else terminal)
    hub._push.assert_not_called()
    hub._publish_control.assert_not_called()


@pytest.mark.parametrize("cancel", [False, True])
async def test_resume_wakes_waiter_registered_during_commit(control_hub, monkeypatch, cancel):
    hub = control_hub
    hub.store.put("task", "t", row("resume"))
    key = hub._entry_step("t", row("resume"))
    state = hub._step(key)
    state.update(paused=True, pause_op=1, halt_op=2)
    await hub._save_step(key, state)
    # Keep a real step row for the pause-order cleanup being asserted.
    hub.store.put("step_control", key, state)
    entered, release = threading.Event(), threading.Event()
    original = hub.store._transaction

    @contextmanager
    def blocked_commit():
        with original() as conn:
            yield conn
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(hub.store, "_transaction", blocked_commit)
    saved = []
    monkeypatch.setattr(hub, "_save_step", AsyncMock(side_effect=lambda key, state: saved.append(dict(state))))
    pending = asyncio.create_task(hub.control_task("t", "resume"))
    event = asyncio.Event()
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        hub.halted_waits["t"] = event
        if cancel:
            pending.cancel()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await pending
        assert event.is_set()
        assert saved and saved[-1]["paused"] is False
        hub._push.assert_not_called()
        hub._publish_control.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_erased_halt_without_waiting_is_rejected_for_retry(control_hub, monkeypatch):
    hub = control_hub
    initial = row("halt")
    hub.store.put("task", "t", initial)
    original = hub.store.aupdate_task_control

    async def overwritten(*args):
        result = await original(*args)
        await hub.store.aput("task", "t", {**initial, "accepted": True})
        return result

    monkeypatch.setattr(hub.store, "aupdate_task_control", overwritten)
    with pytest.raises(ValueError, match="다시 시도"):
        await hub.control_task("t", "halt")
    assert hub.store.get("task", "t") == {**initial, "accepted": True}
    hub._push.assert_not_called()
    hub._publish_control.assert_not_called()
