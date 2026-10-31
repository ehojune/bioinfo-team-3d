"""A cancel that reaches the runner before its task.dispatch must still stop that task (#1149)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from labhq.models import Task
from labhq.runner.daemon import Runner


def memory_runner():
    runner = Runner.__new__(Runner)  # no store, daemon, writer thread or host configuration is opened
    runner.tasks, runner._dispatches, runner._stopping = {}, set(), False
    runner.jobs = {}
    rows, sent = {}, []

    async def aput(kind, key, body):
        rows[(kind, key)] = body

    async def adelete(kind, key):
        rows.pop((kind, key), None)

    runner.store = SimpleNamespace(
        get=lambda kind, key: rows.get((kind, key)), aput=aput, adelete=adelete,
        all=lambda kind: {k[1]: v for k, v in rows.items() if k[0] == kind})

    async def send_batch(items):
        for obj, before, after in items:
            sent.append(obj)
            for row in (*before, *after):
                rows[row[:2]] = row[2]

    async def send_durably(obj, *, before=None, after=None):
        await send_batch([(obj, [before] if before else [], [after] if after else [])])

    runner._send_batch, runner._send_durably = send_batch, send_durably
    runner._run_guarded = AsyncMock()
    runner._cancel_local_jobs = AsyncMock(return_value=True)
    return runner, rows, sent


def dispatch(task_id="t1"):
    return {"type": "task.dispatch", "task": Task(id=task_id, agent_id="cso", request_id="r",
                                                  prompt="fake; never execute").model_dump(mode="json")}


async def test_cancel_before_dispatch_does_not_start_the_task():
    runner, rows, sent = memory_runner()
    await runner._on_message({"type": "task.cancel", "task_id": "t1"})
    await runner._on_message(dispatch())
    await asyncio.sleep(0)
    runner._run_guarded.assert_not_called()
    assert "t1" not in runner.tasks
    assert [m["type"] for m in sent] == ["task.accepted", "task.result"]
    data = sent[1]["data"]
    assert data["ok"] is False and data["error_kind"] == "cancelled"
    assert data["cost_usd"] == 0.0 and data["cost_known"] is True
    assert data["cleanup_evidence"]["status"] == "not_started"
    assert rows[("accepted_task", "t1")] == {"state": "finished"}
    assert ("cancelled_task", "t1") not in rows


async def test_a_resent_dispatch_after_the_cancelled_answer_is_not_started():
    runner, rows, sent = memory_runner()
    await runner._on_message({"type": "task.cancel", "task_id": "t1"})
    await runner._on_message(dispatch())
    await runner._on_message(dispatch())
    await asyncio.sleep(0)
    runner._run_guarded.assert_not_called()
    assert runner.tasks == {}


async def test_dispatch_then_cancel_keeps_the_old_behaviour():
    runner, rows, sent = memory_runner()
    await runner._on_message(dispatch())
    await asyncio.sleep(0)
    runner._run_guarded.assert_called_once()
    await runner._on_message({"type": "task.cancel", "task_id": "t1"})
    assert not any(k[0] == "cancelled_task" for k in rows)  # an accepted task needs no tombstone


async def test_cancel_for_another_task_does_not_block_a_dispatch():
    runner, rows, sent = memory_runner()
    await runner._on_message({"type": "task.cancel", "task_id": "other"})
    await runner._on_message(dispatch())
    await asyncio.sleep(0)
    runner._run_guarded.assert_called_once()


async def test_old_tombstones_are_dropped_on_the_next_cancel():
    runner, rows, sent = memory_runner()
    rows[("cancelled_task", "old")] = {"at": 1.0}
    await runner._on_message({"type": "task.cancel", "task_id": "t2"})
    assert ("cancelled_task", "old") not in rows and ("cancelled_task", "t2") in rows
