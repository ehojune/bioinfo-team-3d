"""#606 isolated diagnostic: real Hub/Store, mock commit delay, no processes."""
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
from labhq.models import Task
from labhq.store import StateStore


@pytest.mark.parametrize("repeat", range(1))
@pytest.mark.parametrize("active_lane", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("delay", [0.0, 0.2], ids=["baseline", "slow"])
@pytest.mark.parametrize("action", ["pause", "resume"])
async def test_step_commit_heartbeat(tmp_path, monkeypatch, repeat, active_lane, delay, action):
    await diagnose(tmp_path, monkeypatch, repeat, active_lane, delay, action)


@pytest.mark.parametrize("active_lane", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("delay", [0.0, 0.2], ids=["baseline", "slow"])
async def test_step_delete_helper_heartbeat(tmp_path, monkeypatch, active_lane, delay):
    # control_task retains last_task, so its ordinary resume takes put, not delete.
    # Exercise the delete arm directly; this is not a full API delete path.
    await diagnose(tmp_path, monkeypatch, 0, active_lane, delay, "delete-helper")


async def diagnose(tmp_path, monkeypatch, repeat, active_lane, delay, action):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.requests["req"] = {"id": "req", "status": "running", "results": {}}
    hub.store.put("task", "task", {"kind": "step", "step_id": "s1", "request_id": "req",
                                  "completed": False, "payload": {"agent_id": "mock"}})
    key = hub.step_key("req", "s1", "step", "task")
    if action != "pause":
        hub.store.put("step_control", key, {"paused": True})
    monkeypatch.setattr(hub, "asave_request", AsyncMock())
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    assert durable.lane(hub.store.lane).active() is active_lane
    trace = []
    started, caller = time.perf_counter(), threading.get_ident()

    def mark(name, **values):
        trace.append({"name": name, "ms": round((time.perf_counter() - started) * 1000, 3),
                      "thread": "loop" if threading.get_ident() == caller else "writer", **values})

    class Controls(dict):
        def __setitem__(self, k, value):
            mark("control-memory", action=value.get("action"))
            super().__setitem__(k, value)

    class Wake(asyncio.Event):
        def set(self):
            mark("resume-wake")
            super().set()

    hub.task_controls = Controls(hub.task_controls)
    hub.step_resume_events[key] = Wake()
    context = threading.local()
    transaction = hub.store._transaction
    record = hub._record_published_event

    def observe_write(original):
        def wrapped(kind, *args):
            context.target = kind == "step_control"
            try:
                return original(kind, *args)
            finally:
                context.target = False
        return wrapped

    @contextmanager
    def delayed_transaction():
        selected = getattr(context, "target", False)
        with transaction() as conn:
            yield conn
            if selected:
                mark("step-commit-enter", stored_before=hub.store.get("step_control", key),
                     control_memory=dict(hub.task_controls), wake_set=hub.step_resume_events[key].is_set())
                # Delay only the selected transaction's C commit boundary model.
                # No os.fsync monkeypatch and no real disk latency measurement.
                time.sleep(delay)
        if selected:
            mark("step-commit-done", stored_after=hub.store.get("step_control", key))

    control_now = hub.store._control_state_now

    def observed_control(*args):
        mark("control-commit-enter")
        result = control_now(*args)
        mark("control-commit-done")
        return result

    async def push(*args):
        mark("runner-send-mock")
        return True

    def observed_event(event):
        if event.get("type") == "agent.control":
            mark("control-event")
        return record(event)

    monkeypatch.setattr(hub.store, "_put_now", observe_write(hub.store._put_now))
    monkeypatch.setattr(hub.store, "_delete_now", observe_write(hub.store._delete_now))
    monkeypatch.setattr(hub.store, "_transaction", delayed_transaction)
    monkeypatch.setattr(hub.store, "_control_state_now", observed_control)
    monkeypatch.setattr(hub, "_push", push)
    monkeypatch.setattr(hub, "_record_published_event", observed_event)
    heartbeat = asyncio.Event()

    def beat():
        mark("heartbeat")
        heartbeat.set()

    timer = asyncio.get_running_loop().call_later(0.05, beat)
    try:
        if action == "delete-helper":
            await hub._save_step(key, hub._step("empty-key"))
            result = None
        else:
            result = await hub.control_task("task", action)
        mark("response")
        await asyncio.wait_for(heartbeat.wait(), 1)
        print("DIAGNOSTIC " + json.dumps({"repeat": repeat, "lane": "active" if active_lane else "idle",
              "delay_ms": int(delay * 1000), "action": action, "result": result, "trace": trace}, sort_keys=True))
        names = [item["name"] for item in trace]
        assert names.count("step-commit-enter") == names.count("step-commit-done") == 1
        assert names.index("step-commit-enter") < names.index("step-commit-done") < names.index("response")
        if action == "delete-helper":
            assert hub.store.get("step_control", key) is None
        else:
            assert result["sent"] is True
            assert hub.store.get("step_control", key)["paused"] is (action == "pause")
            assert names.index("step-commit-done") < names.index("runner-send-mock")
            assert names.index("control-commit-done") < names.index("control-memory") < names.index("control-event")
            if action == "resume":
                assert names.index("step-commit-done") < names.index("resume-wake") < names.index("runner-send-mock")
        if delay:
            assert names.index("heartbeat") < names.index("step-commit-done"), "slow step commit blocked heartbeat"
        else:
            assert "heartbeat" in names
    finally:
        timer.cancel()
        hub.store.close()
        assert durable.lane(hub.store.lane).shutdown(5)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    gateway = Hub(settings)
    gateway.requests["req"] = {"id": "req", "status": "running", "results": {}}
    gateway.store.put("task", "t", {"kind": "step", "step_id": "s1", "request_id": "req",
                                   "completed": False, "payload": {"agent_id": "mock"}})
    monkeypatch.setattr(gateway, "_push", AsyncMock(return_value=True))
    monkeypatch.setattr(gateway, "asave_request", AsyncMock())
    monkeypatch.setattr(gateway, "send_runner", AsyncMock())
    yield gateway
    gateway.store.close()
    assert durable.lane(gateway.store.lane).shutdown(5)


def task(tid="t", step="s1"):
    return Task(id=tid, request_id="req", agent_id="mock", prompt="mock",
                meta={"kind": "step", "step_id": step})


def hold_step(hub, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = hub.store.aput

    async def held(kind, key, body):
        if kind == "step_control" and not entered.is_set():
            entered.set()
            await release.wait()
        return await original(kind, key, body)

    monkeypatch.setattr(hub.store, "aput", held)
    return entered, release


@pytest.mark.parametrize("mutation", ["settle", "release", "attach", "close", "nack"])
async def test_pi_races_use_latest_step_and_do_not_resurrect(hub, monkeypatch, mutation):
    key = hub._entry_step("t", hub.store.get("task", "t"))
    await hub.control_task("t", "steer", "first")
    first = hub._step(key)["steers"][0]["id"]
    entered, release = hold_step(hub, monkeypatch)
    pi = asyncio.create_task(hub.control_task("t", "steer", "second"))
    await asyncio.wait_for(entered.wait(), 2)
    entry = hub.store.get("task", "t")
    if mutation == "settle":
        work = hub._settle_steers("t", entry, {"delivered": [first]})
    elif mutation == "release":
        work = hub._release_steers(task())
    elif mutation == "attach":
        work = hub._attach_steers(task())
    elif mutation == "close":
        work = hub._close_step_controls("req", "s1")
    else:
        # A completed turn's NACK returns its ID, preserving the concurrent steer.
        await hub.store.aput("task", "t", {**entry, "completed": True})
        work = hub.on_runner_message("r", {"type": "control.nack", "task_id": "t",
                                            "action": "steer", "steer_id": first})
    other = asyncio.create_task(work)
    try:
        await asyncio.sleep(0)
        assert not other.done()
        release.set()
        await pi
        result = await other
        if mutation == "close":
            assert hub.store.get("step_control", key) is None
            await hub._settle_steers("t", entry, None)
            await hub._attach_steers(task("late"))
            assert hub.store.get("step_control", key) is None
            notices = [ev for ev in hub.events if (ev.get("data") or {}).get("action") == "undelivered"]
            assert notices[-1]["data"]["undelivered"] == ["first", "second"]
        else:
            state = hub._step(key)
            assert [s["text"] for s in state["steers"]] == (["second"] if mutation == "settle" else ["first", "second"])
            assert len({s["id"] for s in state["steers"]}) == len(state["steers"])
            if mutation == "attach":
                assert [s["id"] for s in result.meta["steers"]] == [s["id"] for s in state["steers"]]
            elif mutation == "release":
                assert all(s["task"] is None for s in state["steers"])
            elif mutation == "nack":
                assert state["steers"][0]["task"] is None
    finally:
        release.set()
        await asyncio.gather(pi, other, return_exceptions=True)


async def test_same_step_fifo_and_other_step_independence(hub, monkeypatch):
    entry = hub.store.get("task", "t")
    hub.store.put("task", "b", {**entry, "step_id": "s2"})
    entered, release = hold_step(hub, monkeypatch)
    pause = asyncio.create_task(hub.control_task("t", "pause"))
    await asyncio.wait_for(entered.wait(), 2)
    resume = asyncio.create_task(hub.control_task("t", "resume"))
    try:
        await asyncio.wait_for(hub.control_task("b", "pause"), 2)
        assert not resume.done()
        release.set()
        await asyncio.gather(pause, resume)
        messages = [call.args[1]["action"] for call in hub._push.call_args_list if call.args[0] == "t"]
        assert messages == ["pause", "resume"]
        assert not hub._step(hub._entry_step("t", entry))["paused"]
    finally:
        release.set()
        await asyncio.gather(pause, resume, return_exceptions=True)


async def test_steer_after_result_settlement_waits_for_next_turn(hub, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = hub._control_state

    async def held(tid, data, **kwargs):
        if data is None:
            entered.set()
            await release.wait()
        return await original(tid, data, **kwargs)

    monkeypatch.setattr(hub, "_control_state", held)
    result = asyncio.create_task(hub.on_runner_message("r", {
        "type": "task.result", "task_id": "t", "request_id": "req",
        "data": {"task_id": "t", "agent_id": "mock", "ok": True}}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        reply = await hub.control_task("t", "steer", "next turn")
        assert reply["queued"] and not reply["sent"]
        release.set()
        await result
        attached = await hub._attach_steers(task("next"))
        assert [s["text"] for s in attached.meta["steers"]] == ["next turn"]
    finally:
        release.set()
        await asyncio.gather(result, return_exceptions=True)


async def test_close_during_send_suppresses_late_control_notice(hub, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def push(*args):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(hub, "_push", push)
    pending = asyncio.create_task(hub.control_task("t", "pause"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await hub._close_step_controls("req", "s1")
        release.set()
        with pytest.raises(ValueError, match="step ended"):
            await pending
        assert not [e for e in hub.events if e.get("type") == "agent.control"]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_new_revision_reopens_step_but_old_turn_cannot(hub):
    key = hub._entry_step("t", hub.store.get("task", "t"))
    await hub.control_task("t", "pause")
    await hub._close_step_controls("req", "s1")
    await hub._attach_steers(task("late-old"))
    assert hub.store.get("step_control", key) is None
    revised = task("revised").model_copy(update={"meta": {"kind": "step", "step_id": "s1", "revision": 1}})
    await hub._attach_steers(revised)
    assert hub.store.get("step_control", key)["last_task"] == "revised"
    await hub._attach_steers(task("late-again"))
    assert hub.store.get("step_control", key)["last_task"] == "revised"
    await hub._close_step_controls("req", "s1")
    await hub._attach_steers(revised)
    assert hub.store.get("step_control", key) is None


async def test_revision_attachment_accepts_previous_result_context(hub):
    key = hub._entry_step("t", hub.store.get("task", "t"))
    await hub._close_step_controls("req", "s1")
    # Revision construction may retain its previous result as context.
    hub.requests["req"]["results"]["s1"] = {"ok": True, "text": "previous"}
    revised = task("revised").model_copy(update={"meta": {"kind": "step", "step_id": "s1", "revision": 1}})
    await hub._attach_steers(revised)
    assert hub.store.get("step_control", key)["last_task"] == "revised"


@pytest.mark.parametrize("operation", ["pause", "resume", "steer", "attach", "settle", "close"])
async def test_step_rollback_suppresses_wake_send_and_notice(hub, monkeypatch, operation):
    key = hub._entry_step("t", hub.store.get("task", "t"))
    await hub.control_task("t", "steer", "keep")
    if operation == "resume":
        await hub.control_task("t", "pause")
    initial = hub.store.get("step_control", key)
    hub.step_resume_events[key] = asyncio.Event()
    hub._push.reset_mock()
    before_events = list(hub.events)
    transaction = hub.store._transaction

    @contextmanager
    def fail():
        with transaction() as conn:
            yield conn
            raise OSError("mock step rollback")

    monkeypatch.setattr(hub.store, "_transaction", fail)
    if operation == "attach":
        work = hub._attach_steers(task())
    elif operation == "settle":
        work = hub._settle_steers("t", hub.store.get("task", "t"), {"delivered": [initial["steers"][0]["id"]]})
    elif operation == "close":
        work = hub._close_step_controls("req", "s1")
    else:
        work = hub.control_task("t", operation, "never sent")
    with pytest.raises(OSError, match="mock step rollback"):
        await work
    assert hub.store.get("step_control", key) == initial
    assert not hub.step_resume_events[key].is_set()
    hub._push.assert_not_called()
    assert list(hub.events) == before_events
    assert key not in getattr(hub, "_closed_steps", set())


@pytest.mark.parametrize("operation", ["steer", "attach", "close", "resume"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
@pytest.mark.parametrize("cancel_kind", ["asyncio", "anyio"])
async def test_cancel_settles_step_and_replays_committed_ids(hub, monkeypatch, operation, fail, cancel_kind):
    import anyio

    key = hub._entry_step("t", hub.store.get("task", "t"))
    await hub.control_task("t", "steer", "keep")
    if operation == "resume":
        await hub.control_task("t", "pause")
    initial = hub.store.get("step_control", key)
    hub.step_resume_events[key] = asyncio.Event()
    entered, release = threading.Event(), threading.Event()
    hub._push.reset_mock()
    transaction = hub.store._transaction
    once = True

    @contextmanager
    def blocked():
        nonlocal once
        with transaction() as conn:
            yield conn
            if once:
                once = False
                entered.set()
                assert release.wait(3)
                if fail:
                    raise OSError("cancelled step rollback")

    monkeypatch.setattr(hub.store, "_transaction", blocked)
    scope = anyio.CancelScope()
    was_cancelled = []

    async def run():
        with scope:
            try:
                if operation == "steer":
                    await hub.control_task("t", "steer", "replay")
                elif operation == "attach":
                    await hub._attach_steers(task())
                elif operation == "resume":
                    await hub.control_task("t", "resume")
                else:
                    await hub._close_step_controls("req", "s1")
            except asyncio.CancelledError:
                was_cancelled.append(True)
                raise

    pending = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if cancel_kind == "anyio":
            scope.cancel()
        else:
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        assert hub.store.get("step_control", key) == initial
        assert not hub.step_resume_events[key].is_set()
        release.set()
        if fail:
            with pytest.raises(OSError, match="cancelled step rollback"):
                await pending
            assert hub.store.get("step_control", key) == initial
        else:
            try:
                await pending
            except asyncio.CancelledError:
                pass
            assert was_cancelled
            state = hub.store.get("step_control", key)
            if operation == "close":
                assert state is None
            elif operation == "attach":
                assert state["steers"][0]["task"] is None
            elif operation == "resume":
                assert not state["paused"]
                assert hub.step_resume_events[key].is_set()
            else:
                assert [s["text"] for s in state["steers"]] == ["keep", "replay"]
                assert [c.args[1]["steer"]["id"] for c in hub._push.call_args_list] == [state["steers"][-1]["id"]]
                hub._push.reset_mock()
                await hub._push_task_state("t")
                assert [c.args[1]["steer"]["id"] for c in hub._push.call_args_list] == [s["id"] for s in state["steers"]]
        restored = StateStore(hub.store.path)
        try:
            assert restored.get("step_control", key) == hub.store.get("step_control", key)
        finally:
            restored.close()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
