"""#1406: a request cancel left the runner inbox blocked, so later requests never saw their task results.

Trial evidence (gateway state rowids): task.result seq 11778 completed, then the cancel's
`_afinish_cancel` scanned every task with `asyncio.to_thread(self.store.all, "task")` on the shared
reader connection `store.db`. While that scan held its read transaction, seq 11779 was admitted and
`_inbox_has_earlier_blocker` read `active_runner_inbox()` from the same connection, saw the stale
snapshot with 11778 still `processing`, and stored 11779 as `blocked`. A blocked row blocks every
later row and nothing resumes it, so every following request stalled, also after a restart.

Both tests fail on main (strict xfail) and document the behaviour the fix must give.
"""
import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from labhq.gateway.server import Hub
from labhq.store import StateStore


@pytest.fixture
def hub(tmp_path, monkeypatch):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.event_lock = asyncio.Lock()
    hub.pending_committed = deque()
    hub.events = deque()
    hub.clients = set()
    hub.requests = {}
    hub.s = SimpleNamespace(gateway=SimpleNamespace(event_buffer=100))
    hub.reporter = SimpleNamespace(enabled=lambda: False)
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None)
    hub.runner_capabilities = {}
    hub.recruits_running = {}

    async def send(*args):
        pass

    monkeypatch.setattr(hub, "send_runner", send)
    yield hub
    hub.store.close()


def stale_reader(store):
    """What `asyncio.to_thread(store.all, "task")` holds while it iterates: an unfinished statement on
    `store.db`, so that connection keeps the read snapshot taken when the scan started."""
    cursor = store.db.execute("SELECT key, body FROM state WHERE kind='task'")
    assert cursor.fetchone() is not None
    return cursor


def setup_cancelled_request(store):
    # Two task rows so the scan's statement is still open after its first row.
    store.put("task", "task_plan", {"request_id": "req_old", "kind": "plan"})
    store.put("task", "task_closed", {"request_id": "req_old", "kind": "plan", "completed": True})
    store.put("task", "task_brief", {"request_id": "req_new", "kind": "briefing"})
    store.put("jobs_cancel", "task_closed", {"runner_id": "trial"})


@pytest.mark.xfail(strict=True, reason="#1406: inbox admission reads a stale snapshot on the shared reader connection")
async def test_completed_result_does_not_block_next_message_during_cancel_scan(hub):
    store = hub.store
    setup_cancelled_request(store)
    # seq 1: the cancelled plan task's result, claimed and being handled.
    result = {"type": "task.result", "runner_seq": 1, "task_id": "task_plan", "request_id": "req_old", "data": {}}
    store.accept_runner_inbox("trial", None, 1, result)
    store.set_runner_inbox_state("trial", None, 1, "processing", expected="pending")
    # fut.set_result() wakes the cancel, which starts its task scan before seq 1 is marked completed.
    scan = stale_reader(store)
    try:
        store.set_runner_inbox_state("trial", None, 1, "completed", expected="processing")
        # seq 2 arrives while the scan still runs (trial seq 11779).
        await hub.on_runner_message("trial", {"type": "task.jobs_cancelled", "runner_seq": 2, "task_id": "task_closed"})
    finally:
        scan.close()
    assert store.runner_inbox("trial", None, 2)["state"] == "completed"
    assert store.get("jobs_cancel", "task_closed") is None


@pytest.mark.xfail(strict=True, reason="#1406: a blocked row is never resumed after its blocker resolves")
async def test_new_request_result_is_not_held_behind_resolved_block(hub):
    store = hub.store
    setup_cancelled_request(store)
    result = {"type": "task.result", "runner_seq": 1, "task_id": "task_plan", "request_id": "req_old", "data": {}}
    store.accept_runner_inbox("trial", None, 1, result)
    store.set_runner_inbox_state("trial", None, 1, "processing", expected="pending")
    scan = stale_reader(store)
    try:
        store.set_runner_inbox_state("trial", None, 1, "completed", expected="processing")
        await hub.on_runner_message("trial", {"type": "task.jobs_cancelled", "runner_seq": 2, "task_id": "task_closed"})
    finally:
        scan.close()
    # The scan is over and nothing earlier is unresolved. The next request's messages (trial seq 11780+)
    # must reach the gateway; on main they are stored as blocked behind seq 2 forever.
    await hub.on_runner_message("trial", {"type": "task.accepted", "runner_seq": 3, "task_id": "task_brief",
                                          "request_id": "req_new"})
    states = {seq: store.runner_inbox("trial", None, seq)["state"] for seq in (2, 3)}
    assert states == {2: "completed", 3: "completed"}
