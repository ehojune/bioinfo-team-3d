"""Mock commit boundaries only: no gateway/runner processes or disk-delay experiment."""
import asyncio
from contextlib import contextmanager
import threading
import time
from unittest.mock import AsyncMock

import anyio
import pytest

from labhq import durable
from labhq.gateway.server import Hub
from labhq.models import TaskResult
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.fixture
def hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    gateway = Hub(settings)
    gateway.requests["req"] = {"id": "req", "status": "running", "results": {}}
    gateway.store.put("task", "t", {"kind": "direct", "request_id": "req", "completed": False,
                                   "payload": {"agent_id": "mock"}})
    monkeypatch.setattr(gateway, "_push", AsyncMock(return_value=True))
    monkeypatch.setattr(gateway, "asave_request", AsyncMock())
    monkeypatch.setattr(gateway, "send_runner", AsyncMock())
    yield gateway
    gateway.store.close()
    assert durable.lane(gateway.store.lane).shutdown(5)


def boundary(hub, monkeypatch, *, delay=0.2, fail=False):
    """Pause the first control transaction's mock commit, release from a thread."""
    original = hub.store._transaction
    target = threading.local()
    write = getattr(hub.store, "_control_state_now", None)
    put = hub.store._put_now
    trace, during = [], {}
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def wrapped(*args):
        target.control = True
        try:
            return write(*args)
        finally:
            target.control = False

    def wrapped_put(kind, *args):
        target.control = kind == "task_control_state"
        try:
            return put(kind, *args)
        finally:
            target.control = False

    @contextmanager
    def transaction():
        selected = getattr(target, "control", False) and not entered.is_set()
        with original() as conn:
            yield conn
            if selected:
                trace.append("commit-enter")
                during["memory"] = dict(hub.task_controls)
                during["row"] = hub.store.get("task_control_state", "t")
                entered.set()
                assert release.wait(5), "mock commit controller timed out"
                if fail:
                    trace.append("rollback")
                    raise OSError("mock control commit failure")
        if selected:
            trace.append("commit-done")

    def controller():
        if entered.wait(5):
            loop.call_soon_threadsafe(trace.append, "heartbeat")
            time.sleep(delay)
        release.set()

    if write is not None:
        monkeypatch.setattr(hub.store, "_control_state_now", wrapped)
    monkeypatch.setattr(hub.store, "_put_now", wrapped_put)
    monkeypatch.setattr(hub.store, "_transaction", transaction)
    worker = threading.Thread(target=controller, daemon=True)
    worker.start()
    return trace, during, entered, release, worker


@pytest.mark.parametrize("active", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("delay", [0, 0.2], ids=["baseline", "slow"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_control_task_commit_heartbeat_memory_event_reopen(hub, monkeypatch, active, delay, fail):
    assert durable.lane(hub.store.lane).shutdown(5)
    if active:
        await durable.run(hub.store.lane, lambda: None)
    trace, during, _, release, worker = boundary(hub, monkeypatch, delay=delay, fail=fail)
    record = hub._record_published_event

    def observe(event):
        if event["type"] == "agent.control":
            assert hub.task_controls["t"] == hub.store.get("task_control_state", "t")
            trace.append("event")
        return record(event)

    monkeypatch.setattr(hub, "_record_published_event", observe)
    try:
        if fail:
            with pytest.raises(OSError, match="mock control"):
                await hub.control_task("t", "pause")
            assert "t" not in hub.task_controls
            assert hub.store.get("task_control_state", "t") is None
            assert "event" not in trace
        else:
            answer = await hub.control_task("t", "pause")
            assert answer["sent"]
            assert trace.index("commit-done") < trace.index("event")
        assert during == {"memory": {}, "row": None}
        if delay:
            if fail:
                assert trace.index("heartbeat") < trace.index("rollback")
            else:
                assert trace.index("heartbeat") < trace.index("commit-done")
        path, expected = hub.store.path, hub.store.get("task_control_state", "t")
        reopened = StateStore(path)
        try:
            assert reopened.get("task_control_state", "t") == expected
        finally:
            reopened.close()
    finally:
        release.set()
        worker.join(2)
        assert not worker.is_alive()


async def wait_entered(entered):
    for _ in range(500):
        if entered.is_set():
            return
        await asyncio.sleep(0.002)
    pytest.fail("commit did not start")


@pytest.mark.parametrize("close", [False, True], ids=["merge", "delete"])
async def test_same_row_fifo_runner_latest_and_delete_no_resurrection(hub, monkeypatch, close):
    trace, _, entered, release, worker = boundary(hub, monkeypatch)
    first = asyncio.create_task(hub._publish_control("t", {}, {"paused": True, "source": "pi"}))
    await wait_entered(entered)
    # These actual runner and result/expiry row operations queue behind the PI.
    runner = asyncio.create_task(hub.on_runner_message("r", {
        "type": "agent.control", "task_id": "t", "data": {"halt": True, "source": "runner"}}))
    await asyncio.sleep(0)
    delete = asyncio.create_task(hub._control_state("t", None)) if close else None
    release.set()
    await first
    await runner
    if delete:
        await delete
        await hub.on_runner_message("r", {"type": "agent.control", "task_id": "t", "runner_seq": 5,
                                          "data": {"paused": True}})
        assert hub.store.runner_seen("r") == 5
        assert hub.store.get("task_control_state", "t") is None
        assert "t" not in hub.task_controls
        # A fresh Store/Hub reads the durable deletion marker; late runner replay stays closed.
        other = Hub(hub.s)
        try:
            await other.on_runner_message("r", {"type": "agent.control", "task_id": "t",
                                                "data": {"paused": True}})
            assert "t" not in other.task_controls
            assert other.store.get("task_control_state", "t") is None
        finally:
            other.store.close()
        # PI controls through an ended turn remain supported for the live step.
        await hub._publish_control("t", {}, {"paused": False})
        assert hub.store.get("task_control_closed", "t") is None
        assert hub.task_controls["t"]["paused"] is False
    else:
        row = hub.store.get("task_control_state", "t")
        assert row["paused"] is True and row["halt"] is True and row["source"] == "runner"
        assert row == hub.task_controls["t"]
        assert [e["data"]["source"] for e in hub.events if e["type"] == "agent.control"] == ["pi", "runner"]
    worker.join(2)
    assert not worker.is_alive()


async def test_control_started_before_result_delete_cannot_reopen_row(hub, monkeypatch):
    pushed, resume = asyncio.Event(), asyncio.Event()

    async def push(*args):
        pushed.set()
        await resume.wait()
        return True

    monkeypatch.setattr(hub, "_push", push)
    pending = asyncio.create_task(hub.control_task("t", "pause"))
    await pushed.wait()
    await hub.on_runner_message("r", {"type": "task.result", "task_id": "t", "request_id": "req",
                                      "data": {"task_id": "t", "agent_id": "mock", "ok": True}})
    resume.set()
    await pending
    assert hub.store.get("task_control_state", "t") is None
    assert "t" not in hub.task_controls
    assert not [e for e in hub.events if e["type"] == "agent.control"]


async def test_pi_controls_keep_step_order_while_first_send_waits(hub, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    sends = []

    async def push(tid, message):
        sends.append(message["action"])
        if message["action"] == "pause":
            entered.set()
            await release.wait()
        return True

    monkeypatch.setattr(hub, "_push", push)
    first = asyncio.create_task(hub.control_task("t", "pause"))
    await entered.wait()
    second = asyncio.create_task(hub.control_task("t", "resume"))
    await asyncio.sleep(0.02)
    release.set()
    await asyncio.gather(first, second)
    assert sends == ["pause", "resume"]
    assert hub.task_controls["t"]["paused"] is False
    assert hub._step(hub._entry_step("t", hub.store.get("task", "t")))["paused"] is False
    assert [e["data"]["action"] for e in hub.events if e["type"] == "agent.control"] == ["pause", "resume"]


async def test_runner_then_pi_merges_current_row_and_freezes_input(hub, monkeypatch):
    _, _, entered, release, worker = boundary(hub, monkeypatch)
    message = {"type": "agent.control", "task_id": "t", "data": {"halted": True, "halt": True}}
    hub.halted_waits["t"] = asyncio.Event()
    runner = asyncio.create_task(hub.on_runner_message("r", message))
    await wait_entered(entered)
    message["data"]["halt"] = False
    second = asyncio.create_task(hub._publish_control("t", {}, {"paused": True, "halted": False}))
    release.set()
    await asyncio.gather(runner, second)
    assert hub.task_controls["t"]["halt"] is True
    assert hub.task_controls["t"]["halted"] is True
    assert hub.task_controls["t"]["paused"] is True
    assert hub.store.get("task_control_state", "t") == hub.task_controls["t"]
    worker.join(2)


@pytest.mark.parametrize("operation", ["delete", "replace"])
@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_result_row_delete_replace_failure_and_repeated_cancel(hub, monkeypatch, operation, fail):
    await hub._publish_control("t", {}, {"paused": True})
    before = dict(hub.task_controls["t"])
    _, _, entered, release, worker = boundary(hub, monkeypatch, delay=0.3, fail=fail)
    pending = asyncio.create_task(hub._control_state(
        "t", None if operation == "delete" else {"halted": True}, replace=True))
    await wait_entered(entered)
    assert hub.task_controls["t"] == before
    for _ in range(3):
        pending.cancel()
        await asyncio.sleep(0.005)
    release.set()
    if fail:
        with pytest.raises(OSError, match="mock control"):
            await pending
        assert hub.task_controls["t"] == before
        assert hub.store.get("task_control_state", "t") == before
        assert hub.store.get("task_control_closed", "t") is None
    else:
        with pytest.raises(asyncio.CancelledError):
            await pending
        if operation == "delete":
            assert "t" not in hub.task_controls
            assert hub.store.get("task_control_state", "t") is None
            assert hub.store.get("task_control_closed", "t") is not None
        else:
            assert hub.task_controls["t"]["halted"] is True
            assert "paused" not in hub.task_controls["t"]
            assert hub.store.get("task_control_state", "t") == hub.task_controls["t"]
    worker.join(2)


@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_runner_storage_failure_never_publishes_or_acks(hub, monkeypatch, fail):
    _, _, entered, release, worker = boundary(hub, monkeypatch, fail=fail)
    pending = asyncio.create_task(hub.on_runner_message("r", {
        "type": "agent.control", "task_id": "t", "runner_seq": 3, "data": {"paused": True}}))
    await wait_entered(entered)
    hub.send_runner.assert_not_awaited()
    assert not hub.events and not hub.task_controls
    release.set()
    if fail:
        with pytest.raises(OSError, match="mock control"):
            await pending
        assert not hub.events and not hub.task_controls
        assert hub.store.runner_seen("r") == 0
        hub.send_runner.assert_not_awaited()
    else:
        await pending
        assert hub.store.runner_seen("r") == 3
        hub.send_runner.assert_awaited_once()
        assert len([e for e in hub.events if e["type"] == "agent.control"]) == 1
    worker.join(2)


async def test_halt_wait_repeated_cancel_finishes_cleanup_and_expiry_event(hub, monkeypatch):
    await hub._control_state("t", {"halted": True}, replace=True)
    monkeypatch.setattr(hub, "_publish_hold_status", AsyncMock())
    # Wait until the park was published before cancelling it.
    parked = asyncio.Event()
    publish = hub.publish

    async def observed(event, **kwargs):
        await publish(event, **kwargs)
        if event["type"] == "request.step_halted":
            parked.set()

    monkeypatch.setattr(hub, "publish", observed)
    pending = asyncio.create_task(hub.wait_halted("req", "direct", TaskResult(
        task_id="t", agent_id="mock", ok=False, error_kind="halted")))
    await parked.wait()
    _, _, entered, release, worker = boundary(hub, monkeypatch, delay=0.3)
    pending.cancel()
    await wait_entered(entered)
    for _ in range(3):
        pending.cancel()
        await asyncio.sleep(0.005)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert "t" not in hub.task_controls and "t" not in hub.halted_waits
    assert not hub.requests["req"].get("pi_waits")
    assert hub.store.get("task_control_state", "t") is None
    assert hub.store.get("task", "t")["halt_wait_finished"] is True
    assert len([e for e in hub.events if e["type"] == "request.step_halt_expired"]) == 1
    worker.join(2)


@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
@pytest.mark.parametrize("kind", ["asyncio", "anyio"])
async def test_cancel_settles_commit_memory_event_and_preserves_error(hub, monkeypatch, fail, kind):
    _, _, entered, release, worker = boundary(hub, monkeypatch, delay=0.3, fail=fail)
    scope = anyio.CancelScope()

    async def call():
        if kind == "anyio":
            with scope:
                await hub._publish_control("t", {}, {"paused": True})
        else:
            await hub._publish_control("t", {}, {"paused": True})

    pending = asyncio.create_task(call())
    await wait_entered(entered)
    if kind == "anyio":
        scope.cancel()
    else:
        for _ in range(3):
            pending.cancel()
            await asyncio.sleep(0.005)
    release.set()
    if fail:
        with pytest.raises(OSError, match="mock control"):
            await pending
        assert "t" not in hub.task_controls
        assert not [e for e in hub.events if e["type"] == "agent.control"]
    else:
        if kind == "asyncio":
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert scope.cancel_called
        assert hub.task_controls["t"]["paused"] is True
        assert hub.store.get("task_control_state", "t") == hub.task_controls["t"]
        assert len([e for e in hub.events if e["type"] == "agent.control"]) == 1
    worker.join(2)
    assert not worker.is_alive()
