"""Mock-only regression of #606 activity task-row storage."""
import asyncio
from contextlib import contextmanager
import json
import threading
import time

import pytest

from labhq import durable
from labhq.gateway.server import Hub
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.mark.parametrize("active_lane", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_activity_heartbeat_during_task_row_commit(tmp_path, monkeypatch, active_lane, fail):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    initial = {"runner_id": "r", "completed": False, "activity": {"state": "queued"}}
    hub.store.put("task", "t", initial)
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    assert durable.lane(hub.store.lane).active() == active_lane
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    trace, controller_errors = [], []
    timing = {}
    observations = {}
    original = hub.store._transaction

    def mark(label):
        trace.append(label)
        timing[label] = time.perf_counter()

    @contextmanager
    def blocked_commit():
        try:
            with original() as conn:
                yield conn
                mark("commit-enter")
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("controller did not release mock commit")
                if fail:
                    raise OSError("mock activity commit failure")
            mark("commit-done")
        except OSError:
            mark("commit-rollback")
            raise

    def controller():
        try:
            if not entered.wait(3):
                raise TimeoutError("activity storage never entered")
            observations["during"] = hub.store.get("task", "t")
            loop.call_soon_threadsafe(mark, "heartbeat")
            # A thread releases the boundary even when the event loop is blocked.
            time.sleep(0.2)
            observations["heartbeat_before_release"] = "heartbeat" in trace
            mark("release")
        except BaseException as exc:
            controller_errors.append(repr(exc))
        finally:
            release.set()

    async def publish(*args, **kwargs):
        mark("publish")

    async def send(*args, **kwargs):
        mark("ack")

    monkeypatch.setattr(hub.store, "_transaction", blocked_commit)
    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    worker = threading.Thread(target=controller, daemon=True)
    worker.start()
    caught = None
    try:
        try:
            await hub.on_runner_message("r", {
                "type": "agent.status", "task_id": "t", "runner_seq": 1,
                "data": {"state": "working"}, "ts": 1,
            })
        except OSError as exc:
            caught = str(exc)
        await asyncio.sleep(0)
        worker.join(1)
        assert not worker.is_alive() and not controller_errors, controller_errors
        assert entered.is_set()
        assert observations["during"] == initial
        after = hub.store.get("task", "t")
        if fail:
            assert caught == "mock activity commit failure"
            assert after == initial
            assert "publish" not in trace and "ack" not in trace
        else:
            assert caught is None
            assert after["activity"]["state"] == "working"
            assert after["completed"] is False and after["runner_id"] == "r"
            assert trace.index("commit-done") < trace.index("publish") < trace.index("ack")
        path = hub.store.path
        hub.store.close()
        reopened = StateStore(path)
        try:
            assert reopened.get("task", "t") == after
        finally:
            reopened.close()
        print(json.dumps({
            "active_lane": active_lane, "fail": fail, "trace": trace,
            "heartbeat_delay_ms": round((timing["heartbeat"] - timing["commit-enter"]) * 1000, 2),
            "heartbeat_before_release": observations["heartbeat_before_release"],
            "row_after": after, "row_reopen_matches": True,
            "storage_state_checks": "PASS", "error": caught,
        }, sort_keys=True))
        assert observations["heartbeat_before_release"], trace
    finally:
        release.set()
        worker.join(1)
        if not hub.store._closed:
            hub.store.close()


@pytest.fixture
def activity_hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    trace = []

    async def publish(*args, **kwargs):
        trace.append("publish")

    async def send(*args, **kwargs):
        trace.append("ack")

    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    yield hub, trace
    hub.store.close()


def activity_message(tool="one", seq=1):  # a runner never reuses a seq for another message (#1143)
    return {"type": "agent.tool", "task_id": "t", "runner_seq": seq,
            "data": {"id": tool, "name": "mock", "input": {"value": "original"}}, "ts": 1}


@pytest.mark.parametrize("change", ["terminal", "control", "deleted", "other_activity"])
async def test_activity_reads_latest_row_after_queued_write(activity_hub, monkeypatch, change):
    hub, trace = activity_hub
    initial = {"completed": False, "runner_id": "r", "activity": {"state": "queued"}}
    hub.store.put("task", "t", initial)
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3), "test failed to release writer"

    blocker = asyncio.create_task(durable.run(hub.store.lane, block))
    queued = asyncio.Event()
    original = hub.store.aupdate_task_activity

    async def observed(*args):
        queued.set()
        await original(*args)

    monkeypatch.setattr(hub.store, "aupdate_task_activity", observed)
    pending = []
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if change == "deleted":
            previous = asyncio.create_task(hub.store.adelete("task", "t"))
            latest = None
        else:
            latest = {**initial, "new_field": "preserved"}
            if change == "terminal":
                latest.update(completed=True, result={"ok": True})
            elif change == "control":
                latest.update(control={"paused": True}, control_seq=7)
            else:
                latest["activity"] = {"state": "working", "tools": {"earlier": {"name": "kept"}}}
            previous = asyncio.create_task(hub.store.aput("task", "t", latest))
        pending.append(previous)
        # FIFO tasks submit the older write before the activity reads its row.
        message = asyncio.create_task(hub.on_runner_message("r", activity_message()))
        pending.append(message)
        await asyncio.wait_for(queued.wait(), 3)
        assert hub.store.get("task", "t") == initial
        assert trace == []
        release.set()
        await asyncio.gather(blocker, *pending)
        after = hub.store.get("task", "t")
        if change in {"terminal", "deleted"}:
            assert after == latest
        else:
            assert {k: v for k, v in after.items() if k != "activity"} == {
                k: v for k, v in latest.items() if k != "activity"}
            assert "one" in after["activity"]["tools"]
            if change == "other_activity":
                assert after["activity"]["tools"]["earlier"] == {"name": "kept"}
        assert trace == ["publish", "ack"]
    finally:
        release.set()
        await asyncio.gather(blocker, *pending, return_exceptions=True)


async def test_concurrent_activity_merges_tools_and_owns_inputs(activity_hub, monkeypatch):
    hub, _ = activity_hub
    hub.store.put("task", "t", {"completed": False, "activity": {"state": "queued"}})
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3)

    blocker = asyncio.create_task(durable.run(hub.store.lane, block))
    queued = asyncio.Event()
    original = hub.store.aupdate_task_activity
    count = 0

    async def observed(*args):
        nonlocal count
        count += 1
        if count == 2:
            queued.set()
        await original(*args)

    monkeypatch.setattr(hub.store, "aupdate_task_activity", observed)
    pending = []
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        first = activity_message("one")
        pending = [asyncio.create_task(hub.on_runner_message("r", first)),
                   asyncio.create_task(hub.on_runner_message("r", activity_message("two", seq=2)))]
        await asyncio.wait_for(queued.wait(), 3)
        first["data"]["input"]["value"] = "mutated"
        release.set()
        await asyncio.gather(blocker, *pending)
        tools = hub.store.get("task", "t")["activity"]["tools"]
        assert set(tools) == {"one", "two"}
        assert tools["one"]["input"] == {"value": "original"}
        await hub.on_runner_message("r", {"type": "agent.tool_result", "task_id": "t", "data": {"id": "one"}})
        assert set(hub.store.get("task", "t")["activity"]["tools"]) == {"two"}
    finally:
        release.set()
        await asyncio.gather(blocker, *pending, return_exceptions=True)


@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
@pytest.mark.parametrize("cancel_kind", ["asyncio", "anyio"])
async def test_cancel_during_activity_commit_keeps_write_without_publish_or_ack(
        activity_hub, monkeypatch, fail, cancel_kind):
    import anyio

    hub, trace = activity_hub
    initial = {"completed": False, "activity": {"state": "queued"}}
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
                raise OSError("mock cancelled activity failure")

    monkeypatch.setattr(hub.store, "_transaction", blocked_commit)
    scope = anyio.CancelScope()

    async def run():
        with scope:
            await hub.on_runner_message("r", activity_message())

    pending = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert hub.store.get("task", "t") == initial
        if cancel_kind == "asyncio":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            scope.cancel()
            await pending
            assert scope.cancelled_caught
        assert trace == []
        assert hub.store.runner_seen("r") == 0
        release.set()
        assert await durable.lane(hub.store.lane).adrain(3)
        after = hub.store.get("task", "t")
        if fail:
            assert after == initial
        else:
            assert after["activity"]["tools"]["one"]["input"] == {"value": "original"}
        assert trace == []
        monkeypatch.setattr(hub.store, "_transaction", original)
        # Unacknowledged replay is safe after either completed or rolled back storage.
        await hub.on_runner_message("r", activity_message())
        assert set(hub.store.get("task", "t")["activity"]["tools"]) == {"one"}
        assert trace == ["publish", "ack"]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await durable.lane(hub.store.lane).adrain(3)
