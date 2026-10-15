"""Runner-only shutdown and replay must reach a live gateway's resume card."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from labhq.runner.daemon import Runner
from tests.test_cso import FakeHub
from tests.test_shutdown_interrupted import make_hub


def resume_cards(hub):
    return [e["approval"] for e in hub.approvals.values()
            if e["approval"]["kind"] == "resume"]


def runner_for(hub, tmp_path):
    hub.s.runner.state_dir = str(tmp_path / "runner")
    return Runner(hub.s)


async def run_request_with_result(hub, result, monkeypatch):
    hub.s.orchestrator.chief_of_staff_agent = None
    hub.agents = FakeHub(None).agents
    hub.requests["r"].update(status="running", budget_usd=10)

    async def dispatch(task):
        return result.model_copy(update={"task_id": task.id, "agent_id": task.agent_id})

    monkeypatch.setattr(hub, "dispatch", dispatch)
    await hub.orchestrator.run_request("r")


@pytest.mark.parametrize("stop", [True, False])
async def test_guard_cancel_outbox_and_replay(tmp_path, monkeypatch, stop):
    hub = make_hub(tmp_path)
    runner = runner_for(hub, tmp_path)
    task = Task(id="cancelled-task", agent_id="cso", request_id="r", prompt="plan")
    started = asyncio.Event()

    async def run(_task):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "run_task", run)
    runner.tasks[task.id] = asyncio.create_task(runner._run_guarded(task))
    await asyncio.wait_for(started.wait(), 5)
    if stop:
        runner.stop()
    else:
        runner.tasks[task.id].cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner.tasks[task.id]
    events = [e for e in runner.store.pending() if e["type"] == "task.result"]
    assert len(events) == 1
    result = TaskResult.model_validate(events[0]["data"])
    assert bool(result.shutdown_evidence) is stop
    restarted = Runner(hub.s)
    replay = [e for e in restarted.store.pending() if e["type"] == "task.result"]
    assert replay == events
    if stop:
        await run_request_with_result(hub, result, monkeypatch)
        assert hub.requests["r"]["status"] == "interrupted"
        assert len(resume_cards(hub)) == 1


async def test_recovered_accepted_task_interrupts_live_gateway(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    runner = runner_for(hub, tmp_path)
    runner.store.put("accepted_task", "lost", {"state": "running",
        "task": {"agent_id": "cso", "request_id": "r"}})
    restarted = Runner(hub.s)
    events = [e for e in restarted.store.pending() if e["type"] == "task.result"]
    assert len(events) == 1
    recovered = TaskResult.model_validate(events[0]["data"])
    assert recovered.shutdown_evidence
    await run_request_with_result(hub, recovered, monkeypatch)
    assert hub.requests["r"]["status"] == "interrupted"
    assert len(resume_cards(hub)) == 1
    assert Runner(hub.s).store.pending() == restarted.store.pending()


async def test_live_shutdown_publishes_one_resume_card(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    client = AsyncMock()
    hub.clients.add(client)
    failed = TaskResult(task_id="t", agent_id="cso", ok=False, exit_code=143,
        error="exit 143", shutdown_evidence={"exit_code": 143, "stopping": True})
    await run_request_with_result(hub, failed, monkeypatch)
    assert not hub.stopping
    assert hub.store.get("request", "r")["status"] == "interrupted"
    assert len(resume_cards(hub)) == 1
    first_id = resume_cards(hub)[0]["id"]
    hub.interrupt_request("r", evidence=failed.shutdown_evidence)
    await hub._send_committed_event()
    assert [c["id"] for c in resume_cards(hub)] == [first_id]
    sent = [json.loads(call.args[0]) for call in client.send_text.await_args_list]
    assert len([e for e in sent if e["type"] == "approval.requested"
                and e["data"]["kind"] == "resume"]) == 1
    assert len([e for e in hub.store.events_since(0) if e["type"] == "request.status"
                and e["data"].get("status") == "interrupted"]) == 1


def test_startup_uses_common_interruption_and_keeps_card(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    hub.requests["r"]["status"] = "running"
    hub.save_request("r")
    calls = []
    original = Hub.interrupt_request

    def record(self, rid, **kwargs):
        calls.append(rid)
        return original(self, rid, **kwargs)

    monkeypatch.setattr(Hub, "interrupt_request", record)
    restarted = Hub(hub.s)
    assert calls and set(calls) == {"r"}
    assert restarted.requests["r"]["status"] == "interrupted"
    cards = resume_cards(restarted)
    assert len(cards) == 1
    again = Hub(hub.s)
    assert resume_cards(again) == cards


def test_recovery_keeps_submitted_jobs(tmp_path):
    runner = runner_for(make_hub(tmp_path), tmp_path)
    runner.jobs["job"] = {"task_id": "submitted", "job_id": "job"}
    result = runner._interrupted_result("submitted", "cso")
    assert result.ok and result.pending_jobs == ["job"]
    assert not result.shutdown_evidence
