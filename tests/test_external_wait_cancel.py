"""The lookup ends even when a progress update loses its cancellation (PR #800 Python 3.10 CI hang).

Python 3.10's `asyncio.wait_for` returns the inner result and drops an outer cancellation that lands in the same loop
step as the inner completion. `run_lookup` cancelled its progress task while that task was inside
`wait_for(notify(...))`; the cancellation was lost, the progress loop kept posting every few milliseconds and
`gather(watch)` waited forever (the 3.10 job timed out after 25 minutes). The fake `notify` below swallows its
cancellation once, which is what 3.10 did, so the regression is the same on every Python version. Asyncio only: no
process, network or file."""
import asyncio

from labhq.tools import external_wait as wait


async def test_a_progress_update_that_swallows_its_cancellation_still_lets_the_lookup_finish():
    in_flight = asyncio.Event()
    calls = []

    async def notify(data):
        calls.append(data["status"])
        if len(calls) == 1:
            return  # the first "waiting" record, before the progress task starts
        if data["status"] == "waiting" and not in_flight.is_set():
            in_flight.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return  # the lost cancellation: the update returns normally

    async def operation():
        await in_flight.wait()  # finish while a progress update is in flight
        return "done"

    result = await asyncio.wait_for(
        wait.run_lookup(operation, tool="fixture", workdir=None, notify=notify, timeout_s=5, progress_s=.001,
                        clock=lambda: 100.0),
        2)

    assert result == "done" and calls[-1] == "ok"
    leftover = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    assert leftover == []  # the progress task ended instead of looping on
