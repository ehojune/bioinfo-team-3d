"""Fake approval clocks/notifications and coroutine gates; no service or process calls."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from labhq.tools import external_wait as wait


class Decision:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def __call__(self):
        self.entered.set()
        await self.release.wait()
        return "declined"


async def turns():
    for _ in range(8):
        await asyncio.sleep(0)


async def seed(root, now):
    await wait.run_lookup(AsyncMock(return_value=True), tool="key_fulltext", workdir=root,
                          clock=lambda: now[0], notify=AsyncMock())


async def test_overlapping_approvals_do_not_future_date_budget_or_break_later_lookup(tmp_path):
    now = [100.0]
    await seed(tmp_path, now)
    now[0] = 110.0  # Ten seconds already spent; neither decision may reset them.
    first, second = Decision(), Decision()
    one = asyncio.create_task(wait.exclude_human_wait(first, workdir=tmp_path, clock=lambda: now[0]))
    await first.entered.wait()
    now[0] = 120.0
    two = asyncio.create_task(wait.exclude_human_wait(second, workdir=tmp_path, clock=lambda: now[0]))
    try:
        await turns()
        now[0] = 160.0
        first.release.set()
        await one
        await second.entered.wait()
        now[0] = 200.0
        second.release.set()
        await two
        notifications = []
        assert await wait.run_lookup(AsyncMock(return_value="later"), tool="gtex", workdir=tmp_path,
            timeout_s=20, clock=lambda: now[0], notify=AsyncMock(side_effect=notifications.append)) == "later"
        state = json.loads((tmp_path / wait.BUDGET_FILE).read_text(encoding="utf-8"))
        assert state["started_at"] <= now[0] and now[0] - state["started_at"] == 10
        assert notifications[-1]["elapsed_s"] == 10
        now[0] += 11
        operation = AsyncMock()
        with pytest.raises(wait.LookupTimeout):
            await wait.run_lookup(operation, tool="gtex", workdir=tmp_path, timeout_s=20, clock=lambda: now[0])
        operation.assert_not_called()
    finally:
        first.release.set()
        second.release.set()
        await asyncio.gather(one, two, return_exceptions=True)


@pytest.mark.parametrize("approval_first", [True, False])
async def test_approval_and_automatic_lookup_never_share_active_budget(tmp_path, approval_first):
    now = [100.0]
    await seed(tmp_path, now)
    decision, automatic_entered, automatic_cancelled = Decision(), asyncio.Event(), asyncio.Event()
    notifications = []

    async def automatic():
        automatic_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            automatic_cancelled.set()

    def start_lookup():
        return asyncio.create_task(wait.run_lookup(automatic, tool="gtex", workdir=tmp_path,
            timeout_s=.05, progress_s=60, clock=lambda: now[0], notify=AsyncMock(side_effect=notifications.append)))

    def start_approval():
        return asyncio.create_task(wait.exclude_human_wait(decision, workdir=tmp_path, clock=lambda: now[0]))

    if approval_first:
        human = start_approval()
        await decision.entered.wait()
        lookup = start_lookup()
    else:
        lookup = start_lookup()
        await automatic_entered.wait()
        human = start_approval()
    try:
        await turns()
        if approval_first:
            assert not automatic_entered.is_set(), "approval must not hide an automatic lookup's active time"
            now[0] += 7200
            decision.release.set()
            await human
        else:
            assert not decision.entered.is_set() or automatic_cancelled.is_set(), "a running automatic lookup keeps its own deadline"
        with pytest.raises(wait.LookupTimeout):
            await lookup
        assert automatic_cancelled.is_set() and notifications[-1]["status"] == "timeout"
        if not approval_first:
            await decision.entered.wait()
            now[0] += 7200
            decision.release.set()
            await human
    finally:
        decision.release.set()
        if not lookup.done():
            lookup.cancel()
        await asyncio.gather(human, lookup, return_exceptions=True)


@pytest.mark.parametrize("queued_kind", ["approval", "lookup"])
async def test_queued_and_active_cancellation_release_budget_slot(tmp_path, queued_kind):
    now = [100.0]
    await seed(tmp_path, now)
    now[0] += 10
    decision, queued_operation = Decision(), AsyncMock()
    human = asyncio.create_task(wait.exclude_human_wait(decision, workdir=tmp_path, clock=lambda: now[0]))
    await decision.entered.wait()
    pending = (wait.exclude_human_wait(queued_operation, workdir=tmp_path, clock=lambda: now[0])
               if queued_kind == "approval" else wait.run_lookup(
                   queued_operation, tool="gtex", workdir=tmp_path, clock=lambda: now[0]))
    queued = asyncio.create_task(pending)
    try:
        await turns()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        queued_operation.assert_not_called()
        now[0] += 7200
        human.cancel()
        with pytest.raises(asyncio.CancelledError):
            await human
        assert await wait.run_lookup(AsyncMock(return_value=True), tool="later", workdir=tmp_path,
                                     timeout_s=20, clock=lambda: now[0])
        state = json.loads((tmp_path / wait.BUDGET_FILE).read_text(encoding="utf-8"))
        assert now[0] - state["started_at"] == 10
    finally:
        decision.release.set()
        human.cancel()
        await asyncio.gather(human, queued, return_exceptions=True)


async def test_separate_step_budget_runs_while_another_step_waits(tmp_path):
    first, other = tmp_path / "first", tmp_path / "other"
    first.mkdir()
    other.mkdir()
    now = [100.0]
    await seed(first, now)
    await seed(other, now)
    decision = Decision()
    human = asyncio.create_task(wait.exclude_human_wait(decision, workdir=first, clock=lambda: now[0]))
    await decision.entered.wait()
    try:
        assert await asyncio.wait_for(wait.run_lookup(AsyncMock(return_value=True), tool="gtex", workdir=other,
                                      clock=lambda: now[0]), 1)
        now[0] += wait.TIMEOUT_S + 1
        operation = AsyncMock()
        with pytest.raises(wait.LookupTimeout):
            await wait.run_lookup(operation, tool="gtex", workdir=other, clock=lambda: now[0])
        operation.assert_not_called()
        decision.release.set()
        await human
        assert await wait.run_lookup(AsyncMock(return_value=True), tool="gtex", workdir=first,
                                     clock=lambda: now[0])
    finally:
        decision.release.set()
        await human
