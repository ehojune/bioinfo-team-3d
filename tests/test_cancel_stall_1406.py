"""#1406: a request cancel left the runner inbox blocked, so later requests never saw their task results.

Trial evidence (gateway state rowids): task.result seq 11778 completed, then the cancel's
`_afinish_cancel` scanned every task with `asyncio.to_thread(self.store.all, "task")` on the shared
reader connection `store.db`. While that scan held its read transaction, seq 11779 was admitted and
`_inbox_has_earlier_blocker` read `active_runner_inbox()` from the same connection, saw the stale
snapshot with 11778 still `processing`, and stored 11779 as `blocked`. A blocked row blocks every
later row and nothing resumes it, so every following request stalled, also after a restart.

The first two tests failed on main with the rows left `blocked`.
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
    hub.runner_incarnations = {}

    async def send(*args):
        pass

    monkeypatch.setattr(hub, "send_runner", send)
    # task.accepted for a stored task refreshes the session view; this hub has no web session.
    monkeypatch.setattr(hub, "_session_state_changed", lambda: None)
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
    # must reach the gateway; on main they were stored as blocked behind seq 2 forever.
    await hub.on_runner_message("trial", {"type": "task.accepted", "runner_seq": 3, "task_id": "task_brief",
                                          "request_id": "req_new"})
    states = {seq: store.runner_inbox("trial", None, seq)["state"] for seq in (2, 3)}
    assert states == {2: "completed", 3: "completed"}


def held_store(store):
    """The trial state after the stall: seq 1 completed, seq 2 and 3 held behind a stale view of seq 1."""
    setup_cancelled_request(store)
    rows = [{"type": "task.result", "runner_seq": 1, "task_id": "task_plan", "request_id": "req_old", "data": {}},
            {"type": "task.jobs_cancelled", "runner_seq": 2, "task_id": "task_closed"},
            {"type": "task.accepted", "runner_seq": 3, "task_id": "task_brief", "request_id": "req_new"}]
    for row in rows:
        store.accept_runner_inbox("trial", None, row["runner_seq"], row)
    store.set_runner_inbox_state("trial", None, 1, "processing", expected="pending")
    store.set_runner_inbox_state("trial", None, 1, "completed", expected="processing")
    for seq in (2, 3):
        store.set_runner_inbox_state("trial", None, seq, "blocked", "earlier outcome unresolved", expected="pending")


async def test_restart_resumes_rows_held_behind_a_completed_row(hub):
    store = hub.store
    held_store(store)
    hub.ask_tasks = {}
    await hub.recover_runner_publications()
    assert {seq: store.runner_inbox("trial", None, seq)["state"] for seq in (2, 3)} == {2: "completed", 3: "completed"}
    assert store.get("jobs_cancel", "task_closed") is None


async def test_row_held_behind_unknown_ask_stays_held(hub):
    """#1395's guard stays: an ask whose start is unknown is not re-run, and what came after it waits."""
    store = hub.store
    setup_cancelled_request(store)
    ask = {"type": "ask.requested", "runner_seq": 1, "task_id": "task_plan", "request_id": "req_old",
           "data": {"id": "a1", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    store.accept_runner_inbox("trial", None, 1, ask)
    store.set_runner_inbox_state("trial", None, 1, "processing", expected="pending")
    store.set_runner_inbox_state("trial", None, 1, "failed", "ask_start_unknown", expected="processing")
    later = {"type": "task.jobs_cancelled", "runner_seq": 2, "task_id": "task_closed"}
    store.accept_runner_inbox("trial", None, 2, later)
    store.set_runner_inbox_state("trial", None, 2, "blocked", "earlier outcome unresolved", expected="pending")
    hub.ask_tasks = {}
    await hub.recover_runner_publications()
    await hub.on_runner_message("trial", {"type": "task.accepted", "runner_seq": 3, "task_id": "task_brief",
                                          "request_id": "req_new"})
    states = {seq: store.runner_inbox("trial", None, seq)["state"] for seq in (1, 2, 3)}
    assert states == {1: "failed", 2: "blocked", 3: "blocked"}


async def test_thread_scan_does_not_share_the_loop_connection(hub):
    """`_afinish_cancel` scans tasks with `asyncio.to_thread(store.all, "task")`; that scan must not hold
    the loop's reader snapshot."""
    store = hub.store
    setup_cancelled_request(store)
    seen = await asyncio.to_thread(lambda: (store._reader() is store.db, store.all("task")))
    assert seen[0] is False and set(seen[1]) == {"task_plan", "task_closed", "task_brief"}
    assert store._reader() is store.db
