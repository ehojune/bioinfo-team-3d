"""Restart and reconnect contracts for the gateway and runner."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, create_app
from labhq.models import ApprovalRequest, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.store import StateStore


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.hpc.scheduler = "mock"
    return s


@pytest.mark.asyncio
async def test_restart_keeps_approval_and_resumes_only_remaining_steps(tmp_path):
    s = settings(tmp_path)
    h1 = Hub(s)
    finished = TaskResult(task_id="t1", agent_id="a", ok=True, text="saved")
    h1.requests["r1"] = {"id": "r1", "text": "study", "mode": "orchestrate", "status": "running",
                         "plan": {"steps": [{"id": "s1", "agent_id": "a", "instruction": "first", "depends_on": []},
                                            {"id": "s2", "agent_id": "a", "instruction": "second", "depends_on": ["s1"]}]},
                         "results": {"s1": finished.model_dump(mode="json")}}
    h1.save_request("r1")
    pending = ApprovalRequest(kind="tool_permission", summary="approve", request_id="r1")
    h1.approvals[pending.id] = {"approval": pending.model_dump(mode="json"), "origin": "runner1"}
    h1.save_approval(pending.id)

    app = create_app(s)
    hub = app.state.hub
    assert hub.requests["r1"]["status"] == "interrupted"
    assert hub.requests["r1"]["results"]["s1"]["text"] == "saved"
    resume_id = next(aid for aid, e in hub.approvals.items() if e["approval"]["kind"] == "resume")
    called = []

    async def fake_step(task):
        called.append(task.meta.get("step_id", task.meta["kind"]))
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="new")

    hub.orchestrator.run_step = fake_step
    await hub.resolve_approval(resume_id, True)
    for _ in range(100):
        if hub.requests["r1"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert hub.requests["r1"]["status"] == "done"
    assert called == ["s2", "synthesis"]
    assert hub.requests["r1"]["results"]["s1"]["text"] == "saved"
    assert "s2" in Hub(s).requests["r1"]["results"]
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {s.gateway.client_token}"}
        assert client.post(f"/api/approvals/{pending.id}", headers=headers,
                           json={"approved": False, "note": "later"}).status_code == 200
        assert pending.id not in Hub(s).approvals
    assert hub.store.all("approval_decision")[pending.id]["note"] == "later"
    assert pending.id in hub.store.all("decision")

    class Socket:
        def __init__(self):
            self.sent = []
        async def send_text(self, body):
            self.sent.append(json.loads(body))

    socket = Socket()
    hub.register_runner("runner1", socket, [])
    await hub.flush_decisions("runner1")
    assert socket.sent[0]["id"] == pending.id
    await hub.on_runner_message("runner1", {"type": "approval.ack", "id": pending.id, "runner_seq": 1})
    assert pending.id not in hub.store.all("decision")


@pytest.mark.asyncio
async def test_seq_replay_boundary_and_retention(tmp_path):
    s = settings(tmp_path)
    s.gateway.event_buffer = 3
    hub = Hub(s)
    await hub.publish({"type": "one"})
    await hub.publish({"type": "two"})
    app = create_app(s)
    h = app.state.hub
    await h.publish({"type": "three"})
    assert [e["seq"] for e in h.events] == [1, 2, 3]
    assert all(e["schema_version"] == 1 for e in h.events)
    with TestClient(app) as client:
        token = s.gateway.client_token
        headers = {"Authorization": f"Bearer {token}"}
        with client.websocket_connect(f"/ws/client?token={token}&since=2") as ws:
            assert json.loads(ws.receive_text())["seq"] == 3
            await h.publish({"type": "four"})
            assert json.loads(ws.receive_text())["seq"] == 4
        assert [e["seq"] for e in client.get("/api/events?since=2", headers=headers).json()] == [3, 4]
        await h.publish({"type": "five"})
        gap = client.get("/api/events?since=1", headers=headers).json()[0]
        assert gap["type"] == "snapshot" and gap["replay_gap"]["oldest_seq"] == 3
        with client.websocket_connect(f"/ws/client?token={token}&since=1") as ws:
            assert json.loads(ws.receive_text())["replay_gap"]["oldest_seq"] == 3


@pytest.mark.asyncio
async def test_runner_job_recovery_outbox_and_gateway_dedup(tmp_path):
    s = settings(tmp_path)
    runner = Runner(s)
    runner.task_req["t1"] = "r1"
    await runner._on_track({"job_id": "42", "task_id": "t1", "agent_id": "a", "name": "job"})
    restored = Runner(s)
    assert restored.jobs["42"]["scheduler"] == "mock"
    await restored._poll_jobs()
    assert restored.jobs["42"]["state"] == "completed"
    assert any(e["type"] == "jobs.finished" for e in restored.store.pending())

    class OutboundSocket:
        def __init__(self):
            self.sent = []
        async def send(self, body):
            self.sent.append(json.loads(body))

    outbound = OutboundSocket()
    restored.reload_outbox()
    sender = asyncio.create_task(restored._sender(outbound))
    for _ in range(100):
        if len(outbound.sent) == len(restored.store.pending()):
            break
        await asyncio.sleep(0.01)
    sender.cancel()
    assert [e["runner_seq"] for e in outbound.sent] == [e["runner_seq"] for e in restored.store.pending()]

    class Socket:
        def __init__(self):
            self.sent = []
        async def send_text(self, body):
            self.sent.append(json.loads(body))

    gateway = Hub(s)
    socket = Socket()
    gateway.register_runner("local", socket, [])
    for ev in restored.store.pending():
        await gateway.on_runner_message("local", ev)
        await gateway.on_runner_message("local", ev)
    assert len([e for e in gateway.events if e["type"] == "jobs.finished"]) == 1
    restarted_gateway = Hub(s)
    restarted_gateway.register_runner("local", socket, [])
    await restarted_gateway.on_runner_message("local", restored.store.pending()[-1])
    assert len([e for e in restarted_gateway.events if e["type"] == "jobs.finished"]) == 1
    assert any(m["type"] == "runner.ack" for m in socket.sent)
    for msg in socket.sent:
        if msg["type"] == "runner.ack":
            await restored._on_message(msg)
    assert restored.store.pending() == []


def test_unknown_schema_fails_closed(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.db.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="unsupported state schema"):
        StateStore(tmp_path / "state.sqlite3")


def test_runner_outbox_is_bounded(tmp_path):
    s = settings(tmp_path)
    s.runner.outbox_limit = 2
    runner = Runner(s)
    for i in range(3):
        runner.send({"type": "agent.log", "data": {"n": i}})
    assert [e["data"]["n"] for e in runner.store.pending()] == [1, 2]
