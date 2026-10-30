"""Gateway publication uses the SQLite lane without blocking its event loop."""
import asyncio
from collections import deque
from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace

import pytest

from labhq import durable
from labhq.gateway.server import Hub
from labhq.store import StateStore


@pytest.fixture
def gateway(tmp_path):
    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "gateway.sqlite3")
    hub.event_lock = asyncio.Lock()
    hub.pending_committed = deque()
    hub.events = deque()
    hub.clients = set()
    hub.requests = {"r": {"id": "r", "status": "running", "results": {}}}
    hub.s = SimpleNamespace(gateway=SimpleNamespace(event_buffer=100))
    hub.reporter = SimpleNamespace(enabled=lambda: False)
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None)
    yield hub
    hub.store.close()


def stall_commit(hub, monkeypatch, number=1, fail=False):
    entered, release = threading.Event(), threading.Event()
    transaction = hub.store._transaction
    count = 0

    @contextmanager
    def blocked():
        nonlocal count
        with transaction() as conn:
            yield conn
            count += 1
            if count == number:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release commit")
                if fail:
                    raise OSError("commit failed")

    monkeypatch.setattr(hub.store, "_transaction", blocked)
    return entered, release


async def until_entered(entered):
    # Poll only a mock boundary; no server, worker or child process is started.
    async def wait():
        while not entered.is_set():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 10)


def event(label="first", request=True):
    return {"type": "request.created", "ts": 1, "request_id": "r" if request else None,
            "data": {"text": label}}


@pytest.mark.parametrize("active_lane", [False, True])
@pytest.mark.parametrize("commit", [1])  # event and request share one transaction (#1129): no second commit to stall
async def test_heartbeat_during_event_and_request_commit(gateway, monkeypatch, active_lane, commit):
    hub = gateway
    assert durable.lane(hub.store.lane).shutdown(5)
    if active_lane:
        await durable.run(hub.store.lane, lambda: None)
    entered, release = stall_commit(hub, monkeypatch, commit)
    task = asyncio.create_task(hub.publish(event()))
    try:
        await until_entered(entered)
        beats = []
        asyncio.get_running_loop().call_soon(beats.append, "heartbeat")
        await asyncio.sleep(0.01)
        assert beats == ["heartbeat"] and not task.done()
        assert hub.store.get("request", "r") is None
    finally:
        release.set()
        await task
    assert hub.store.get("request", "r")["status"] == "running"


async def test_fifo_snapshot_and_restart(gateway, monkeypatch):
    hub = gateway
    entered, release = stall_commit(hub, monkeypatch)
    original = event()
    first = asyncio.create_task(hub.publish(original, "runner", 1))
    try:
        await until_entered(entered)
        original["data"]["text"] = "mutated"
        hub.requests["r"]["results"]["new"] = {"text": "latest"}
        second = asyncio.create_task(hub.publish(event("second"), "runner", 2))
        await asyncio.sleep(0.01)
        assert hub.event_lock.locked() and not second.done()
    finally:
        release.set()
        await first
    await second
    assert [e["data"]["text"] for e in hub.events] == ["first", "second"]
    reopened = StateStore(hub.store.path)
    try:
        assert reopened.events_since(0) == list(hub.events)
        assert reopened.runner_seen("runner") == 2
        assert reopened.get("request", "r")["results"] == {"new": {"text": "latest"}}
    finally:
        reopened.close()


@pytest.mark.parametrize("commit", [1])  # event and request share one transaction (#1129): no second commit to stall
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_repeated_cancel_waits_for_commit_outcome(gateway, monkeypatch, commit, fail, cancel):
    hub = gateway
    entered, release = stall_commit(hub, monkeypatch, commit, fail)
    delivered, reported = [], []

    class Client:
        async def send_text(self, text):
            delivered.append(json.loads(text))

    hub.clients.add(Client())
    hub.reporter = SimpleNamespace(enabled=lambda: True, submit=reported.append)
    task = asyncio.create_task(hub.publish(event(), "runner", 7))
    try:
        await until_entered(entered)
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and hub.event_lock.locked()
    finally:
        release.set()
    if fail or cancel:
        with pytest.raises(OSError if fail else asyncio.CancelledError):
            await task
    else:
        await task
    sender = getattr(hub, "committed_sender", None)
    if sender is not None:
        await asyncio.wait_for(sender, 10)
    assert not hub.event_lock.locked()
    persisted = hub.store.events_since(0)
    assert len(persisted) == (0 if fail and commit == 1 else 1)
    if cancel and not fail and commit == 1:
        # No new publication is needed to deliver the committed event.
        assert sender is not None
    assert len(delivered) == len(persisted)
    assert len(reported) == (0 if fail else 1)
    assert hub.store.runner_seen("runner") == (0 if fail and commit == 1 else 7)
    assert (hub.store.get("request", "r") is not None) == (not fail)
    if not fail:
        assert hub.store.all("reporter_delivery") == {str(persisted[0]["seq"]): persisted[0]}


async def test_request_snapshot_does_not_replace_a_later_save(gateway, monkeypatch):
    hub = gateway
    entered, release = stall_commit(hub, monkeypatch, 1)
    first = asyncio.create_task(hub.publish(event()))
    try:
        await until_entered(entered)
        hub.requests["r"]["status"] = "completed"
        later = asyncio.create_task(hub.publish(event("later")))
        await asyncio.sleep(0.01)
        assert not later.done()
    finally:
        release.set()
        await first
    await later
    assert hub.store.get("request", "r")["status"] == "completed"
    assert hub.requests["r"]["status"] == "completed"  # the first snapshot's write did not revert the live request


async def test_event_and_request_commit_in_one_transaction(gateway, monkeypatch):
    """#1129: one publication is one commit, and a failed commit leaves neither the event nor the request."""
    hub = gateway
    transaction = hub.store._transaction
    commits = []

    @contextmanager
    def counted():
        with transaction() as conn:
            yield conn
        commits.append(1)

    monkeypatch.setattr(hub.store, "_transaction", counted)
    await hub.publish(event(), "runner", 3)
    assert len(commits) == 1
    assert len(hub.store.events_since(0)) == 1 and hub.store.get("request", "r")["status"] == "running"
    assert hub.store.runner_seen("runner") == 3

    @contextmanager
    def failing():
        with transaction() as conn:
            yield conn
            raise OSError("commit failed")

    hub.store.put("request", "r", {"id": "r", "status": "before"})
    monkeypatch.setattr(hub.store, "_transaction", failing)
    with pytest.raises(OSError):
        await hub.publish(event("second"), "runner", 4)
    assert len(hub.store.events_since(0)) == 1
    assert hub.store.get("request", "r")["status"] == "before"
    assert hub.store.runner_seen("runner") == 3
    assert hub.requests["r"]["status"] == "before"  # the live request goes back to what is stored (a restart would show it)


def tool_activity(name):
    def update(activity):
        tools = dict(activity.get("tools") or {})
        tools[name] = {"name": name}
        return {**activity, "state": "working", "tools": tools}
    return update


async def test_activity_commits_with_its_event_or_not_at_all(gateway, monkeypatch):
    """#1132: a publication that carries task activity is one commit; a failed commit leaves neither."""
    hub = gateway
    hub.store.put("task", "t", {"completed": False, "activity": {"state": "queued"}})
    transaction = hub.store._transaction
    commits = []

    @contextmanager
    def counted():
        with transaction() as conn:
            yield conn
        commits.append(1)

    monkeypatch.setattr(hub.store, "_transaction", counted)
    await hub.publish(event(request=False), "runner", 1, activity=("t", tool_activity("one")))
    assert len(commits) == 1
    assert set(hub.store.get("task", "t")["activity"]["tools"]) == {"one"}
    assert len(hub.store.events_since(0)) == 1 and hub.store.runner_seen("runner") == 1

    @contextmanager
    def failing():
        with transaction() as conn:
            yield conn
            raise OSError("commit failed")

    monkeypatch.setattr(hub.store, "_transaction", failing)
    with pytest.raises(OSError):
        await hub.publish(event(request=False), "runner", 2, activity=("t", tool_activity("two")))
    assert set(hub.store.get("task", "t")["activity"]["tools"]) == {"one"}
    assert len(hub.store.events_since(0)) == 1 and hub.store.runner_seen("runner") == 1


async def test_tool_activity_burst_costs_one_commit_per_message_and_keeps_every_tool(gateway, monkeypatch):
    """#1132: activity now waits for the event lock with its event. A burst of agent.tool messages must still land in
    order, one commit each, with no tool lost and no message waiting on an unrelated one beyond its turn."""
    hub = gateway
    hub.store.put("task", "t", {"completed": False, "activity": {"state": "queued"}})
    transaction = hub.store._transaction
    commits = []

    @contextmanager
    def counted():
        with transaction() as conn:
            yield conn
        commits.append(1)

    monkeypatch.setattr(hub.store, "_transaction", counted)
    count = 40
    publications = [asyncio.create_task(hub.publish(event(request=False), "runner", n + 1,
                                                    activity=("t", tool_activity(f"tool{n}"))))
                    for n in range(count)]
    await asyncio.wait_for(asyncio.gather(*publications), 10)
    assert len(commits) == count  # it was 2 per message: the event, then the activity
    assert set(hub.store.get("task", "t")["activity"]["tools"]) == {f"tool{n}" for n in range(count)}
    assert [e["seq"] for e in hub.store.events_since(0)] == list(range(1, count + 1))
    assert hub.store.runner_seen("runner") == count


async def test_activity_waits_for_a_slow_client_only_as_long_as_its_event_does(gateway, monkeypatch):
    """#1132: the activity rides the event lock, so a client that stalls the previous event holds it for at most the
    network timeout (set short here); the activity is then saved and the slow client is dropped, as for any event."""
    import labhq.gateway.server as server

    hub = gateway
    hub.store.put("task", "t", {"completed": False, "activity": {"state": "queued"}})
    monkeypatch.setattr(server, "CLIENT_SEND_TIMEOUT_S", 0.05)

    class Slow:
        async def send_text(self, text):
            await asyncio.Event().wait()

        async def close(self, code):
            pass

    slow = Slow()
    hub.clients.add(slow)
    await hub.publish(event(request=False), "runner", 1)  # the slow client stalls this one until its timeout
    assert slow not in hub.clients
    await asyncio.wait_for(hub.publish(event(request=False), "runner", 2, activity=("t", tool_activity("late"))), 1)
    assert set(hub.store.get("task", "t")["activity"]["tools"]) == {"late"}


async def test_publication_without_request(gateway):
    await gateway.publish(event(request=False))
    assert len(gateway.events) == 1
    assert gateway.store.all("request") == {}


@pytest.mark.parametrize("buffer_size", [1, 10])
async def test_sync_review_during_async_commit_preserves_sequence(gateway, monkeypatch, buffer_size):
    hub = gateway
    hub.events = deque(maxlen=buffer_size)
    hub.requests["r"]["status"] = "done"
    hub.result_storage = SimpleNamespace(schedule=lambda rid: None)
    monkeypatch.setattr(hub, "retire_resume_cards", lambda *args: [])
    delivered = []

    class Client:
        async def send_text(self, text):
            delivered.append(json.loads(text)["seq"])

    hub.clients.add(Client())
    entered, release = stall_commit(hub, monkeypatch)
    task = asyncio.create_task(hub.publish(event()))
    timer = None
    try:
        await until_entered(entered)
        # A synchronous helper waits on this lane, then appends seq 2 before
        # publication's event-loop continuation can append seq 1.
        timer = threading.Timer(0.02, release.set)
        timer.start()
        hub.review_done("r")
        assert [e["seq"] for e in hub.events] == [2]
    finally:
        release.set()
        if timer:
            timer.join()
        await task
    await hub._send_committed_event()
    assert [e["seq"] for e in hub.events] == ([2] if buffer_size == 1 else [1, 2])
    assert delivered == [1, 2]
    assert [e["seq"] for e in hub.store.events_since(0)] == [1, 2]


@pytest.mark.parametrize("since", [None, 0, -1])
async def test_rest_history_waits_for_complete_ordered_publication(tmp_path, monkeypatch, since):
    from labhq.gateway.server import create_app
    from labhq.settings import Settings

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "status": "done"}
    hub.result_storage = SimpleNamespace(schedule=lambda rid: None)
    monkeypatch.setattr(hub, "retire_resume_cards", lambda *args: [])
    committed, resume = asyncio.Event(), asyncio.Event()
    append = hub.store.aappend_event

    async def pause_after_commit(*args, **kwargs):
        ev = await append(*args, **kwargs)
        committed.set()
        await resume.wait()
        return ev

    monkeypatch.setattr(hub.store, "aappend_event", pause_after_commit)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/events")
    publication = asyncio.create_task(hub.publish(event(request=False)))
    read = None
    try:
        await asyncio.wait_for(committed.wait(), 10)
        hub.review_done("r")
        read = asyncio.create_task(endpoint(since=since))
        await asyncio.sleep(0)
        assert not read.done(), "REST must share the WebSocket ordering boundary"
    finally:
        resume.set()
        await publication
        if read:
            response = await read
        hub.store.close()
    history = response[0]["data"]["recent_events"] if since == -1 else response
    assert [ev["seq"] for ev in history] == [1, 2]


async def test_cancel_backpressured_client_finishes_storage_and_releases_event_lock(gateway):
    hub = gateway
    entered = asyncio.Event()
    closed = asyncio.Event()

    class StalledClient:
        async def send_text(self, text):
            entered.set()
            await asyncio.Event().wait()

        async def close(self, code):
            assert code == 1012
            closed.set()

    stalled = StalledClient()
    hub.clients.add(stalled)
    publication = asyncio.create_task(hub.publish(event(), "runner", 1))
    await asyncio.wait_for(entered.wait(), 10)
    publication.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(publication, 1)
    await asyncio.wait_for(closed.wait(), 10)
    assert not hub.event_lock.locked() and stalled not in hub.clients
    assert hub.store.get("request", "r")["status"] == "running"
    assert hub.store.runner_seen("runner") == 1
    await asyncio.wait_for(hub.publish(event("next"), "runner", 2), 1)
    assert [e["seq"] for e in hub.events] == [1, 2]
    assert [e["seq"] for e in hub.store.events_since(0)] == [1, 2]
    assert not hub.pending_committed


async def test_asgi_cancel_scope_only_shields_storage(gateway):
    import anyio

    hub = gateway
    entered = asyncio.Event()
    scopes = []

    class Client:
        async def send_text(self, text):
            entered.set()
            await asyncio.Event().wait()

    client = Client()
    hub.clients.add(client)

    async def publish_in_scope():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await hub.publish(event())

    publication = asyncio.create_task(publish_in_scope())
    await asyncio.wait_for(entered.wait(), 10)
    scopes[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(publication, 1)
    assert hub.store.get("request", "r")["status"] == "running"
    assert not hub.event_lock.locked() and client not in hub.clients
    await asyncio.wait_for(hub.publish(event("next")), 1)
    assert [e["seq"] for e in hub.store.events_since(0)] == [1, 2]


async def test_cancelled_send_still_delivers_to_remaining_clients_without_next_event(gateway):
    hub = gateway
    blocked, received = asyncio.Event(), asyncio.Event()
    deliveries = []

    class Slow:
        async def send_text(self, text):
            blocked.set()
            await asyncio.Event().wait()

    class Healthy:
        async def send_text(self, text):
            deliveries.append(json.loads(text)["seq"])
            received.set()

    class OrderedClients(list):
        def discard(self, client):
            if client in self:
                self.remove(client)

    hub.clients = OrderedClients([Slow(), Healthy()])
    publication = asyncio.create_task(hub.publish(event()))
    await asyncio.wait_for(blocked.wait(), 10)
    publication.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(publication, 1)
    await asyncio.wait_for(received.wait(), 10)
    await asyncio.wait_for(hub.committed_sender, 10)
    assert deliveries == [1] and not hub.pending_committed
    assert not hub.event_lock.locked()


async def test_network_timeout_closes_client_without_holding_publication(gateway, monkeypatch):
    import labhq.gateway.server as server

    monkeypatch.setattr(server, "CLIENT_SEND_TIMEOUT_S", 0.01)
    closed = asyncio.Event()

    class Slow:
        async def send_text(self, text):
            await asyncio.Event().wait()

        async def close(self, code):
            assert code == 1012
            closed.set()

    slow = Slow()
    gateway.clients.add(slow)
    await asyncio.wait_for(gateway.publish(event()), 1)
    await asyncio.wait_for(closed.wait(), 1)
    assert slow not in gateway.clients and not gateway.event_lock.locked()
    assert gateway.store.get("request", "r")["status"] == "running"


async def test_anyio_cancel_during_commit_prevents_caller_continuation(gateway, monkeypatch):
    import anyio

    hub = gateway
    entered, release = stall_commit(hub, monkeypatch)
    scopes, continued = [], []

    async def caller():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await hub.publish(event())
            continued.append("must not continue after cancellation")

    publication = asyncio.create_task(caller())
    try:
        await until_entered(entered)
        scopes[0].cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(publication, 1)
    assert continued == []
    assert hub.store.get("request", "r")["status"] == "running"
    assert not hub.event_lock.locked()


async def test_disconnected_client_is_not_retried_for_later_pending_events(gateway, monkeypatch):
    import labhq.gateway.server as server

    monkeypatch.setattr(server, "CLIENT_SEND_TIMEOUT_S", 0.01)
    attempts, delivered = [], []

    class Slow:
        async def send_text(self, text):
            attempts.append(json.loads(text)["seq"])
            await asyncio.Event().wait()

    class Healthy:
        async def send_text(self, text):
            delivered.append(json.loads(text)["seq"])

    slow, healthy = Slow(), Healthy()
    gateway.clients.update([slow, healthy])
    for label in ("first", "second", "third"):
        saved = gateway.store.append_event(event(label), 100)
        gateway.pending_committed.append((saved, (slow, healthy)))
    await asyncio.wait_for(gateway._send_committed_event(), 1)
    assert attempts == [1]
    assert delivered == [1, 2, 3]
    assert not gateway.pending_committed
