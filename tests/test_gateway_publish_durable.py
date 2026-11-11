"""Gateway publication uses the SQLite lane without blocking its event loop."""
import asyncio
from collections import deque
from contextlib import contextmanager
import json
import subprocess
import sys
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


async def test_inbox_ask_with_unknown_start_is_not_reexecuted(gateway):
    from labhq.models import AskRequest

    hub = gateway
    hub.ask_tasks = {}
    calls = []

    async def answer(*args):
        calls.append(args)

    hub.orchestrator = SimpleNamespace(answer_ask=answer)
    ask = AskRequest.model_validate({"id": "unknown", "to": "pi", "question": "question",
                                     "why_blocked": "waiting"})
    hub.store.put("ask", ask.id, {"ask": ask.model_dump(mode="json"), "origin": "runner",
                                  "state": "working", "inbox_controlled": True})
    hub._start_ask(ask, "runner")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not calls
    assert hub.store.get("ask", ask.id).get("recovery_blocked") == "ask start outcome unknown"


@pytest.mark.parametrize("after_commit", [False, True])
async def test_runner_log_staged_commit_boundary(gateway, monkeypatch, after_commit):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    delivered, interpreted, attempts = [], [], []

    class Client:
        async def send_text(self, value):
            delivered.append(json.loads(value))

    hub.clients.add(Client())
    hub.pi_interpreter = SimpleNamespace(schedule=interpreted.append)
    monkeypatch.setattr(hub, "send_runner", lambda *args: asyncio.sleep(0))
    commit = hub.store.acommit_runner_publication

    async def fail(*args, **kwargs):
        attempts.append(True)
        if after_commit:
            await commit(*args, **kwargs)
        raise RuntimeError("injected publication boundary")

    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail)
    msg = {"type": "agent.log", "runner_seq": 1, "data": {"text": "boundary secret"}}
    await hub.on_runner_message("runner", msg)
    assert attempts == [True]
    events = hub.store.events_since(0)
    if after_commit:
        assert len(events) == 1 and delivered == events and interpreted == events
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    else:
        assert events == [] and delivered == [] and interpreted == []
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"


@pytest.mark.parametrize("after_commit", [False, True])
async def test_runner_ask_staged_commit_boundary(gateway, monkeypatch, after_commit):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    started, deliveries, interpreted, attempts = [], [], [], []

    class Client:
        async def send_text(self, value):
            deliveries.append(json.loads(value))

    hub.clients.add(Client())
    hub.pi_interpreter = SimpleNamespace(schedule=interpreted.append)
    monkeypatch.setattr(hub, "send_runner", lambda *args: asyncio.sleep(0))
    monkeypatch.setattr(hub, "_start_ask", lambda ask, runner: started.append(ask.id))
    commit = hub.store.acommit_runner_publication

    async def fail(key, name, *args, **kwargs):
        if name != "agent.ask":
            return await commit(key, name, *args, **kwargs)
        attempts.append(True)
        if after_commit:
            await commit(key, name, *args, **kwargs)
        raise RuntimeError("injected ask publication boundary")

    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail)
    msg = {"type": "ask.requested", "runner_seq": 1,
           "data": {"id": "a", "to": "pi", "question": "private question", "why_blocked": "waiting"}}
    await hub.on_runner_message("runner", msg)
    assert attempts == [True]
    events = hub.store.events_since(0)
    if after_commit:
        assert [e["type"] for e in events] == ["agent.ask", "ask.requested"]
        assert deliveries == interpreted == events and started == ["a"]
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
        await hub.on_runner_message("runner", msg)
        assert started == ["a"] and hub.store.events_since(0) == events
    else:
        assert events == deliveries == interpreted == started == []
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"


async def test_unknown_partial_task_accept_is_held_without_duplicate_send(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.runner_incarnations = {}
    hub.recruits_running = {}
    hub.store.put("task", "t", {"kind": "step", "step_id": "s", "request_id": "r"})
    sends = []
    monkeypatch.setattr(hub, "_session_state_changed", lambda: None)
    monkeypatch.setattr(hub, "controllable", lambda *args: True)

    async def push(task):
        sends.append(task)

    async def fail(*args, **kwargs):
        raise RuntimeError("after control send, before publication")

    monkeypatch.setattr(hub, "_push_task_state", push)
    monkeypatch.setattr(hub, "send_runner", lambda *args: asyncio.sleep(0))
    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail)
    msg = {"type": "task.accepted", "task_id": "t", "runner_seq": 1}
    await hub.on_runner_message("runner", msg)
    await hub.on_runner_message("runner", msg)
    assert sends == ["t"] and hub.store.events_since(0) == []
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    reopened = StateStore(hub.store.path)
    try:
        assert reopened.runner_inbox("runner", None, 1)["state"] == "failed"
        assert reopened.get("task", "t")["accepted"] is True
    finally:
        reopened.close()


async def test_failed_unpublished_log_can_be_retried_explicitly(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    monkeypatch.setattr(hub, "send_runner", lambda *args: asyncio.sleep(0))
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "retained"}}

    async def fail(*args, **kwargs):
        raise RuntimeError("before commit")

    with monkeypatch.context() as m:
        m.setattr(hub.store, "acommit_runner_publication", fail)
        await hub.on_runner_message("runner", message)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    await hub.retry_runner_inbox("runner", None, 1)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    assert [e["data"]["text"] for e in hub.store.events_since(0)] == ["retained"]


async def test_startup_recovers_committed_runner_publication(gateway):
    hub = gateway
    store = hub.store
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "committed"}}
    store.accept_runner_inbox("runner", None, 1, message)
    key = store._runner_inbox_key("runner", None, 1)
    store.prepare_runner_publication(key, "message", message)
    saved = store.commit_runner_publication(key, "message", 100)
    delivered, interpreted = [], []

    class Client:
        async def send_text(self, value):
            delivered.append(json.loads(value))

    hub.clients.add(Client())
    hub.pi_interpreter = SimpleNamespace(schedule=interpreted.append, tasks={})
    await hub.recover_runner_publications()
    await hub.recover_runner_publications()
    assert delivered == [saved]
    assert interpreted == [saved]
    record = store.runner_publication(key, "message")
    assert record["delivery_state"] == record["interpreter_state"] == "done"


async def test_recovery_does_not_complete_other_message_transformed_to_log(gateway):
    hub = gateway
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    msg = {"type": "restricted.requested", "runner_seq": 1, "data": {}}
    hub.store.accept_runner_inbox("runner", None, 1, msg)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", None, 1)
    hub.store.prepare_runner_publication(key, "message", {"type": "agent.log", "data": {"text": "decision"}})
    hub.store.commit_runner_publication(key, "message", 100)
    await hub.recover_runner_publications()
    retained = hub.store.runner_inbox("runner", None, 1)
    assert retained["state"] == "failed" and retained["error"] == "interrupted_unknown"


@pytest.mark.parametrize("kind", [
    "approval.attachment.ack", "approval.ack", "approval.requested", "approval.timed_out",
    "ask.ack", "control.nack", "agent.breaker", "agent.control", "task.result", "restricted.requested",
    "jobs.finished", "task.jobs_cancelled", "task.accepted", "job.submitted", "recruit.done", "recruit.failed",
])
async def test_startup_holds_interrupted_side_effect_types(gateway, monkeypatch, kind):
    hub = gateway
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    msg = {"type": kind, "runner_seq": 1, "data": {}}
    hub.store.accept_runner_inbox("runner", None, 1, msg)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")

    async def unexpected(*args, **kwargs):
        raise AssertionError("unknown side effects were reexecuted")

    monkeypatch.setattr(hub, "_on_runner_message", unexpected)
    await hub.recover_runner_publications()
    row = hub.store.runner_inbox("runner", None, 1)
    assert row["state"] == "failed" and row["error"] == "interrupted_unknown"
    assert hub.store.events_since(0) == []


@pytest.mark.parametrize("main_committed", [False, True])
async def test_pipeline_event_waits_for_result_commit_and_recovers(gateway, main_committed):
    hub = gateway
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    message = {"type": "task.result", "runner_seq": 1, "task_id": "t", "data": {}}
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", None, 1)
    pipeline = {"type": "pipeline.ready", "task_id": "t", "ts": 1, "data": {}}
    hub.store.prepare_runner_publication(key, "pipeline.ready", pipeline)
    if main_committed:
        hub.store.prepare_runner_publication(key, "message", message)
        hub.store.commit_runner_publication(key, "message", 100)
    await hub.recover_runner_publications()
    events = hub.store.events_since(0)
    assert [e["type"] for e in events] == (["task.result", "pipeline.ready"] if main_committed else [])
    assert hub.store.runner_inbox("runner", None, 1)["error"] == "interrupted_unknown"


@pytest.mark.parametrize("ask_state", ["pending", "working"])
async def test_startup_distinguishes_unstarted_and_uncertain_ask(gateway, monkeypatch, ask_state):
    from labhq.models import AskRequest

    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    starts = []
    monkeypatch.setattr(hub, "_start_ask", lambda ask, runner: starts.append(ask.id))
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Q", "why_blocked": "waiting"})
    message = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", None, 1)
    hub.store.put("ask", "a", {"state": ask_state, "origin": "runner", "ask": message["data"],
                                 "publish_intent": True, "inbox_controlled": True, "inbox_key": key})
    event = {"type": "agent.ask", "ts": 1, "task_id": None, "agent_id": None, "request_id": None,
             "data": message["data"]}
    hub.store.prepare_runner_publication(key, "agent.ask", event)
    hub.store.commit_runner_publication(key, "agent.ask", 100)
    await hub.recover_runner_publications()
    if ask_state == "pending":
        assert starts == ["a"]
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
        assert [event["type"] for event in hub.store.events_since(0)] == ["agent.ask", "ask.requested"]
    else:
        assert starts == []
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
        assert hub.store.get("ask", "a")["recovery_blocked"] == "ask start outcome unknown"


@pytest.mark.parametrize("kind", ["log", "ask"])
async def test_startup_recovers_safe_precommit_interruption(gateway, monkeypatch, kind):
    from labhq.models import AskRequest

    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    starts = []
    monkeypatch.setattr(hub, "_start_ask", lambda ask, runner: starts.append(ask.id))
    if kind == "log":
        message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "private"}}
        event, name = message, "message"
    else:
        ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "private", "why_blocked": "waiting"})
        message = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
        event = {"type": "agent.ask", "ts": 1, "task_id": None, "agent_id": None, "request_id": None,
                 "data": message["data"]}
        name = "agent.ask"
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", None, 1)
    if kind == "ask":
        hub.store.put("ask", "a", {"state": "pending", "origin": "runner", "ask": message["data"],
                                     "publish_intent": True, "inbox_controlled": True, "inbox_key": key})
    hub.store.prepare_runner_publication(key, name, event)
    assert hub.store.events_since(0) == []
    await hub.recover_runner_publications()
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    assert len(hub.store.events_since(0)) == (1 if kind == "log" else 2)
    assert starts == ([] if kind == "log" else ["a"])


async def test_old_generation_recovery_does_not_advance_new_runner_cursor(gateway):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    hub.store.runner_incarnation("runner", "old")
    old = {"type": "agent.log", "runner_seq": 5, "data": {"text": "old"}}
    hub.store.accept_runner_inbox("runner", "old", 5, old)
    hub.store.set_runner_inbox_state("runner", "old", 5, "processing", expected="pending")
    hub.store.prepare_runner_publication(hub.store._runner_inbox_key("runner", "old", 5), "message", old)
    hub.store.runner_incarnation("runner", "new")
    await hub.recover_runner_publications()
    fresh = {"type": "agent.log", "runner_seq": 1, "data": {"text": "new"}}
    assert hub.store.runner_seen("runner") == 0
    assert hub.store.accept_runner_inbox("runner", "new", 1, fresh)["state"] == "pending"


async def test_startup_does_not_start_ask_for_replaced_runner_generation(gateway, monkeypatch):
    from labhq.models import AskRequest

    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    starts = []
    monkeypatch.setattr(hub, "_start_ask", lambda ask, runner: starts.append(ask.id))
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Q", "why_blocked": "waiting"})
    msg = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
    hub.store.runner_incarnation("runner", "old")
    hub.store.accept_runner_inbox("runner", "old", 1, msg)
    hub.store.set_runner_inbox_state("runner", "old", 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", "old", 1)
    hub.store.put("ask", "a", {"state": "pending", "origin": "runner", "ask": msg["data"],
                                 "publish_intent": True, "inbox_controlled": True, "inbox_key": key})
    hub.store.runner_incarnation("runner", "new")
    await hub.recover_runner_publications()
    assert starts == [] and hub.store.events_since(0) == []
    row = hub.store.runner_inbox("runner", "old", 1)
    assert row["state"] == "failed" and row["error"] == "runner_generation_changed"


async def test_resolved_answer_is_not_sent_to_replacement_generation(gateway, monkeypatch):
    hub = gateway
    hub.store.runner_incarnation("runner", "old")
    msg = {"type": "ask.requested", "runner_seq": 1, "data": {"id": "a"}}
    hub.store.accept_runner_inbox("runner", "old", 1, msg)
    key = hub.store._runner_inbox_key("runner", "old", 1)
    hub.store.put("ask", "a", {"state": "resolved", "origin": "runner", "answer": {"answer": "old"},
                                 "inbox_key": key})
    hub.store.runner_incarnation("runner", "new")
    sent = []

    async def send(*args):
        sent.append(args)

    monkeypatch.setattr(hub, "send_runner", send)
    await hub.flush_ask_answers("runner")
    assert sent == []


async def test_resolved_ask_answer_publication_recovers_without_reexecution(gateway, monkeypatch):
    from labhq.models import AskRequest

    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.ask_waiters = {}
    hub.runners = {}
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: None, tasks={})
    starts = []
    monkeypatch.setattr(hub, "_start_ask", lambda ask, runner: starts.append(ask.id))
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Q", "why_blocked": "waiting"})
    message = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    key = hub.store._runner_inbox_key("runner", None, 1)
    hub.store.put("ask", "a", {"state": "working", "origin": "runner", "ask": message["data"],
                                 "publish_intent": True, "inbox_controlled": True, "inbox_key": key})
    initial = {"type": "agent.ask", "ts": 1, "task_id": None, "agent_id": None, "request_id": None,
               "data": message["data"]}
    hub.store.prepare_runner_publication(key, "agent.ask", initial)
    hub.store.commit_runner_publication(key, "agent.ask", 100)
    hub.store.prepare_runner_publication(key, "message", message)
    hub.store.commit_runner_publication(key, "message", 100)
    publish = hub.publish

    async def fail_answer(event, *args, **kwargs):
        if event["type"] == "agent.answer":
            raise RuntimeError("after answer save")
        return await publish(event, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(hub, "publish", fail_answer)
        with pytest.raises(RuntimeError, match="after answer save"):
            await hub.resolve_ask(ask, "runner", {"answer": "resolved", "from": "pi"})
    assert hub.store.get("ask", "a")["state"] == "resolved"
    assert not any(e["type"] == "agent.answer" for e in hub.store.events_since(0))
    await hub.recover_runner_publications()
    await hub.recover_runner_publications()
    assert starts == []
    assert len([e for e in hub.store.events_since(0) if e["type"] == "agent.answer"]) == 1
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"


async def test_answer_publication_error_does_not_replace_saved_answer(gateway, monkeypatch):
    from labhq.models import AskRequest

    hub = gateway
    hub.ask_tasks = {}
    hub.ask_waiters = {}
    hub.runners = {}
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Q", "why_blocked": "waiting"})
    msg = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
    hub.store.accept_runner_inbox("runner", None, 1, msg)
    key = hub.store._runner_inbox_key("runner", None, 1)
    hub.store.put("ask", "a", {"state": "pending", "origin": "runner", "ask": msg["data"],
                                 "inbox_controlled": True, "inbox_key": key})

    async def answer(ask, runner):
        await hub.resolve_ask(ask, runner, {"answer": "original", "from": "pi"})

    async def fail(*args, **kwargs):
        raise RuntimeError("publication failed")

    hub.orchestrator = SimpleNamespace(answer_ask=answer)
    monkeypatch.setattr(hub, "publish", fail)
    hub._start_ask(ask, "runner")
    await asyncio.gather(*hub.ask_tasks.values())
    result = hub.store.get("ask", "a")
    assert result["state"] == "resolved" and result["answer"]["answer"] == "original"
    assert result["answer_publish_intent"] is True


async def test_conflicting_second_ask_answer_cannot_replace_first(gateway):
    from labhq.models import AskRequest

    hub = gateway
    hub.ask_waiters = {}
    hub.runners = {}
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Q", "why_blocked": "waiting"})
    msg = {"type": "ask.requested", "runner_seq": 1, "data": ask.model_dump(mode="json")}
    hub.store.accept_runner_inbox("runner", None, 1, msg)
    key = hub.store._runner_inbox_key("runner", None, 1)
    hub.store.put("ask", "a", {"state": "working", "origin": "runner", "ask": msg["data"], "inbox_key": key})
    await hub.resolve_ask(ask, "runner", {"answer": "first", "from": "pi"})
    with pytest.raises(ValueError, match="answer conflict"):
        await hub.resolve_ask(ask, "runner", {"answer": "second", "from": "pi"})
    assert hub.store.get("ask", "a")["answer"]["answer"] == "first"
    assert [e["data"]["answer"] for e in hub.store.events_since(0)] == ["first"]


@pytest.mark.parametrize("kind", ["log", "ask", "ask_uncertain", "ask_answer"])
def test_gateway_process_restart_recovers_committed_publication(tmp_path, kind):
    script = r"""
import asyncio, json, os, socket, sys
import httpx, uvicorn
from labhq.gateway.server import create_app
from labhq.settings import Settings

settings = Settings()
settings.gateway.state_dir = sys.argv[1]
settings.runner.state_dir = sys.argv[1]
settings.runner.workspace_root = sys.argv[2]
settings.gateway.client_token = "local-test-token"
app = create_app(settings)
hub = app.state.hub
kind = sys.argv[4]
message = ({"type": "agent.log", "runner_seq": 1, "data": {"text": "restart proof"}}
           if kind == "log" else {"type": "ask.requested", "runner_seq": 1,
           "data": {"id": "a", "to": "pi", "question": "restart proof", "why_blocked": "waiting"}})
key = hub.store._runner_inbox_key("runner", None, 1)

if sys.argv[3] == "crash":
    @app.on_event("startup")
    async def crash_after_commit():
        if kind in {"ask_uncertain", "ask_answer"}:
            async def die(*args, **kwargs):
                os._exit(0)
            async def send(*args, **kwargs):
                pass
            hub.send_runner = send
            if kind == "ask_uncertain":
                hub.orchestrator.answer_ask = die
            else:
                publish = hub.publish
                async def fail_answer(event, *args, **kwargs):
                    if event.get("type") == "agent.answer":
                        os._exit(0)
                    return await publish(event, *args, **kwargs)
                async def answer(ask, runner):
                    await hub.resolve_ask(ask, runner, {"answer": "done", "from": "pi"})
                hub.publish = fail_answer
                hub.orchestrator.answer_ask = answer
        else:
            commit = hub.store.acommit_runner_publication
            async def die(*args, **kwargs):
                await commit(*args, **kwargs)
                os._exit(0)
            hub.store.acommit_runner_publication = die
        await hub.on_runner_message("runner", message)
        await asyncio.sleep(5)
        raise RuntimeError("gateway did not reach publication commit")
else:
    scheduled = []
    reservations = []
    starts = []
    async def answer(ask, runner):
        starts.append(ask.id)
    hub.orchestrator.answer_ask = answer
    original_schedule = hub.pi_interpreter.schedule
    def schedule(event):
        scheduled.append(event["seq"])
        original_schedule(event)
    hub.pi_interpreter.schedule = schedule
    original_reserve = hub.pi_interpreter._reserve_then_interpret
    async def reserve(event, *args):
        reservations.append(event["seq"])
        return await original_reserve(event, *args)
    hub.pi_interpreter._reserve_then_interpret = reserve

async def main():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):
        if server.started:
            break
        if task.done():
            await task
            raise RuntimeError("gateway did not start")
        await asyncio.sleep(.01)
    else:
        raise TimeoutError("gateway startup")
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(f"http://127.0.0.1:{port}/api/events?since=0",
                                    headers={"Authorization": "Bearer local-test-token"})
        response.raise_for_status()
        events = [e for e in response.json() if e.get("type") == ("agent.log" if kind == "log" else "agent.ask")
                  and "restart proof" in json.dumps(e)]
        assert len(events) == 1 and scheduled.count(events[0]["seq"]) >= 1, (events, scheduled)
        for _ in range(200):
            record = hub.store.runner_publication(key, "message" if kind == "log" else "agent.ask")
            if record["interpreter_state"] == "done":
                break
            await asyncio.sleep(.01)
        assert record["delivery_state"] == record["interpreter_state"] == "done", record
        assert reservations.count(events[0]["seq"]) <= 1, reservations
        if kind in {"log", "ask"}:
            assert reservations.count(events[0]["seq"]) == 1, reservations
            assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
        elif kind == "ask_uncertain":
            assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
            assert hub.store.get("ask", "a")["recovery_blocked"] == "ask start outcome unknown"
            assert starts == []
        else:
            assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
            assert hub.store.get("ask", "a")["state"] == "resolved" and starts == []
            assert len([e for e in response.json() if e.get("type") == "agent.answer"]) == 1
        if kind == "ask":
            assert starts == ["a"]
    server.should_exit = True
    await task

asyncio.run(main())
"""
    args = [sys.executable, "-c", script, str(tmp_path / "gateway"), str(tmp_path / "runs")]
    subprocess.run([*args, "crash", kind], check=True, timeout=30, capture_output=True, text=True)
    recovered = subprocess.run([*args, "recover", kind], timeout=30, capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stderr


def test_runner_inbox_survives_reopen_and_rejects_conflicting_delivery(gateway):
    store = gateway.store
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "private"}}
    first = store.accept_runner_inbox("runner", "generation", 1, message)
    assert first["state"] == "pending" and first["message"] == message
    store.set_runner_inbox_state("runner", "generation", 1, "failed", "failure")
    assert store.accept_runner_inbox("runner", "generation", 1, message)["state"] == "failed"
    with pytest.raises(ValueError, match="conflicting"):
        store.accept_runner_inbox("runner", "generation", 1, {**message, "type": "other"})
    reopened = StateStore(store.path)
    try:
        assert reopened.runner_inbox("runner", "generation", 1)["error"] == "failure"
        assert reopened.runner_seen("runner") == 0
        assert reopened.accept_runner_inbox("runner", "new-generation", 1, message)["state"] == "pending"
    finally:
        reopened.close()


def test_runner_inbox_failed_commit_does_not_retain_message(gateway, monkeypatch):
    store = gateway.store
    transaction = store._transaction

    @contextmanager
    def fail_commit():
        with transaction() as conn:
            yield conn
            raise OSError("injected commit failure")

    monkeypatch.setattr(store, "_transaction", fail_commit)
    with pytest.raises(OSError, match="injected"):
        store.accept_runner_inbox("runner", "generation", 1, {"runner_seq": 1})
    assert store.runner_inbox("runner", "generation", 1) is None
    assert store.runner_seen("runner") == 0


def test_prepared_publication_is_private_stable_and_survives_reopen(gateway):
    store = gateway.store
    message = {"type": "ask.requested", "runner_seq": 1, "data": {"id": "a"}}
    store.accept_runner_inbox("runner", None, 1, message)
    inbox_key = store._runner_inbox_key("runner", None, 1)
    event = {"type": "agent.ask", "data": {"id": "a", "question": "private"}}
    prepared = store.prepare_runner_publication(inbox_key, "agent.ask", event)
    assert prepared["state"] == "prepared"
    assert store.events_since(0) == []
    assert store.prepare_runner_publication(inbox_key, "agent.ask", event) == prepared
    with pytest.raises(ValueError, match="conflict"):
        store.prepare_runner_publication(inbox_key, "agent.ask", {**event, "data": {"id": "different"}})
    reopened = StateStore(store.path)
    try:
        assert reopened.runner_publication(inbox_key, "agent.ask") == prepared
        assert reopened.events_since(0) == []
    finally:
        reopened.close()


def test_prepared_publication_commits_atomically_once(gateway, monkeypatch):
    store = gateway.store
    store.accept_runner_inbox("runner", None, 1, {"runner_seq": 1})
    inbox_key = store._runner_inbox_key("runner", None, 1)
    event = {"type": "agent.ask", "data": {"id": "a", "question": "private"}}
    store.prepare_runner_publication(inbox_key, "agent.ask", event)
    transaction = store._transaction

    @contextmanager
    def fail_commit():
        with transaction() as conn:
            yield conn
            raise OSError("injected before commit")

    with monkeypatch.context() as m:
        m.setattr(store, "_transaction", fail_commit)
        with pytest.raises(OSError, match="injected"):
            store.commit_runner_publication(inbox_key, "agent.ask", 100)
    assert store.events_since(0) == []
    assert store.runner_publication(inbox_key, "agent.ask")["state"] == "prepared"
    published = store.commit_runner_publication(inbox_key, "agent.ask", 100)
    assert store.runner_publication(inbox_key, "agent.ask")["state"] == "published"
    assert store.commit_runner_publication(inbox_key, "agent.ask", 100) == published
    reopened = StateStore(store.path)
    try:
        assert reopened.commit_runner_publication(inbox_key, "agent.ask", 100) == published
        assert reopened.events_since(0) == [published]
    finally:
        reopened.close()


def test_prepared_publication_includes_existing_delivery_and_activity_ledgers(gateway):
    store = gateway.store
    store.accept_runner_inbox("runner", None, 1, {"runner_seq": 1})
    inbox_key = store._runner_inbox_key("runner", None, 1)
    store.put("task", "t", {"activity": {}})
    event = {"type": "agent.log", "task_id": "t", "data": {"text": "hello"}}
    store.prepare_runner_publication(inbox_key, "message", event)
    def update(activity):
        activity["state"] = "working"
        return activity
    published = store.commit_runner_publication(inbox_key, "message", 100, reporter_delivery=True,
                                                 activity=("t", update))
    assert store.get("reporter_delivery", str(published["seq"])) == published
    assert store.get("task", "t")["activity"]["state"] == "working"


def test_committed_publication_keeps_delivery_intent_after_process_exit(gateway):
    store = gateway.store
    store.accept_runner_inbox("runner", None, 1, {"runner_seq": 1})
    inbox_key = store._runner_inbox_key("runner", None, 1)
    event = {"type": "agent.ask", "data": {"id": "a", "question": "private"}}
    store.prepare_runner_publication(inbox_key, "agent.ask", event)
    script = '''
import sys
from pathlib import Path
from labhq.store import StateStore
s = StateStore(Path(sys.argv[1]))
s.commit_runner_publication(sys.argv[2], "agent.ask", 100)
s.close()
'''
    subprocess.run([sys.executable, "-c", script, str(store.path), inbox_key], check=True, timeout=20)
    record = store.runner_publication(inbox_key, "agent.ask")
    assert record["state"] == "published"
    assert record["delivery_state"] == "pending"
    assert record["interpreter_state"] == "pending"


def test_same_ask_in_two_inbox_entries_does_not_publish_twice(gateway):
    store = gateway.store
    event = {"type": "agent.ask", "data": {"id": "a", "question": "same ask"}}
    for seq in (1, 2):
        store.accept_runner_inbox("runner", None, seq, {"runner_seq": seq})
        key = store._runner_inbox_key("runner", None, seq)
        store.prepare_runner_publication(key, "agent.ask", event)
        store.commit_runner_publication(key, "agent.ask", 100)
    assert len(store.events_since(0)) == 1


def test_inbox_event_identity_survives_later_cursor_and_reopen(gateway):
    store = gateway.store
    first = {"type": "agent.log", "runner_seq": 1, "data": {"text": "first"}}
    second = {"type": "agent.log", "runner_seq": 2, "data": {"text": "second"}}
    key1 = store._runner_inbox_key("runner", "generation", 1)
    key2 = store._runner_inbox_key("runner", "generation", 2)
    store.accept_runner_inbox("runner", "generation", 1, first)
    store.accept_runner_inbox("runner", "generation", 2, second)
    store.append_event(second, 100, "runner", 2, inbox_key=key2)
    store.append_event(first, 100, "runner", 1, inbox_key=key1)
    reopened = StateStore(store.path)
    try:
        assert reopened.append_event(first, 100, "runner", 1, inbox_key=key1) is None
        assert [e["data"]["text"] for e in reopened.events_since(0)] == ["second", "first"]
        assert reopened.runner_seen("runner") == 2
    finally:
        reopened.close()


def test_runner_inbox_survives_new_process(gateway):
    store = gateway.store
    store.accept_runner_inbox("runner", "generation", 1, {"runner_seq": 1, "type": "agent.log"})
    store.set_runner_inbox_state("runner", "generation", 1, "failed", "failure")
    script = ("from pathlib import Path; from labhq.store import StateStore; import sys; "
              "s=StateStore(Path(sys.argv[1])); "
              "assert s.runner_inbox('runner','generation',1)['state']=='failed'; "
              "assert s.runner_seen('runner')==0; s.close()")
    subprocess.run([sys.executable, "-c", script, str(store.path)], check=True, timeout=20)


async def test_runner_inbox_transition_compares_previous_state(gateway):
    store = gateway.store
    await store.aaccept_runner_inbox("runner", "generation", 1, {"runner_seq": 1})
    claimed = await store.aset_runner_inbox_state("runner", "generation", 1, "processing", expected="pending")
    assert claimed["state"] == "processing"
    with pytest.raises(ValueError, match="state changed"):
        await store.aset_runner_inbox_state("runner", "generation", 1, "processing", expected="pending")
    await store.aset_runner_inbox_state("runner", "generation", 1, "failed", "unknown", expected="processing")
    store.mark_runner("runner", 2)
    assert store.runner_inbox("runner", "generation", 1)["state"] == "failed"


def test_inbox_uncertain_execution_cannot_be_requeued_after_reopen(gateway):
    store = gateway.store
    for seq in (3, 1, 2):
        store.accept_runner_inbox("runner", "generation", seq, {"runner_seq": seq})
    store.set_runner_inbox_state("runner", "generation", 1, "processing", expected="pending")
    store.set_runner_inbox_state("runner", "generation", 2, "blocked")
    reopened = StateStore(store.path)
    try:
        assert [row["seq"] for row in reopened.runner_inbox_entries("runner", "generation")] == [1, 2, 3]
        with pytest.raises(ValueError, match="unsafe"):
            reopened.set_runner_inbox_state("runner", "generation", 1, "pending", expected="processing")
        with pytest.raises(ValueError, match="pending claim"):
            reopened.set_runner_inbox_state("runner", "generation", 1, "processing")
        with pytest.raises(ValueError, match="requires expected"):
            reopened.set_runner_inbox_state("runner", "generation", 2, "pending")
        reopened.set_runner_inbox_state("runner", "generation", 2, "pending", expected="blocked")
        assert reopened.runner_inbox("runner", "generation", 1)["state"] == "processing"
    finally:
        reopened.close()


@pytest.mark.parametrize("after_commit", [False, True])
async def test_ask_replay_after_publication_failure_starts_pending_ask(gateway, monkeypatch, after_commit):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    started, sent = [], []
    original_publish = hub.publish
    fail = True

    async def publish(ev, *args, **kwargs):
        nonlocal fail
        if ev.get("type") == "agent.ask" and fail:
            fail = False
            if after_commit:
                await original_publish(ev, *args, **kwargs)
            raise RuntimeError("publication failed")
        return await original_publish(ev, *args, **kwargs)

    async def send_runner(runner_id, message):
        sent.append(message)

    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send_runner)
    monkeypatch.setattr(hub, "_start_ask", lambda ask, origin: started.append(ask.id))
    message = {"type": "ask.requested", "runner_seq": 1,
               "data": {"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", message)
    assert hub.store.get("ask", "a")["state"] == "pending"
    assert hub.store.runner_seen("runner") == 0
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    assert sent == [{"type": "runner.ack", "runner_seq": 1}]
    await hub.on_runner_message("runner", message)
    assert started == []
    await hub.retry_runner_inbox("runner", None, 1)
    assert started == ["a"]
    assert len([e for e in hub.store.events_since(0) if e["type"] == "agent.ask"]) == 1
    assert len([e for e in hub.store.events_since(0) if e["type"] == "ask.requested"]) == 1
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    assert sent[-1] == {"type": "runner.ack", "runner_seq": 1}


@pytest.mark.parametrize("after_commit", [False, True])
async def test_ask_publication_recovered_after_store_reopen(gateway, monkeypatch, after_commit):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    class Transport:
        async def send_text(self, text):
            pass
    hub.runners = {"runner": Transport()}
    hub.runner_locks = {"runner": asyncio.Lock()}
    original_publish = hub.publish
    started = []

    async def fail(ev, *args, **kwargs):
        if ev.get("type") == "agent.ask":
            if after_commit:
                await original_publish(ev, *args, **kwargs)
            raise RuntimeError("publication interrupted")
        return await original_publish(ev, *args, **kwargs)

    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "_start_ask", lambda ask, origin: started.append((ask.id, origin)))
    message = {"type": "ask.requested", "runner_seq": 1,
               "data": {"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", message)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    path = hub.store.path
    hub.store.close()
    hub.store = StateStore(path)
    monkeypatch.setattr(hub, "publish", original_publish)
    await hub.retry_runner_inbox("runner", None, 1)
    assert started == [("a", "runner")]
    assert len([e for e in hub.store.events_since(0) if e["type"] == "agent.ask"]) == 1
    assert hub.store.runner_seen("runner") == 1
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    assert len([e for e in hub.store.events_since(0) if e["type"] == "ask.requested"]) == 1


async def test_ask_with_unknown_consult_outcome_is_not_restarted(gateway):
    from labhq.gateway.server import ConsultOutcomeUnknown
    from labhq.models import AskRequest

    hub = gateway
    hub.ask_tasks = {}
    async def answer_ask(*args):
        raise ConsultOutcomeUnknown("consult")
    hub.orchestrator = SimpleNamespace(answer_ask=answer_ask)
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"})
    hub.store.put("ask", "a", {"state": "working", "origin": "runner", "ask": ask.model_dump(mode="json")})
    hub.store.put("task", "consult", {"kind": "consult", "completed": False,
                                       "payload": {"meta": {"ask_id": "a"}}})
    hub._start_ask(ask, "runner")
    await asyncio.gather(*hub.ask_tasks.values())
    await asyncio.sleep(0)
    entry = hub.store.get("ask", "a")
    assert entry["state"] == "pending" and entry["recovery_blocked"] == "consult outcome unknown"
    assert hub.ask_tasks == {}


async def test_reconnect_does_not_restart_ask_with_unknown_consult(gateway):
    from labhq.models import AskRequest

    hub = gateway
    hub.ask_tasks = {}
    started = []
    async def answer_ask(*args):
        started.append(True)
    hub.orchestrator = SimpleNamespace(answer_ask=answer_ask)
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"})
    hub.store.put("ask", "a", {"state": "pending", "origin": "runner", "ask": ask.model_dump(mode="json"),
                                 "recovery_blocked": "consult outcome unknown"})
    hub._start_ask(ask, "runner")
    await asyncio.sleep(0)
    await asyncio.gather(*hub.ask_tasks.values())
    assert started == []
    assert hub.store.get("ask", "a")["state"] == "pending"


async def test_failed_runner_message_is_held_before_later_independent_message(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    sent = []
    publish = hub.publish
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r2"})

    async def fail_ask(ev, *args, **kwargs):
        if ev.get("type") == "agent.ask":
            raise RuntimeError("injected business failure")
        return await publish(ev, *args, **kwargs)

    async def send(_runner, msg):
        sent.append(msg)

    monkeypatch.setattr(hub, "publish", fail_ask)
    monkeypatch.setattr(hub, "send_runner", send)
    ask = {"type": "ask.requested", "runner_seq": 1, "task_id": "one", "request_id": "r1",
           "data": {"id": "a", "to": "pi", "task_id": "one", "request_id": "r1", "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", ask)
    held = list(hub.store.all("runner_inbox").values())
    assert len(held) == 1 and held[0]["message"] == ask and held[0]["state"] == "failed"
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 2,
                                           "task_id": "two", "request_id": "r2", "data": {"text": "independent"}})
    assert sent[-1] == {"type": "runner.ack", "runner_seq": 2}
    assert hub.store.runner_seen("runner") == 2


async def test_independent_activity_continues_after_failed_log(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r2"})
    monkeypatch.setattr(hub, "send_runner", lambda *args: asyncio.sleep(0))
    commit = hub.store.acommit_runner_publication

    async def fail_first(key, name, *args, **kwargs):
        if json.loads(key)[2] == 1:
            raise RuntimeError("injected first failure")
        return await commit(key, name, *args, **kwargs)

    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail_first)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1, "task_id": "one",
                                           "request_id": "r1", "data": {"text": "first"}})
    await hub.on_runner_message("runner", {"type": "agent.status", "runner_seq": 2, "task_id": "two",
                                           "request_id": "r2", "data": {"state": "working"}})
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "completed"
    assert hub.store.get("task", "two")["activity"]["state"] == "working"


async def test_inbox_owned_publication_after_later_cursor_is_not_lost(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    sent = []
    async def send(_runner, message):
        sent.append(message)
    monkeypatch.setattr(hub, "send_runner", send)
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r2"})
    first = {"type": "agent.log", "runner_seq": 1, "task_id": "one", "request_id": "r1", "data": {"text": "first"}}
    second = {"type": "agent.log", "runner_seq": 2, "task_id": "two", "request_id": "r2", "data": {"text": "second"}}
    hub.store.accept_runner_inbox("runner", None, 1, first)
    hub.store.set_runner_inbox_state("runner", None, 1, "failed", "unknown")
    await hub.on_runner_message("runner", second)
    await hub._on_runner_message("runner", first, inbox_owned=True)
    await hub._on_runner_message("runner", first, inbox_owned=True)
    assert [e["data"]["text"] for e in hub.store.events_since(0)] == ["second", "first"]
    assert hub.store.runner_seen("runner") == 2


async def test_legacy_cursor_duplicate_does_not_reexecute_on_first_inbox_delivery(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.mark_runner("runner", 5)
    calls, sent = [], []
    async def publish(*args, **kwargs):
        calls.append(args)
    async def send(_runner, message):
        sent.append(message)
    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 4, "data": {"text": "old"}})
    assert calls == []
    assert hub.store.runner_inbox("runner", None, 4)["state"] == "completed"
    assert sent == [{"type": "runner.ack", "runner_seq": 4}]


async def test_ask_retry_after_independent_event_and_reopen(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r2"})
    publish = hub.publish
    starts, sent = [], []
    async def fail(ev, *args, **kwargs):
        if ev.get("type") == "agent.ask":
            raise RuntimeError("injected failure")
        return await publish(ev, *args, **kwargs)
    async def send(_runner, message):
        sent.append(message)
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_start_ask", lambda ask, origin: starts.append(ask.id))
    message = {"type": "ask.requested", "runner_seq": 1, "task_id": "one", "request_id": "r1",
               "data": {"id": "a", "to": "pi", "task_id": "one", "request_id": "r1",
                        "question": "private question", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", message)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 2, "task_id": "two",
                                            "request_id": "r2", "data": {"text": "independent"}})
    assert "private question" not in json.dumps(hub.store.events_since(0))
    assert hub.store.runner_seen("runner") == 2
    path = hub.store.path
    hub.store.close()
    hub.store = StateStore(path)
    monkeypatch.setattr(hub, "publish", publish)
    await hub.retry_runner_inbox("runner", None, 1)
    assert starts == ["a"]
    events = hub.store.events_since(0)
    assert [e["type"] for e in events].count("agent.ask") == 1
    assert [e["type"] for e in events].count("ask.requested") == 1
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    assert sent == [{"type": "runner.ack", "runner_seq": 1}, {"type": "runner.ack", "runner_seq": 2}]


async def test_held_ask_is_not_visible_before_publication_commit(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    async def fail_before_commit(*args, **kwargs):
        raise RuntimeError("before event commit")

    async def send(*args):
        pass

    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail_before_commit)
    monkeypatch.setattr(hub, "send_runner", send)
    message = {"type": "ask.requested", "runner_seq": 1,
               "data": {"id": "a", "to": "pi", "question": "held secret", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", message)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    assert "held secret" not in json.dumps(hub.store.events_since(0))


async def test_held_log_is_not_visible_before_publication_commit(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    async def fail_before_commit(*args, **kwargs):
        raise RuntimeError("before event commit")

    async def send(*args):
        pass

    monkeypatch.setattr(hub.store, "acommit_runner_publication", fail_before_commit)
    monkeypatch.setattr(hub, "send_runner", send)
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "held log secret"}}
    await hub.on_runner_message("runner", message)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    assert "held log secret" not in json.dumps(hub.store.events_since(0))


@pytest.mark.parametrize("after_commit", [False, True])
async def test_ask_publication_recovery_in_new_process(gateway, monkeypatch, after_commit):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    publish = hub.publish
    async def fail(*args, **kwargs):
        if after_commit:
            await publish(*args, **kwargs)
        raise RuntimeError("injected")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    message = {"type": "ask.requested", "runner_seq": 1,
               "data": {"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", message)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    path = hub.store.path
    hub.store.close()
    script = '''
import asyncio, sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from labhq.gateway.server import Hub
from labhq.store import StateStore
h = Hub.__new__(Hub)
h.store = StateStore(Path(sys.argv[1]))
h.event_lock = asyncio.Lock()
h.pending_committed = deque()
h.events = deque()
h.clients = set()
h.requests = {}
h.s = SimpleNamespace(gateway=SimpleNamespace(event_buffer=100))
h.reporter = SimpleNamespace(enabled=lambda: False)
h.pi_interpreter = SimpleNamespace(schedule=lambda event: None)
h.runner_capabilities = {}
h.recruits_running = {}
started = []
h._start_ask = lambda ask, origin: started.append(ask.id)
asyncio.run(h.retry_runner_inbox("runner", None, 1))
assert started == ["a"]
assert [e["type"] for e in h.store.events_since(0)] == ["agent.ask", "ask.requested"]
assert h.store.runner_inbox("runner", None, 1)["state"] == "completed"
h.store.close()
'''
    try:
        subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=20)
    finally:
        hub.store = StateStore(path)
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"


async def test_ask_recovery_reschedules_existing_publication_after_event_eviction(gateway, monkeypatch):
    from labhq.models import AskRequest
    hub = gateway
    hub.recruits_running = {}
    scheduled = []
    hub.pi_interpreter = SimpleNamespace(schedule=lambda event: scheduled.append(event))
    ask = AskRequest.model_validate({"id": "a", "to": "pi", "question": "technical question",
                                      "why_blocked": "waiting"})
    hub.store.put("ask", "a", {"state": "pending", "origin": "runner", "ask": ask.model_dump(mode="json"),
                               "publish_intent": True})
    saved = hub.store.append_event({"type": "agent.ask", "data": ask.model_dump(mode="json")}, 1)
    hub.store.append_event({"type": "other", "data": {}}, 1)
    assert all(event["seq"] != saved["seq"] for event in hub.store.events_since(0))
    monkeypatch.setattr(hub, "_start_ask", lambda *_: None)
    await hub._recover_ask_publication("a")
    assert [event["seq"] for event in scheduled] == [saved["seq"]]
    assert len(hub.store.events_since(0)) == 1


async def test_blocked_delivery_resumes_only_after_earlier_ask_recovery(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    publish = hub.publish
    async def fail(ev, *args, **kwargs):
        if ev.get("type") == "agent.ask":
            raise RuntimeError("injected")
        return await publish(ev, *args, **kwargs)
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_start_ask", lambda *_: None)
    first = {"type": "ask.requested", "runner_seq": 1,
             "data": {"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    second = {"type": "agent.log", "runner_seq": 2, "data": {"text": "after"}}
    await hub.on_runner_message("runner", first)
    await hub.on_runner_message("runner", second)
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "blocked"
    with pytest.raises(ValueError, match="earlier"):
        await hub.resume_blocked_runner_inbox("runner", None, 2)
    monkeypatch.setattr(hub, "publish", publish)
    await hub.retry_runner_inbox("runner", None, 1)
    await hub.resume_blocked_runner_inbox("runner", None, 2)
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "completed"
    assert [e["type"] for e in hub.store.events_since(0)] == ["agent.ask", "ask.requested", "agent.log"]


def test_inbox_inspection_and_recovery_require_auth_and_omit_raw_message(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from labhq.gateway.server import create_app
    from labhq.settings import Settings
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    settings.gateway.client_token = "test-client-token"
    app = create_app(settings)
    hub = app.state.hub
    message = {"type": "ask.requested", "runner_seq": 1, "data": {"question": "secret input"}}
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    hub.store.set_runner_inbox_state("runner", None, 1, "failed", "ask_publication_incomplete", expected="processing")
    seen = []
    async def run(_runner, msg, **kwargs):
        seen.append(msg)
    monkeypatch.setattr(hub, "_on_runner_message", run)
    client = TestClient(app)
    try:
        assert client.get("/api/runner-inbox/runner").status_code == 401
        headers = {"Authorization": "Bearer test-client-token"}
        listed = client.get("/api/runner-inbox/runner", headers=headers)
        assert listed.status_code == 200 and listed.json()[0]["state"] == "failed"
        assert "secret input" not in listed.text
        assert client.post("/api/runner-inbox/runner/1/retry", headers=headers).status_code == 200
        assert seen == [message]
        assert hub.store.runner_inbox("runner", None, 1)["state"] == "completed"
    finally:
        client.close()
        hub.store.close()


async def test_ask_retry_refuses_replaced_generation_without_mutation(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.runner_incarnation("runner", "old")
    async def fail(ev, *args, **kwargs):
        raise RuntimeError("publication failure")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    msg = {"type": "ask.requested", "runner_seq": 8,
           "data": {"id": "a", "to": "pi", "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner", msg, incarnation="old")
    hub.store.runner_incarnation("runner", "new")
    hub.store.mark_runner("runner", 1)
    with pytest.raises(ValueError, match="generation"):
        await hub.retry_runner_inbox("runner", "old", 8)
    assert hub.store.runner_inbox("runner", "old", 8)["state"] == "failed"
    assert hub.store.runner_seen("runner") == 1


@pytest.mark.parametrize("second_request", ["r1", None])
async def test_inbox_holds_different_task_when_request_is_same_or_unknown(gateway, monkeypatch, second_request):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    calls = []

    async def fail(ev, *args, **kwargs):
        calls.append(ev)
        raise RuntimeError("unknown outcome")

    async def send(*args):
        pass

    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1,
                                            "task_id": "one", "request_id": "r1", "data": {}})
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 2,
                                            "task_id": "two", "request_id": second_request, "data": {}})
    assert len(calls) == 1
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "blocked"


async def test_inbox_holds_conflicting_task_request_identity(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r1"})
    calls = []
    async def fail(ev, *args, **kwargs):
        calls.append(ev)
        raise RuntimeError("unknown outcome")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1,
                                            "task_id": "one", "request_id": "r1", "data": {}})
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 2,
                                            "task_id": "two", "request_id": "r2", "data": {}})
    assert len(calls) == 1
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "blocked"


async def test_inbox_does_not_assume_runner_wide_message_is_request_local(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r2"})
    calls = []
    async def fail(ev, *args, **kwargs):
        calls.append(ev)
        raise RuntimeError("unknown outcome")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1,
                                            "task_id": "one", "request_id": "r1", "data": {}})
    await hub.on_runner_message("runner", {"type": "recruit.done", "runner_seq": 2,
                                            "task_id": "two", "request_id": "r2", "data": {}})
    assert len(calls) == 1
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "blocked"


async def test_inbox_holds_same_request_across_runners(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r1"})
    calls = []
    async def fail(ev, *args, **kwargs):
        calls.append(ev)
        raise RuntimeError("unknown outcome")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner1", {"type": "agent.log", "runner_seq": 1,
                                             "task_id": "one", "request_id": "r1", "data": {}})
    await hub.on_runner_message("runner2", {"type": "agent.log", "runner_seq": 1,
                                             "task_id": "two", "request_id": "r1", "data": {}})
    assert len(calls) == 1
    assert hub.store.runner_inbox("runner2", None, 1)["state"] == "blocked"
    with pytest.raises(ValueError, match="earlier"):
        await hub.resume_blocked_runner_inbox("runner2", None, 1)


async def test_blocked_cross_runner_entries_resume_in_admission_order(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    for task in ("one", "two", "three"):
        hub.store.put("task", task, {"request_id": "r1"})
    publish = hub.publish
    async def fail(ev, *args, **kwargs):
        if ev.get("type") == "agent.ask":
            raise RuntimeError("injected")
        return await publish(ev, *args, **kwargs)
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_start_ask", lambda *_: None)
    ask = {"type": "ask.requested", "runner_seq": 1, "task_id": "one", "request_id": "r1",
           "data": {"id": "a", "to": "pi", "task_id": "one", "request_id": "r1",
                    "question": "Why?", "why_blocked": "Waiting"}}
    await hub.on_runner_message("runner1", ask)
    for number, task in ((2, "two"), (3, "three")):
        await hub.on_runner_message(f"runner{number}", {"type": "agent.log", "runner_seq": 1,
                                    "task_id": task, "request_id": "r1", "data": {"text": task}})
    monkeypatch.setattr(hub, "publish", publish)
    await hub.retry_runner_inbox("runner1", None, 1)
    await hub.resume_blocked_runner_inbox("runner2", None, 1)
    await hub.resume_blocked_runner_inbox("runner3", None, 1)
    assert [e["data"].get("text") for e in hub.store.events_since(0) if e["type"] == "agent.log"] == ["two", "three"]


async def test_reconnect_does_not_reexecute_inflight_delivery(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "uncertain"}}
    hub.store.accept_runner_inbox("runner", None, 1, message)
    hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
    called = []
    async def handler(*args, **kwargs):
        called.append(True)
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "_on_runner_message", handler)
    monkeypatch.setattr(hub, "send_runner", send)
    await hub.on_runner_message("runner", message)
    assert called == []
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "processing"


async def test_ask_id_cannot_alias_another_runners_ask(gateway, monkeypatch):
    from labhq.models import AskRequest
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    ask = {"id": "a", "to": "pi", "question": "original", "why_blocked": "Waiting"}
    hub.store.put("ask", "a", {"state": "pending", "origin": "runner1",
                               "ask": AskRequest.model_validate(ask).model_dump(mode="json"), "publish_intent": True})
    sent, started = [], []
    async def send(*args):
        sent.append(args)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_start_ask", lambda *args: started.append(args))
    with pytest.raises(ValueError, match="ask identity"):
        await hub.on_runner_message("runner2", {"type": "ask.requested", "runner_seq": 1,
                                                 "data": {**ask, "question": "different"}})
    assert sent == [] and started == []
    assert hub.store.get("ask", "a")["origin"] == "runner1"
    assert hub.store.events_since(0) == []


async def test_concurrent_runner_claims_cannot_both_cross_same_request_hold(gateway, monkeypatch):
    hub = gateway
    hub.store.put("task", "one", {"request_id": "r1"})
    hub.store.put("task", "two", {"request_id": "r1"})
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def process(_runner, msg, *args, **kwargs):
        calls.append(msg["task_id"])
        entered.set()
        await release.wait()
        raise RuntimeError("failed")
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "_on_runner_message", process)
    monkeypatch.setattr(hub, "send_runner", send)
    a = {"type": "agent.log", "runner_seq": 1, "task_id": "one", "request_id": "r1", "data": {}}
    b = {"type": "agent.log", "runner_seq": 1, "task_id": "two", "request_id": "r1", "data": {}}
    first = asyncio.create_task(hub.on_runner_message("runner1", a))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await hub.on_runner_message("runner2", b)
        assert calls == ["one"]
        assert hub.store.runner_inbox("runner2", None, 1)["state"] == "blocked"
    finally:
        release.set()
        await first


async def test_inbox_rejects_ack_when_acceptance_fails(gateway, monkeypatch):
    hub = gateway
    sent = []
    async def fail(*args):
        raise OSError("storage unavailable")
    async def send(_runner, message):
        sent.append(message)
    monkeypatch.setattr(hub.store, "aaccept_runner_inbox", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    with pytest.raises(OSError, match="storage unavailable"):
        await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1})
    assert sent == []


@pytest.mark.parametrize("after_commit", [False, True])
async def test_inbox_claim_storage_failure_never_acks_or_executes(gateway, monkeypatch, after_commit):
    hub = gateway
    original = hub.store.aset_runner_inbox_state
    sent, called = [], []
    async def fail(*args, **kwargs):
        if after_commit:
            await original(*args, **kwargs)
        raise OSError("claim failed")
    async def send(*args):
        sent.append(args)
    async def execute(*args, **kwargs):
        called.append(True)
    monkeypatch.setattr(hub.store, "aset_runner_inbox_state", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_on_runner_message", execute)
    message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "x"}}
    with pytest.raises(OSError, match="claim failed"):
        await hub.on_runner_message("runner", message)
    assert sent == [] and called == []
    assert hub.store.runner_inbox("runner", None, 1)["state"] == ("processing" if after_commit else "pending")


async def test_cancelled_inbox_commit_preserves_storage_failure_and_never_acks(gateway, monkeypatch):
    hub = gateway
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []
    async def fail(*args):
        entered.set()
        await release.wait()
        raise OSError("durable intake failed")
    async def send(*args):
        sent.append(args)
    monkeypatch.setattr(hub.store, "aaccept_runner_inbox", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    pending = asyncio.create_task(hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1}))
    await asyncio.wait_for(entered.wait(), 5)
    pending.cancel()
    release.set()
    with pytest.raises(OSError, match="durable intake failed"):
        await pending
    assert sent == []


async def test_inbox_holds_same_task_after_failure_and_duplicate_is_not_executed(gateway, monkeypatch):
    hub = gateway
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    sent = []
    calls = []
    async def fail(ev, *args, **kwargs):
        calls.append(ev["type"])
        raise RuntimeError("failure before publish")
    async def send(_runner, message):
        sent.append(message)
    monkeypatch.setattr(hub, "publish", fail)
    monkeypatch.setattr(hub, "send_runner", send)
    first = {"type": "agent.log", "runner_seq": 1, "task_id": "one", "data": {"text": "private"}}
    await hub.on_runner_message("runner", first)
    await hub.on_runner_message("runner", first)
    await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 2,
                                           "task_id": "one", "data": {"text": "dependent"}})
    assert calls == ["agent.log"]
    assert hub.store.runner_inbox("runner", None, 1)["state"] == "failed"
    assert hub.store.runner_inbox("runner", None, 2)["state"] == "blocked"
    assert sent[-1] == {"type": "runner.ack", "runner_seq": 2}
    assert not hub.store.events_since(0)


async def test_replacement_generation_does_not_inherit_inflight_old_cursor(gateway, monkeypatch):
    hub = gateway
    old, new = object(), object()
    hub.runners = {"runner": old}
    hub.runner_incarnations = {"runner": "old"}
    hub.runner_capabilities = {}
    hub.recruits_running = {}
    hub.store.runner_incarnation("runner", "old")
    entered, release = asyncio.Event(), asyncio.Event()
    original = hub.publish
    sent = []
    async def publish(ev, *args, **kwargs):
        if ev.get("runner_seq") == 8:
            entered.set()
            await release.wait()
        return await original(ev, *args, **kwargs)
    async def send(_runner, message):
        sent.append(message)
    async def register(runner, ws, agents, incarnation, capabilities, process):
        hub.store.runner_incarnation(runner, incarnation)
        hub.runners[runner] = ws
        hub.runner_incarnations[runner] = incarnation
    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_aregister_runner_locked", register)
    first = asyncio.create_task(hub.on_runner_message(
        "runner", {"type": "agent.log", "runner_seq": 8, "data": {"text": "old"}}, old, "old"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        registration = asyncio.create_task(hub.aregister_runner("runner", new, [], "new"))
        await asyncio.sleep(0)
        assert not registration.done()
        release.set()
        await first
        await registration
        await hub.on_runner_message("runner", {"type": "agent.log", "runner_seq": 1,
                                                 "data": {"text": "new"}}, new, "new")
        assert [e["data"]["text"] for e in hub.store.events_since(0)] == ["old", "new"]
        assert sent == [{"type": "runner.ack", "runner_seq": 8}, {"type": "runner.ack", "runner_seq": 1}]
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


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


def translation_hub(tmp_path):
    from labhq.settings import Settings

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.store.put("approval", "a1", {"approval": {"id": "a1"}, "origin": None})
    hub.approvals["a1"] = {"approval": {"id": "a1"}, "origin": None}
    saved = hub.store.append_event({"type": "approval.requested", "ts": 1, "data": {"id": "a1", "summary": "x"}}, 100)
    return hub, saved


async def test_translation_result_is_one_commit_with_its_display_and_approval_copy(tmp_path, monkeypatch):
    """#1132: the translation result, the message's display and the approval card's copy are saved together, then the
    `messenger.translated` event: two commits, where it was one per write."""
    hub, saved = translation_hub(tmp_path)
    commits = []  # real COMMIT statements: writes inside a batch join one transaction
    hub.store._write_connection().set_trace_callback(
        lambda sql: commits.append(sql) if sql.strip().upper().startswith("COMMIT") else None)
    try:
        await hub.pi_interpreter._interpret(saved, "source text")  # no request: the failed display is written
        assert len(commits) == 2
        key = str(saved["seq"])
        display = hub.store.get("pi_translation", key)
        assert display["status"] == "failed"
        assert hub.store.get("approval", "a1")["approval"]["pi_display"] == display
        assert hub.store.events_since(0)[0]["data"]["pi_display"] == display
    finally:
        hub.store.close()


async def test_a_failed_translation_commit_leaves_no_part_of_the_result(tmp_path, monkeypatch):
    """#1132: all or none. Before, a failure after the first write left the translation saved and the card not."""
    hub, saved = translation_hub(tmp_path)
    transaction = hub.store._transaction
    writes = 0

    @contextmanager
    def failing():
        nonlocal writes
        writes += 1
        with transaction() as conn:
            yield conn
            if writes == 2:  # the second write of the result: the first must not stay behind
                raise OSError("write failed")

    monkeypatch.setattr(hub.store, "_transaction", failing)
    try:
        with pytest.raises(OSError):
            await hub.pi_interpreter._interpret(saved, "source text")
        monkeypatch.setattr(hub.store, "_transaction", transaction)
        assert hub.store.get("pi_translation", str(saved["seq"])) is None
        assert "pi_display" not in hub.store.get("approval", "a1")["approval"]
        assert "pi_display" not in hub.store.events_since(0)[0]["data"]
    finally:
        hub.store.close()


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
