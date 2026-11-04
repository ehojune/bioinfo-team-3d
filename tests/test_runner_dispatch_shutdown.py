"""Shutdown/acceptance interleavings using only memory storage and coroutine barriers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from labhq.models import Task, TaskResult
from labhq.runner.daemon import Runner


def memory_runner(*, connected=False):
    runner = Runner.__new__(Runner)  # No store, broker, daemon, writer thread or host configuration is opened.
    runner.tasks, runner._dispatches = {}, set()
    runner._stopping, runner.started_at, runner.s = False, 0, None
    runner.connected = asyncio.Event()
    if connected:
        runner.connected.set()
    runner.broker = SimpleNamespace(stop=Mock())
    ledger, outbox = {}, []
    runner.store = SimpleNamespace(get=lambda kind, tid: ledger.get((kind, tid)), pending=lambda: list(outbox))
    runner._run_guarded = AsyncMock(side_effect=AssertionError("shutdown must not launch staff work"))
    return runner, ledger, outbox


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("count", [1, 2])
async def test_shutdown_waits_for_every_accepted_handler_and_result_commit(connected, count):
    runner, ledger, outbox = memory_runner(connected=connected)
    accepted = [asyncio.Event() for _ in range(count)]
    resume_accept = [asyncio.Event() for _ in range(count)]
    writing_result = [asyncio.Event() for _ in range(count)]
    commit_result = [asyncio.Event() for _ in range(count)]

    async def send(message, *, before=None, after=None):
        index = int(message["task_id"])
        if before:
            ledger[before[:2]] = before[2]
        if message["type"] == "task.accepted":
            outbox.append(message)
            accepted[index].set()  # The durable acceptance completed, but its coroutine has not returned.
            await resume_accept[index].wait()
        else:
            writing_result[index].set()
            await commit_result[index].wait()
            outbox.append(message)
            ledger[after[:2]] = after[2]

    runner._send_durably = send
    handlers = [asyncio.create_task(runner._on_message({"type": "task.dispatch", "task": Task(
        id=str(i), agent_id="cso", request_id="r", prompt="fake; never execute").model_dump(mode="json")}))
        for i in range(count)]
    stopping = None
    try:
        await asyncio.gather(*(event.wait() for event in accepted))
        stopping = asyncio.create_task(runner.shutdown(timeout_s=1))
        for _ in range(8):
            await asyncio.sleep(0)  # Let shutdown's wait_for/finish callbacks run without a wall-clock race.
        assert not stopping.done(), "shutdown overlooked the durable-accepted handlers"
        for i in range(count):
            resume_accept[i].set()
            await asyncio.wait_for(writing_result[i].wait(), 1)
            for _ in range(8):
                await asyncio.sleep(0)
            assert not stopping.done(), "shutdown returned before the result commit"
            commit_result[i].set()
            await asyncio.wait_for(handlers[i], 1)
        results = [row for row in outbox if row["type"] == "task.result"]
        assert len(results) == count
        for row in results:
            result = TaskResult.model_validate(row["data"])
            assert not result.ok and result.shutdown_evidence["cancelled"] is True
            assert ledger[("accepted_task", row["task_id"])]["state"] == "finished"
        if connected:
            for _ in range(8):
                await asyncio.sleep(0)
            assert not stopping.done(), "committed results must still wait for ACK"
            outbox[:] = [row for row in outbox if row["type"] != "task.result"]  # Fake gateway ACK.
        await asyncio.wait_for(stopping, 1)
        runner._run_guarded.assert_not_called()
        assert not runner.tasks and not runner._dispatches
    finally:
        for event in [*resume_accept, *commit_result]:
            event.set()
        await asyncio.gather(*handlers, *([stopping] if stopping else []), return_exceptions=True)


async def test_shutdown_timeout_does_not_cancel_the_handler_completion():
    runner, ledger, outbox = memory_runner()
    accepted, release = asyncio.Event(), asyncio.Event()

    async def send(message, *, before=None, after=None):
        if before:
            ledger[before[:2]] = before[2]
        outbox.append(message)
        if message["type"] == "task.accepted":
            accepted.set()
            await release.wait()
        if after:
            ledger[after[:2]] = after[2]

    runner._send_durably = send
    handler = asyncio.create_task(runner._on_message({"type": "task.dispatch", "task": Task(
        id="timeout", agent_id="cso", prompt="fake").model_dump(mode="json")}))
    try:
        await accepted.wait()
        await asyncio.wait_for(runner.shutdown(timeout_s=.01), 1)
        assert not handler.done()
        assert runner._dispatches and all(not done.cancelled() for done in runner._dispatches)
    finally:
        release.set()
        await handler
    assert ledger[("accepted_task", "timeout")]["state"] == "finished"
    assert len([row for row in outbox if row["type"] == "task.result"]) == 1
    assert not runner._dispatches


async def test_dispatch_failure_releases_shutdown_barrier():
    runner, _, _ = memory_runner()
    runner._send_durably = AsyncMock(side_effect=OSError("fake write failed"))
    with pytest.raises(OSError, match="fake write failed"):
        await runner._on_message({"type": "task.dispatch", "task": Task(
            id="failed", agent_id="cso", prompt="fake").model_dump(mode="json")})
    assert not runner._dispatches
    await asyncio.wait_for(runner.shutdown(timeout_s=.1), 1)
    runner._run_guarded.assert_not_called()


async def test_dispatch_entering_during_existing_task_cleanup_is_also_drained():
    runner, ledger, outbox = memory_runner()
    working, cleaning, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    accepted, release = asyncio.Event(), asyncio.Event()

    async def old_task():
        working.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await cleaned.wait()

    async def send(message, *, before=None, after=None):
        if before:
            ledger[before[:2]] = before[2]
        outbox.append(message)
        if message["type"] == "task.accepted":
            accepted.set()
            await release.wait()
        if after:
            ledger[after[:2]] = after[2]

    runner._send_durably = send
    runner.tasks["old"] = asyncio.create_task(old_task())
    await working.wait()
    stopping = asyncio.create_task(runner.shutdown(timeout_s=1))
    await cleaning.wait()  # Shutdown has already passed its initial dispatch snapshot.
    handler = asyncio.create_task(runner._on_message({"type": "task.dispatch", "task": Task(
        id="late", agent_id="cso", prompt="fake").model_dump(mode="json")}))
    try:
        await accepted.wait()
        cleaned.set()
        for _ in range(8):
            await asyncio.sleep(0)
        assert not stopping.done(), "dispatches must be rechecked after existing tasks drain"
        release.set()
        await handler
        await stopping
        assert ledger[("accepted_task", "late")]["state"] == "finished"
        assert len([row for row in outbox if row["type"] == "task.result"]) == 1
        runner._run_guarded.assert_not_called()
    finally:
        cleaned.set()
        release.set()
        await asyncio.gather(handler, stopping, runner.tasks["old"], return_exceptions=True)
