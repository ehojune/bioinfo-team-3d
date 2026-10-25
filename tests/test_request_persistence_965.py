"""Request/result helpers settle durable writes without blocking the loop."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from labhq.gateway.server import Hub
from labhq.models import TaskResult
from labhq.settings import Settings
from labhq.store import StateStore
from tests.test_gateway_publish_durable import stall_commit, until_entered


@pytest.fixture
def hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    gateway = Hub(settings)
    gateway.requests["r"] = {"id": "r", "status": "running", "results": {}}
    gateway.save_request("r")
    async def schedule(rid):
        pass
    gateway.result_storage = SimpleNamespace(schedule=lambda rid: None, aschedule=schedule)
    yield gateway
    gateway.store.close()


def result(text="new"):
    return TaskResult(task_id="t", agent_id="worker", ok=True, text=text)


@pytest.mark.parametrize("operation", ["save", "add", "pop", "delete", "review"])
async def test_slow_helper_commit_keeps_heartbeat_and_defers_memory_changes(hub, monkeypatch, operation):
    hub.requests["r"]["status"] = "done" if operation == "review" else "running"
    hub.requests["r"]["results"] = {"s": result("old").model_dump(mode="json")}
    hub.save_request("r")
    mapping = hub.result_map("r")
    before = copy.deepcopy(hub.requests["r"])
    entered, release = stall_commit(hub, monkeypatch)
    calls = {"save": lambda: hub.asave_request("r"), "add": lambda: mapping.aset("s", result()),
             "pop": lambda: mapping.apop("s"), "delete": lambda: mapping.adelete("s"),
             "review": lambda: hub.areview_done("r")}
    task = asyncio.create_task(calls[operation]())
    try:
        await until_entered(entered)
        heartbeat = []
        asyncio.get_running_loop().call_soon(heartbeat.append, True)
        await asyncio.sleep(0.01)
        assert heartbeat == [True] and not task.done()
        assert hub.requests["r"] == before
        assert hub.store.get("request", "r") == before
        assert mapping["s"].text == "old"
    finally:
        release.set()
        await task
    if operation == "review":
        assert hub.requests["r"]["pi_reviewed_at"] > 0
        await hub._send_committed_event()
    elif operation in {"pop", "delete"}:
        assert "s" not in mapping and "s" not in hub.requests["r"]["results"]
    elif operation == "add":
        assert mapping["s"].text == "new"
    assert hub.store.get("request", "r") == hub.requests["r"]


@pytest.mark.parametrize("operation", ["save", "add", "pop", "review"])
async def test_failed_helper_does_not_publish_or_advance_memory(hub, monkeypatch, operation):
    hub.requests["r"].update(status="done", results={"s": result("old").model_dump(mode="json")},
                             pending_revisions={"s": {"revision": 1}})
    hub.save_request("r")
    before = copy.deepcopy(hub.requests["r"])
    mapping = hub.result_map("r")
    if operation == "save":
        hub.requests["r"]["unsaved"] = "must roll back"
    entered, release = stall_commit(hub, monkeypatch, fail=True)
    calls = {"save": lambda: hub.asave_request("r"), "add": lambda: mapping.aset("s", result()),
             "pop": lambda: mapping.apop("s"), "review": lambda: hub.areview_done("r")}
    task = asyncio.create_task(calls[operation]())
    try:
        await until_entered(entered)
    finally:
        release.set()
    with pytest.raises(OSError, match="commit failed"):
        await task
    assert hub.requests["r"] == before
    assert hub.store.get("request", "r") == before
    assert mapping["s"].text == "old"
    assert not hub.events and not hub.pending_committed


@pytest.mark.parametrize("operation", ["add", "pop", "review"])
async def test_repeated_cancel_still_applies_known_commit_and_restart_state(hub, monkeypatch, operation):
    hub.requests["r"].update(status="done", results={"s": result("old").model_dump(mode="json")})
    hub.save_request("r")
    mapping = hub.result_map("r")
    entered, release = stall_commit(hub, monkeypatch)
    calls = {"add": lambda: mapping.aset("s", result()), "pop": lambda: mapping.apop("s"),
             "review": lambda: hub.areview_done("r")}
    task = asyncio.create_task(calls[operation]())
    try:
        await until_entered(entered)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    reopened = StateStore(hub.store.path)
    try:
        assert reopened.get("request", "r") == hub.requests["r"]
        if operation == "pop":
            assert "s" not in mapping and "s" not in reopened.get("request", "r")["results"]
        elif operation == "add":
            assert mapping["s"].text == reopened.get("request", "r")["results"]["s"]["text"] == "new"
        else:
            assert reopened.get("request", "r")["pi_reviewed_at"] > 0
            assert len(reopened.events_since(0)) == 1
    finally:
        reopened.close()
    await hub._send_committed_event()


async def test_review_result_and_later_save_keep_all_fields_in_fifo_order(hub, monkeypatch):
    hub.requests["r"]["status"] = "done"
    hub.save_request("r")
    entered, release = stall_commit(hub, monkeypatch)
    review = asyncio.create_task(hub.areview_done("r"))
    mapping = hub.result_map("r")
    try:
        await until_entered(entered)
        saved = asyncio.create_task(mapping.aset("s", result()))
        hub.requests["r"]["note"] = "written during review"
        latest = asyncio.create_task(hub.asave_request("r"))
        await asyncio.sleep(0.01)
        assert not saved.done() and not latest.done()
    finally:
        release.set()
        await review
    await asyncio.gather(saved, latest)
    row = hub.store.get("request", "r")
    assert row == hub.requests["r"]
    assert row["note"] == "written during review" and row["pi_reviewed_at"] > 0
    assert row["results"]["s"]["text"] == "new"
    await hub._send_committed_event()


async def test_failed_earlier_save_does_not_erase_later_caller_changes(hub, monkeypatch):
    hub.requests["r"]["note"] = "first"
    entered, release = stall_commit(hub, monkeypatch, fail=True)
    first = asyncio.create_task(hub.asave_request("r"))
    try:
        await until_entered(entered)
        hub.requests["r"]["note"] = "second"
        second = asyncio.create_task(hub.asave_request("r"))
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(OSError):
        await first
    await second
    assert hub.store.get("request", "r")["note"] == hub.requests["r"]["note"] == "second"


async def test_result_payload_is_detached_from_caller_during_commit(hub, monkeypatch):
    entered, release = stall_commit(hub, monkeypatch)
    value = result()
    mapping = hub.result_map("r")
    task = asyncio.create_task(mapping.aset("s", value))
    try:
        await until_entered(entered)
        value.text = "changed by caller"
    finally:
        release.set()
        await task
    assert mapping["s"].text == hub.store.get("request", "r")["results"]["s"]["text"] == "new"


@pytest.mark.parametrize("decision,status", [("accepted", "done"), ("rejected", "rejected"),
                                             ("timed_out", "failed"), ("cancelled", "cancelled")])
async def test_async_final_receipt_survives_cancel_and_reopen(hub, monkeypatch, decision, status):
    from tests.test_research_cp4 import draft_hub, target
    fake = await draft_hub()
    hub.requests["r"] = copy.deepcopy(fake.requests["r"])
    hub.save_request("r")
    before = copy.deepcopy(hub.requests["r"])
    entered, release = stall_commit(hub, monkeypatch)
    task = asyncio.create_task(hub._afinal_review("r", decision, **target(before)))
    try:
        await until_entered(entered)
        assert hub.requests["r"] == before
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hub.requests["r"]["status"] == status
    reopened = StateStore(hub.store.path)
    try:
        row = reopened.get("request", "r")
        assert row == hub.requests["r"]
        assert row["research_contract"]["final_receipt"]["decision"] == decision
        assert len(reopened.events_since(0)) == 1
    finally:
        reopened.close()
    await hub._send_committed_event()


@pytest.mark.parametrize("fail", [False, True])
async def test_terminal_snapshot_commits_before_state_and_delivery(hub, monkeypatch, fail):
    before = copy.deepcopy(hub.requests["r"])
    proposed = {**before, "status": "done", "report": "complete"}
    entered, release = stall_commit(hub, monkeypatch, fail=fail)
    task = asyncio.create_task(hub.aschedule_terminal("r", "request.completed", {"ok": True},
                                                   request=proposed, before=before))
    try:
        await until_entered(entered)
        assert hub.requests["r"] == before and not hub.events
        assert not task.done()
        await asyncio.sleep(0.01)
    finally:
        release.set()
    if fail:
        with pytest.raises(OSError):
            await task
        assert hub.requests["r"] == before and not hub.events
    else:
        await task
        assert hub.requests["r"]["status"] == "done"
        assert hub.store.get("request", "r") == hub.requests["r"]
        assert [event["type"] for event in hub.events] == ["request.completed"]
        await hub._send_committed_event()


async def test_async_hold_removal_is_not_visible_before_commit(hub, monkeypatch):
    hub.requests["r"].update(status="waiting_for_quota", quota_waits={"s": {"engine": "codex"}})
    hub.save_request("r")
    entered, release = stall_commit(hub, monkeypatch)
    task = asyncio.create_task(hub._aremove_quota_wait("r", "s"))
    try:
        await until_entered(entered)
        assert hub.requests["r"]["quota_waits"] and hub.requests["r"]["status"] == "waiting_for_quota"
    finally:
        release.set()
        await task
    assert "quota_waits" not in hub.requests["r"]
    assert hub.store.get("request", "r") == hub.requests["r"]
