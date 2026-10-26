"""Task cancellation intent must commit without blocking the gateway (#606)."""
import asyncio
from contextlib import contextmanager
import threading
import time

import pytest
import anyio

from labhq import durable
from labhq.gateway.server import Hub, RunnerUnavailable
from labhq.store import StateStore


@pytest.fixture
def hub(tmp_path):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.task_runner = {}
    hub.store.put("task", "t", {"runner_id": "r", "completed": False})
    yield hub
    hub.store.close()


@pytest.mark.parametrize("active_lane", [False, True])
async def test_cancel_intent_allows_heartbeat_before_commit(hub, monkeypatch, active_lane):
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    transaction = hub.store._transaction
    timeline = []

    @contextmanager
    def slow_commit():
        with transaction() as conn:
            yield conn
            time.sleep(0.2)
            timeline.append("commit")

    async def send(runner, message):
        assert hub.store.get("task_cancel", "t")["runner_id"] == runner
        timeline.append("send")

    async def heartbeat():
        await asyncio.sleep(0.02)
        timeline.append("heartbeat")

    monkeypatch.setattr(hub.store, "_transaction", slow_commit)
    monkeypatch.setattr(hub, "send_runner", send)
    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    assert await hub.cancel_task("t")
    await beat
    assert timeline == ["heartbeat", "commit", "send"]


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("cancel_mode", ["asyncio", "anyio"])
async def test_cancel_intent_settles_on_repeated_cancellation_and_restart(hub, monkeypatch, fail, cancel_mode):
    transaction = hub.store._transaction
    entered, release = threading.Event(), threading.Event()
    sent = []

    @contextmanager
    def held_commit():
        with transaction() as conn:
            yield conn
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test commit was not released")
            if fail:
                raise OSError("mock rollback")

    async def send(runner, message):
        sent.append(message)

    monkeypatch.setattr(hub.store, "_transaction", held_commit)
    monkeypatch.setattr(hub, "send_runner", send)
    scope = anyio.CancelScope()
    async def cancel():
        if cancel_mode == "anyio":
            with scope:
                return await hub.cancel_task("t")
        return await hub.cancel_task("t")
    action = asyncio.create_task(cancel())
    try:
        async def wait_entered():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_entered(), 6)
        for _ in range(3):
            if cancel_mode == "anyio":
                scope.cancel()
            else:
                action.cancel()
            await asyncio.sleep(0)
        assert not action.done()
        assert hub.store.get("task_cancel", "t") is None
        assert not sent
    finally:
        release.set()
    with pytest.raises(OSError if fail else asyncio.CancelledError):
        await action
    reopened = StateStore(hub.store.path)
    try:
        assert (reopened.get("task_cancel", "t") is None) is fail
        assert reopened.get("task", "t") == {"runner_id": "r", "completed": False}
    finally:
        reopened.close()
    assert not sent


async def test_offline_cancel_is_replayed_after_restart(hub, monkeypatch):
    async def offline(runner, message):
        raise RunnerUnavailable("mock offline")
    monkeypatch.setattr(hub, "send_runner", offline)
    assert await hub.cancel_task("t")
    reopened = StateStore(hub.store.path)
    original = hub.store
    hub.store = reopened
    sent = []
    async def send(runner, message):
        sent.append((runner, message))
    monkeypatch.setattr(hub, "send_runner", send)
    try:
        await hub.flush_task_cancels("r")
        assert sent == [("r", {"type": "task.cancel", "task_id": "t"})]
        assert reopened.get("task_cancel", "t") is not None
    finally:
        hub.store = original
        reopened.close()
