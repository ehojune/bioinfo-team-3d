"""A control cannot promise queued replay after a later row writer erases its intent."""
import asyncio
from contextlib import contextmanager
import threading
from unittest.mock import AsyncMock

import pytest

from labhq.gateway.server import Hub, HALT_WHILE_WAITING
from labhq.settings import Settings


async def test_job_submission_cannot_erase_queued_halt(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.store.put("task", "t", {"runner_id": "r", "kind": "direct", "completed": False})
    entered, read = threading.Event(), threading.Event()
    transaction, get = hub.store._transaction, hub.store.get
    once = True

    @contextmanager
    def held_commit():
        nonlocal once
        with transaction() as conn:
            yield conn
            if once:
                once = False
                entered.set()
                assert read.wait(3)

    def observe_read(kind, key):
        value = get(kind, key)
        if kind == "task" and key == "t" and entered.is_set():
            read.set()
        return value

    monkeypatch.setattr(hub.store, "_transaction", held_commit)
    monkeypatch.setattr(hub.store, "get", observe_read)
    monkeypatch.setattr(hub, "controllable", lambda *args: True)
    monkeypatch.setattr(hub, "_step_alive", lambda *args: True)
    monkeypatch.setattr(hub, "_save_step", lambda *args: None)
    monkeypatch.setattr(hub, "_push", AsyncMock(return_value=False))
    monkeypatch.setattr(hub, "publish", AsyncMock())
    monkeypatch.setattr(hub, "send_runner", AsyncMock())
    halt = asyncio.create_task(hub.control_task("t", "halt"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await hub.on_runner_message("r", {"type": "job.submitted", "task_id": "t", "data": {}})
        with pytest.raises(ValueError, match=HALT_WHILE_WAITING):
            await halt
        latest = hub.store.get("task", "t")
        assert latest.get("jobs_submitted") is True
        assert not latest.get("halt_requested")
        hub._push.assert_not_called()
        # Only the job event was published; no successful agent.control notice.
        assert hub.publish.call_count == 1
        assert hub.publish.call_args.args[0]["type"] == "job.submitted"
    finally:
        read.set()
        await asyncio.gather(halt, return_exceptions=True)
        hub.store.close()
