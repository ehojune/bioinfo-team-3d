"""Minimal mock reproduction for the remaining approval commit boundary (#606)."""
import asyncio
from collections import deque
from contextlib import contextmanager
from types import SimpleNamespace
import time
import threading

import pytest
import anyio

from labhq import durable
from labhq.gateway.server import Hub
from labhq.models import ApprovalRequest
from labhq.store import StateStore
from labhq.settings import Settings


@pytest.mark.parametrize("active_lane", [False, True])
@pytest.mark.parametrize("operation", ["create", "resolve"])
async def test_approval_commit_allows_heartbeat(tmp_path, monkeypatch, active_lane, operation):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.approvals = {}
    hub.requests = {}
    hub.runners = {}
    hub.s = SimpleNamespace(policy=SimpleNamespace(approvals=SimpleNamespace(
        timeout_s=60, pi_decision_timeout_s=60)))
    monkeypatch.setattr(hub, "_cancel_facilities_timeout", lambda aid: None)
    monkeypatch.setattr(hub, "_cancel_resume_timer", lambda aid: None)
    published = asyncio.Event()

    async def publish(event):
        published.set()

    monkeypatch.setattr(hub, "publish", publish)
    if operation == "resolve":
        card = ApprovalRequest(kind="tool_permission", summary="mock")
        hub.approvals[card.id] = {"approval": card.model_dump(mode="json"),
                                  "origin": None, "future": asyncio.get_running_loop().create_future()}
        hub.save_approval(card.id)
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    transaction = hub.store._transaction
    timeline = []

    @contextmanager
    def delayed_commit():
        with transaction() as conn:
            yield conn
            if "commit" not in timeline:
                time.sleep(0.2)
                timeline.append("commit")

    monkeypatch.setattr(hub.store, "_transaction", delayed_commit)

    async def heartbeat():
        await asyncio.sleep(0.02)
        timeline.append("heartbeat")

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)  # arm the timer before entering the approval operation
    task = asyncio.create_task(hub.request_approval("tool_permission", "mock") if operation == "create"
                               else hub.resolve_approval(card.id, True))
    try:
        await published.wait()
        await beat
        assert timeline[:2] == ["heartbeat", "commit"], timeline
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, beat, return_exceptions=True)
        hub.store.close()


@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_failed_approval_commit_keeps_memory_card(tmp_path, monkeypatch, operation):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.requests, hub.runners = {}, {}
    card = ApprovalRequest(kind="tool_permission", summary="mock")
    future = asyncio.get_running_loop().create_future()
    hub.approvals = {card.id: {"approval": card.model_dump(mode="json"),
                               "origin": None, "future": future}}
    hub.save_approval(card.id)
    monkeypatch.setattr(hub, "_cancel_facilities_timeout", lambda aid: None)
    monkeypatch.setattr(hub, "_cancel_resume_timer", lambda aid: None)
    transaction = hub.store._transaction

    @contextmanager
    def failed_commit():
        with transaction() as conn:
            yield conn
            raise OSError("mock commit failure")

    monkeypatch.setattr(hub.store, "_transaction", failed_commit)
    try:
        with pytest.raises(OSError, match="mock commit failure"):
            if operation == "resolve":
                await hub.resolve_approval(card.id, True)
            else:
                await hub.close_approval(card.id, "mock close")
        reopened = StateStore(hub.store.path)
        try:
            assert reopened.get("approval", card.id) is not None
            assert reopened.get("approval_decision", card.id) is None
            assert not future.done()
            assert card.id in hub.approvals, "failed commit removed the live card before persistence"
        finally:
            reopened.close()
    finally:
        hub.store.close()


@pytest.fixture
def live_hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    monkeypatch.setattr(hub.pi_interpreter, "schedule", lambda event: None)
    yield hub
    hub.store.close()


def hold_commit(store, monkeypatch, *, fail=False):
    entered, release = threading.Event(), threading.Event()
    original = store._transaction
    first = True

    @contextmanager
    def held():
        nonlocal first
        with original() as conn:
            yield conn
            if first:
                first = False
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("commit barrier was not released")
                if fail:
                    raise OSError("barrier commit failure")
    monkeypatch.setattr(store, "_transaction", held)
    return entered, release


async def wait_barrier(entered):
    async def wait():
        while not entered.is_set():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 5)


def pending_card(hub, origin=None):
    card = ApprovalRequest(kind="tool_permission", summary="mock")
    future = asyncio.get_running_loop().create_future()
    hub.approvals[card.id] = {"approval": card.model_dump(mode="json"), "origin": origin, "future": future}
    hub.save_approval(card.id)
    return card, future


@pytest.mark.parametrize("operation", ["resolve", "close", "timeout"])
@pytest.mark.parametrize("fail", [False, True])
async def test_repeated_cancel_settles_commit_before_memory_and_future(live_hub, monkeypatch, operation, fail):
    hub = live_hub
    card, future = pending_card(hub)
    entered, release = hold_commit(hub.store, monkeypatch, fail=fail)
    action = {"resolve": lambda: hub.resolve_approval(card.id, True),
              "close": lambda: hub.close_approval(card.id, "closed"),
              "timeout": lambda: hub._timeout_approval(card.id)}[operation]
    task = asyncio.create_task(action())
    try:
        await wait_barrier(entered)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert card.id in hub.approvals and not future.done()
        assert hub.store.get("approval", card.id) is not None
        assert hub.store.get("approval_decision", card.id) is None
    finally:
        release.set()
    with pytest.raises(OSError if fail else asyncio.CancelledError):
        await task
    reopened = StateStore(hub.store.path)
    try:
        assert bool(reopened.get("approval", card.id)) is fail
        assert bool(reopened.get("approval_decision", card.id)) is not fail
        assert (card.id in hub.approvals) is fail
        assert future.done() is not fail
        if not fail:
            expected = None if operation == "resolve" else ("expired" if operation == "close" else "timed_out")
            assert future.result().get("state") == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("second", ["resolve", "close", "timeout"])
@pytest.mark.parametrize("first", ["resolve", "close", "timeout"])
async def test_card_races_do_not_replace_the_committed_answer(live_hub, monkeypatch, first, second):
    hub = live_hub
    card, future = pending_card(hub)
    entered, release = hold_commit(hub.store, monkeypatch)
    actions = {"resolve": lambda: hub.resolve_approval(card.id, True, "first"),
               "close": lambda: hub.close_approval(card.id, "first"),
               "timeout": lambda: hub._timeout_approval(card.id)}
    winner = asyncio.create_task(actions[first]())
    loser = None
    try:
        await wait_barrier(entered)
        operation = {"resolve": lambda: hub.resolve_approval(card.id, False),
                     "close": lambda: hub.close_approval(card.id, "second"),
                     "timeout": lambda: hub._timeout_approval(card.id)}[second]
        loser = asyncio.create_task(operation())
        await asyncio.sleep(0.01)
        assert not loser.done() and not future.done()
    finally:
        release.set()
    await winner
    if second == "resolve":
        with pytest.raises(KeyError):
            await loser
    elif second == "close":
        assert await loser is False
    else:
        assert (await loser)["approved"] is (first == "resolve")
    decision = hub.store.get("approval_decision", card.id)
    assert decision["approved"] is (first == "resolve")
    assert decision["note"] == ("timed out" if first == "timeout" else "first")
    assert future.result()["approved"] is (first == "resolve")
    assert future.result().get("state") == {"resolve": None, "close": "expired", "timeout": "timed_out"}[first]


async def test_runner_delivery_reconnect_and_ack_wait_for_commit(live_hub, monkeypatch):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    entered, release = hold_commit(hub.store, monkeypatch)
    sent = []

    async def send(runner_id, msg):
        sent.append((runner_id, msg))
    monkeypatch.setattr(hub, "send_runner", send)
    resolving = asyncio.create_task(hub.resolve_approval(card.id, True))
    acknowledging = None
    try:
        await wait_barrier(entered)
        await hub.flush_decisions("runner")
        assert not sent
        acknowledging = asyncio.create_task(hub.on_runner_message("runner", {"type": "approval.ack", "id": card.id}))
        await asyncio.sleep(0.01)
        assert not acknowledging.done()
    finally:
        release.set()
    await resolving
    await acknowledging
    assert hub.store.get("decision", card.id) is None
    assert hub.store.get("approval_decision", card.id)["approved"] is True

    # A disconnected runner replays the committed delivery after DB reopen.
    card, _ = pending_card(hub, "runner")
    await hub.resolve_approval(card.id, False, "declined")
    hub.store.close()
    hub.store = StateStore(hub.store.path)
    await hub.flush_decisions("runner")
    assert sent[-1] == ("runner", {"type": "approval.resolved", "id": card.id,
                                   "approved": False, "note": "declined"})
    await hub.on_runner_message("other", {"type": "approval.ack", "id": card.id})
    assert hub.store.get("decision", card.id) is not None
    await hub.on_runner_message("runner", {"type": "approval.ack", "id": card.id})
    assert hub.store.get("decision", card.id) is None


@pytest.mark.parametrize("fail", [False, True])
async def test_transaction_snapshots_and_rolls_back_all_three_rows(live_hub, monkeypatch, fail):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    entered, release = hold_commit(hub.store, monkeypatch, fail=fail)
    decision = {"approved": True, "nested": {"value": "before"}}
    delivery = {"origin": "runner", "approved": True}
    task = asyncio.create_task(hub.store.acommit_approval(card.id, decision, delivery))
    try:
        await wait_barrier(entered)
        decision["nested"]["value"] = "after"
        delivery["approved"] = False
    finally:
        release.set()
    if fail:
        with pytest.raises(OSError):
            await task
        assert hub.store.get("approval", card.id) is not None
        assert hub.store.get("approval_decision", card.id) is None
        assert hub.store.get("decision", card.id) is None
        await hub.store.acommit_approval(card.id, decision, delivery)
    else:
        await task
    assert hub.store.get("approval", card.id) is None
    assert hub.store.get("approval_decision", card.id)["nested"]["value"] == ("after" if fail else "before")
    assert hub.store.get("decision", card.id)["approved"] is not fail


@pytest.mark.parametrize("fail", [False, True])
async def test_cancel_during_creation_leaves_no_unobserved_live_card(live_hub, monkeypatch, fail):
    hub = live_hub
    entered, release = hold_commit(hub.store, monkeypatch, fail=fail)
    task = asyncio.create_task(hub.request_approval("tool_permission", "mock"))
    try:
        await wait_barrier(entered)
        assert not hub.approvals
        assert not hub.store.all("approval")
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(OSError if fail else asyncio.CancelledError):
        await task
    assert not hub.approvals and not hub.store.all("approval")
    decisions = hub.store.all("approval_decision")
    assert len(decisions) == (0 if fail else 1)
    if not fail:
        assert next(iter(decisions.values()))["state"] == "expired"


async def test_runner_replay_cannot_reopen_answered_card_and_timeout_marks_cursor(live_hub, monkeypatch):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    sent = []
    async def send(runner_id, msg):
        sent.append(msg)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.resolve_approval(card.id, True)
    await hub.on_runner_message("runner", {"type": "approval.requested", "data": card.model_dump(mode="json"),
                                           "runner_seq": 1})
    assert card.id not in hub.approvals and hub.store.get("approval", card.id) is None
    assert hub.store.get("approval_decision", card.id)["approved"] is True
    assert hub.store.runner_seen("runner") == 1
    await hub.on_runner_message("runner", {"type": "approval.timed_out", "data": {"id": card.id}, "runner_seq": 2})
    assert hub.store.runner_seen("runner") == 2
    assert hub.store.get("decision", card.id) is None
    assert hub.store.get("approval_decision", card.id)["state"] == "timed_out"
    assert sent[-1] == {"type": "runner.ack", "runner_seq": 2}


async def test_timeout_timer_finishes_after_child_commit(live_hub):
    hub = live_hub
    card, _ = pending_card(hub)
    finished = []
    async def timer():
        hub.facilities_timeout_tasks[card.id] = asyncio.current_task()
        await hub._timeout_approval(card.id)
        finished.append(True)
    await asyncio.create_task(timer())
    assert finished == [True]
    assert card.id not in hub.facilities_timeout_tasks


@pytest.mark.parametrize("fail", [False, True])
async def test_level_cancellation_settles_the_started_decision(live_hub, monkeypatch, fail):
    hub = live_hub
    card, future = pending_card(hub)
    entered, release = hold_commit(hub.store, monkeypatch, fail=fail)
    scopes = []
    async def resolve():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await hub.resolve_approval(card.id, True)
    task = asyncio.create_task(resolve())
    try:
        await wait_barrier(entered)
        scopes[0].cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not future.done()
    finally:
        release.set()
    if fail:
        with pytest.raises(OSError):
            await task
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (card.id in hub.approvals) is fail
    assert future.done() is not fail


@pytest.mark.parametrize("fail", [False, True])
async def test_ack_failure_or_cancel_preserves_known_delivery_state(live_hub, monkeypatch, fail):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    await hub.resolve_approval(card.id, True)
    entered, release = hold_commit(hub.store, monkeypatch, fail=fail)
    task = asyncio.create_task(hub.on_runner_message("runner", {"type": "approval.ack", "id": card.id}))
    try:
        await wait_barrier(entered)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert hub.store.get("decision", card.id) is not None
    finally:
        release.set()
    with pytest.raises(OSError if fail else asyncio.CancelledError):
        await task
    assert bool(hub.store.get("decision", card.id)) is fail
    assert hub.store.get("approval_decision", card.id)["approved"] is True


def test_sync_approval_transaction_remains_available(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        store.put("approval", "a", {"approval": {"id": "a"}})
        store.commit_approval("a", {"approved": True}, {"origin": "runner", "approved": True})
        assert store.get("approval", "a") is None
        assert store.get("approval_decision", "a") == {"approved": True}
        assert store.get("decision", "a")["origin"] == "runner"
    finally:
        store.close()


async def test_reconnect_flush_does_not_send_a_decision_superseded_while_waiting(live_hub, monkeypatch):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    await hub.resolve_approval(card.id, True)
    entered, release = hold_commit(hub.store, monkeypatch)
    sent = []
    async def send(runner_id, msg):
        sent.append(msg)
    monkeypatch.setattr(hub, "send_runner", send)
    timeout = asyncio.create_task(hub._timeout_approval(card.id, runner_id="runner"))
    flush = None
    try:
        await wait_barrier(entered)
        flush = asyncio.create_task(hub.flush_decisions("runner"))
        await asyncio.sleep(0.01)
        assert not flush.done() and not sent
    finally:
        release.set()
    await timeout
    await flush
    assert not sent
    assert hub.store.get("decision", card.id) is None


@pytest.mark.parametrize("publish_barrier", [False, True])
@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_backpressured_runner_delivery_has_a_bound_even_after_cancel(live_hub, monkeypatch, operation, publish_barrier):
    hub = live_hub
    card, _ = pending_card(hub, "runner")
    entered, release, send_timed_out = asyncio.Event(), asyncio.Event(), asyncio.Event()
    publishing, publish_release = asyncio.Event(), asyncio.Event()
    timeline = []
    started = time.monotonic()
    def mark(stage):
        timeline.append((stage, round(time.monotonic() - started, 3)))
    writer_times = {name: None for name in ("enqueue", "start", "commit_start", "commit_finish", "finish")}
    writer = durable.lane(hub.store.lane)
    submit = writer.submit
    transaction = hub.store._transaction
    writer_context = threading.local()

    @contextmanager
    def observed_transaction():
        with transaction() as conn:
            yield conn
            if getattr(writer_context, "publish", False):
                writer_times["commit_start"] = round(time.monotonic() - started, 3)
        if getattr(writer_context, "publish", False):
            writer_times["commit_finish"] = round(time.monotonic() - started, 3)

    def observed_submit(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") != "_append_event_now":
            return submit(fn, *args, **kwargs)
        writer_times["enqueue"] = round(time.monotonic() - started, 3)
        def observed_job():
            writer_times["start"] = round(time.monotonic() - started, 3)
            writer_context.publish = True
            try:
                return fn(*args, **kwargs)
            finally:
                writer_context.publish = False
                writer_times["finish"] = round(time.monotonic() - started, 3)
        return submit(observed_job)
    monkeypatch.setattr(writer, "submit", observed_submit)
    monkeypatch.setattr(hub.store, "_transaction", observed_transaction)
    original_publish = hub.publish
    async def publish(event, **kwargs):
        mark("publish_enter")
        publishing.set()
        if publish_barrier:
            await publish_release.wait()
        await original_publish(event, **kwargs)
        mark("publish_done")
    monkeypatch.setattr(hub, "publish", publish)
    class Socket:
        async def send_text(self, payload):
            mark("send_enter")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                mark("send_cancelled")
                send_timed_out.set()  # the send ended because its timer fired, not because the test released it
                raise
    hub.runners["runner"] = Socket()
    hub.runner_locks["runner"] = asyncio.Lock()
    send_bound = 0.25  # larger than a loaded runner's scheduling jitter, small against the wait below
    monkeypatch.setattr("labhq.gateway.server.CLIENT_SEND_TIMEOUT_S", send_bound)
    action = hub.resolve_approval(card.id, True) if operation == "resolve" else hub.close_approval(card.id, "closed")
    task = asyncio.create_task(action)
    def diagnostics():
        loop = asyncio.get_running_loop()
        action_error = repr(task.exception()) if task.done() and not task.cancelled() else None
        stacks = {repr(t): [f"{f.f_code.co_filename}:{f.f_lineno}:{f.f_code.co_name}" for f in t.get_stack()]
                  for t in asyncio.all_tasks() if not t.done()}
        timers = [(round(h.when() - loop.time(), 3), repr(h)) for h in loop._scheduled if not h.cancelled()]
        return (f"timeline={timeline!r}, writer_times={writer_times!r} (None=unobserved), now={time.monotonic()-started:.3f}, "
                f"action_done={task.done()}, action_cancelled={task.cancelled()}, action_error={action_error}, "
                f"runner_present={'runner' in hub.runners}, runner_lock={hub.runner_locks.get('runner')!r}, "
                f"card_lock={hub._approval_lock(card.id)!r}, publish_entered={publishing.is_set()}, "
                f"publish_released={publish_release.is_set()}, timers={timers!r}, stacks={stacks!r}")
    try:
        try:
            await asyncio.wait_for(entered.wait(), 10)
        except asyncio.TimeoutError:
            pytest.fail("send not reached: " + diagnostics())
        assert hub.store.get("approval", card.id) is None
        assert hub.store.get("approval_decision", card.id) is not None
        assert hub.store.get("decision", card.id) is not None
        for _ in range(3):
            mark("outer_cancel")
            task.cancel()
            await asyncio.sleep(0)
        try:
            await asyncio.wait_for(publishing.wait(), 4 * send_bound)
        except asyncio.TimeoutError:
            pytest.fail("send timeout or unregister did not reach publish within its bound: " + diagnostics())
        assert send_timed_out.is_set(), diagnostics()
        assert "runner" not in hub.runners, diagnostics()
        if publish_barrier:
            assert not task.done(), diagnostics()
            mark("publish_release")
            publish_release.set()
        done, _ = await asyncio.wait({task}, timeout=10)  # durable publish has a separate completion bound
        assert task in done, "durable publish did not complete: " + diagnostics()
        assert any(stage == "publish_done" for stage, _ in timeline), diagnostics()
        assert all(value is not None for value in writer_times.values()), diagnostics()
        assert list(writer_times.values()) == sorted(writer_times.values()), diagnostics()
        assert send_timed_out.is_set(), diagnostics()
        assert "runner" not in hub.runners, diagnostics()
    finally:
        release.set()
        publish_release.set()
        done, _ = await asyncio.wait({task}, timeout=1)
        if not done:
            task.cancel()
            pytest.fail("cleanup did not finish: " + diagnostics())
        result = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError)
    assert hub.store.get("decision", card.id) is not None
    await hub.on_runner_message("runner", {"type": "approval.ack", "id": card.id})
    assert hub.store.get("decision", card.id) is None


@pytest.mark.parametrize("operation", ["expire", "retire", "sync_retire"])
async def test_resume_expiry_and_retirement_cannot_overwrite_committing_answer(live_hub, monkeypatch, operation):
    hub = live_hub
    hub.requests["r"] = {"id": "r", "status": "interrupted", "mode": "orchestrate", "text": "mock"}
    card = ApprovalRequest(kind="resume", request_id="r", summary="mock", created_at=1)
    hub.approvals[card.id] = {"approval": card.model_dump(mode="json"), "origin": None}
    hub.save_approval(card.id)
    async def set_status(rid, status):
        hub.requests[rid]["status"] = status
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(hub, "_set_status", set_status)
    monkeypatch.setattr(hub, "_restart_request_asks", lambda rid: None)
    monkeypatch.setattr(hub, "resume_when_ready", noop)
    monkeypatch.setattr(hub, "track_request", lambda rid, coro: coro.close())
    monkeypatch.setattr(hub, "aschedule_terminal", noop)
    entered, release = hold_commit(hub.store, monkeypatch)
    resolving = asyncio.create_task(hub.resolve_approval(card.id, True, "PI answer"))
    timer = None
    try:
        await wait_barrier(entered)
        # Release from another thread so the old synchronous expiry/retirement
        # cannot freeze the test's own event loop before its failure is observed.
        timer = threading.Timer(0.1, release.set)
        timer.start()
        if operation == "expire":
            await hub.expire_resume_card(card.id)
        elif operation == "retire":
            await hub.aretire_resume_cards("r", "retired")
        else:
            hub.retire_resume_cards("r", "retired")
        await resolving
        assert hub.store.get("approval_decision", card.id)["approved"] is True
        assert hub.requests["r"]["status"] == "waiting_for_runner"
        assert not any(e.get("data", {}).get("state") == "timed_out" for e in hub.events)
    finally:
        release.set()
        if timer:
            timer.join()
        await asyncio.gather(resolving, return_exceptions=True)


@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_late_translation_save_cannot_reinsert_a_decided_card(live_hub, monkeypatch, operation):
    hub = live_hub
    card, _ = pending_card(hub)
    entered, release = hold_commit(hub.store, monkeypatch)
    action = hub.resolve_approval(card.id, True) if operation == "resolve" else hub.close_approval(card.id, "closed")
    task = asyncio.create_task(action)
    timer = None
    try:
        await wait_barrier(entered)
        hub.approvals[card.id]["approval"]["pi_display"] = {"status": "translated", "text": "mock display"}
        timer = threading.Timer(0.1, release.set)
        timer.start()
        hub.save_approval(card.id)  # PiInterpreter's existing synchronous update
        await task
        reopened = StateStore(hub.store.path)
        try:
            assert reopened.get("approval", card.id) is None
            assert reopened.get("approval_decision", card.id) is not None
        finally:
            reopened.close()
    finally:
        release.set()
        if timer:
            timer.join()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", ["resolve", "close"])
async def test_second_commit_failure_does_not_lose_card_on_restart(tmp_path, monkeypatch, operation):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.requests, hub.runners = {}, {}
    card = ApprovalRequest(kind="tool_permission", summary="mock")
    hub.approvals = {card.id: {"approval": card.model_dump(mode="json"), "origin": None}}
    hub.save_approval(card.id)
    monkeypatch.setattr(hub, "_cancel_facilities_timeout", lambda aid: None)
    monkeypatch.setattr(hub, "_cancel_resume_timer", lambda aid: None)
    transaction = hub.store._transaction
    commits = 0

    @contextmanager
    def fail_second():
        nonlocal commits
        with transaction() as conn:
            yield conn
            commits += 1
            if commits == 2:
                raise OSError("mock second commit failure")

    monkeypatch.setattr(hub.store, "_transaction", fail_second)
    hub.event_lock = asyncio.Lock()
    hub.pending_committed, hub.events, hub.clients = deque(), deque(), set()
    hub.s = SimpleNamespace(gateway=SimpleNamespace(event_buffer=100))
    hub.reporter = SimpleNamespace(enabled=lambda: False)
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None)
    try:
        with pytest.raises(OSError, match="mock second commit failure"):
            if operation == "resolve":
                await hub.resolve_approval(card.id, True)
            else:
                await hub.close_approval(card.id, "mock close")
        reopened = StateStore(hub.store.path)
        try:
            assert (reopened.get("approval", card.id) is not None or
                    reopened.get("approval_decision", card.id) is not None), "restart lost card and decision"
        finally:
            reopened.close()
    finally:
        hub.store.close()
