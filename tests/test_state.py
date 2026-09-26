"""Restart and reconnect contracts for the gateway and runner."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, create_app
from labhq.models import ApprovalRequest, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import ProjectSettings, Settings
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
                         "cost_usd": 2.5,
                         "plan": {"steps": [{"id": "s1", "agent_id": "a", "instruction": "first", "depends_on": []},
                                            {"id": "s2", "agent_id": "a", "instruction": "second", "depends_on": ["s1"]}]},
                         "results": {"s1": finished.model_dump(mode="json")}}
    h1.save_request("r1")
    pending = ApprovalRequest(kind="tool_permission", summary="approve", request_id="r1")
    h1.approvals[pending.id] = {"approval": pending.model_dump(mode="json"), "origin": "runner1"}
    h1.save_approval(pending.id)
    h1.store.close()

    app = create_app(s)
    hub = app.state.hub
    assert hub.requests["r1"]["status"] == "interrupted"
    assert hub.requests["r1"]["results"]["s1"]["text"] == "saved"
    resume_id = next(aid for aid, e in hub.approvals.items() if e["approval"]["kind"] == "resume")
    called = []

    class Socket:
        def __init__(self):
            self.sent = []
        async def send_text(self, body):
            self.sent.append(json.loads(body))

    socket = Socket()
    hub.register_runner("runner1", socket, [{"id": aid} for aid in ("a", "cso", "sci_reviewer")])

    async def fake_step(task):
        called.append(task.meta.get("step_id", task.meta["kind"]))
        if task.meta["kind"] == "review":
            revise = task.meta["revision"] == 0
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"verdict": "revise" if revise else "accept", "scores": {},
                                          "issues": [{"step_id": "s2", "problem": "check", "request": "revise"}]
                                          if revise else []})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="new")

    hub.orchestrator.run_step = fake_step
    await hub.resolve_approval(resume_id, True)
    for _ in range(100):
        if hub.requests["r1"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert hub.requests["r1"]["status"] == "done"
    assert called == ["s2", "review", "s2", "review", "synthesis"]
    assert hub.requests["r1"]["cost_usd"] == 2.5
    assert hub.orchestrator.cost["r1"] == 2.5
    assert hub.requests["r1"]["results"]["s1"]["text"] == "saved"
    assert "s2" in hub.store.all("request")["r1"]["results"]
    hub.unregister_runner("runner1", socket)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {s.gateway.client_token}"}
        assert client.post(f"/api/approvals/{pending.id}", headers=headers,
                           json={"approved": False, "note": "later"}).status_code == 200
        assert pending.id not in hub.store.all("approval")
    assert hub.store.all("approval_decision")[pending.id]["note"] == "later"
    assert pending.id in hub.store.all("decision")

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
    hub.store.close()
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
    runner.store.close()
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
    gateway.store.close()
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
    store.close()
    with pytest.raises(ValueError, match="unsupported state schema"):
        StateStore(tmp_path / "state.sqlite3")


def test_runner_outbox_is_bounded(tmp_path):
    s = settings(tmp_path)
    s.runner.outbox_limit = 2
    runner = Runner(s)
    for i in range(3):
        runner.send({"type": "agent.log", "data": {"n": i}})
    assert [e["data"]["n"] for e in runner.store.pending()] == [1, 2]


class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        self.sent.append(json.loads(body))


@pytest.mark.asyncio
async def test_new_runner_incarnation_accepts_reset_sequence(tmp_path):
    s = settings(tmp_path)
    first = Runner(s)
    incarnation = first.hello()["incarnation"]
    first.store.close()
    first = Runner(s)
    assert incarnation == first.incarnation
    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [], first.incarnation)
    result1 = TaskResult(task_id="t1", agent_id="a", ok=True)
    fut1 = asyncio.get_running_loop().create_future()
    hub.futures["t1"] = fut1
    ev1 = first.store.enqueue({"type": "task.result", "task_id": "t1",
                               "data": result1.model_dump(mode="json")}, 10)
    await hub.on_runner_message("local", ev1)
    assert fut1.done() and hub.store.runner_seen("local") == 1

    replacement_settings = settings(tmp_path)
    replacement_settings.runner.state_dir = str(tmp_path / "replacement")
    replacement = Runner(replacement_settings)
    assert replacement.incarnation != first.incarnation
    hub.register_runner("local", socket, [], replacement.hello()["incarnation"])
    assert hub.store.runner_seen("local") == 0
    result2 = TaskResult(task_id="t2", agent_id="a", ok=True)
    fut2 = asyncio.get_running_loop().create_future()
    hub.futures["t2"] = fut2
    ev2 = replacement.store.enqueue({"type": "task.result", "task_id": "t2",
                                     "data": result2.model_dump(mode="json")}, 10)
    await hub.on_runner_message("local", ev2)
    assert fut2.done() and hub.store.runner_seen("local") == 1


@pytest.mark.asyncio
async def test_task_cost_survives_restart_once_per_task(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    hub.requests["r1"] = {"id": "r1", "status": "running", "mode": "direct", "text": "work",
                           "agent_id": "a"}
    hub.save_request("r1")
    socket = CaptureSocket()
    hub.register_runner("local", socket, [], "generation-1")
    msg = {"type": "task.result", "runner_seq": 1, "request_id": "r1", "task_id": "t1",
           "data": TaskResult(task_id="t1", agent_id="a", ok=True, cost_usd=2.5).model_dump(mode="json")}
    await hub.on_runner_message("local", msg)
    assert hub.requests["r1"]["cost_usd"] == 2.5
    hub.store.close()
    restarted = Hub(s)
    restarted.register_runner("local", socket, [], "generation-1")
    await restarted.on_runner_message("local", msg)
    assert restarted.requests["r1"]["cost_usd"] == 2.5
    await restarted.on_runner_message("local", {**msg, "runner_seq": 2})
    assert restarted.requests["r1"]["cost_usd"] == 2.5
    assert restarted.requests["r1"]["cost_by_task"] == {"t1": 2.5}


@pytest.mark.asyncio
async def test_resume_waits_for_runner_and_reoffers_after_timeout(tmp_path):
    s = settings(tmp_path)
    s.gateway.resume_wait_s = 0.15
    first = Hub(s)
    first.requests["r1"] = {"id": "r1", "status": "running", "mode": "direct",
                            "agent_id": "a", "text": "work"}
    first.save_request("r1")
    first.store.close()
    hub = Hub(s)
    aid = next(iter(hub.approvals))
    calls = []

    async def fake_step(task):
        calls.append(task.agent_id)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    hub.orchestrator.run_step = fake_step
    await hub.resolve_approval(aid, True)
    await asyncio.sleep(0.03)
    assert hub.requests["r1"]["status"] == "waiting_for_runner" and calls == []
    assert any(e["type"] == "request.resume_waiting" for e in hub.events)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}])
    for _ in range(50):
        if hub.requests["r1"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert hub.requests["r1"]["status"] == "done" and calls == ["a"]

    hub.requests["r2"] = {"id": "r2", "status": "running", "mode": "direct",
                            "agent_id": "missing", "text": "work"}
    hub.save_request("r2")
    hub.store.close()
    timeout_hub = Hub(s)
    timeout_aid = next(aid for aid, e in timeout_hub.approvals.items()
                       if e["approval"].get("request_id") == "r2")
    await timeout_hub.resolve_approval(timeout_aid, True)
    for _ in range(50):
        if any(e["type"] == "request.resume_timeout" for e in timeout_hub.events):
            break
        await asyncio.sleep(0.01)
    assert timeout_hub.requests["r2"]["status"] == "interrupted"
    assert any(e["approval"].get("request_id") == "r2" and e["approval"]["kind"] == "resume"
               for e in timeout_hub.approvals.values())


def test_gateway_local_approval_expires_on_restart(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    old = ApprovalRequest(kind="budget", summary="extra budget", request_id="r1")
    first.approvals[old.id] = {"approval": old.model_dump(mode="json"), "origin": None}
    first.save_approval(old.id)
    first.store.close()
    restored = Hub(s)
    assert old.id not in restored.approvals
    assert restored.store.all("approval_decision")[old.id]["state"] == "expired"
    assert any(e["type"] == "approval.expired" and e["data"]["id"] == old.id
               for e in restored.events)


@pytest.mark.asyncio
async def test_github_issue_number_restored_for_final_report(tmp_path):
    s = settings(tmp_path)
    s.projects = [ProjectSettings(id="p", repo="o/p", commit_reports=False)]
    first = Hub(s)
    first.requests["r1"] = {"id": "r1", "text": "study", "mode": "direct",
                            "project_id": "p", "status": "running"}
    first.save_request("r1")

    class GitHub:
        def __init__(self):
            self.calls = []
        async def create_issue(self, *args):
            self.calls.append("create")
            return {"number": 7, "html_url": "https://example.test/7"}
        async def comment(self, repo, number, body):
            self.calls.append(("comment", number, body))
            return {"html_url": "https://example.test/7#comment"}
        async def close_issue(self, repo, number):
            self.calls.append(("close", number))

    gh = GitHub()
    first.reporter.client = lambda: gh
    await first.reporter.handle({"type": "request.created", "request_id": "r1", "data": {}})
    assert first.store.all("github_issue")["r1"]["number"] == 7
    first.store.close()
    restored = Hub(s)
    assert restored.reporter.issues["r1"] == 7
    restored.reporter.client = lambda: gh
    await restored.reporter.handle({"type": "request.completed", "request_id": "r1",
                                   "data": {"ok": True, "report": "finished"}})
    assert ("close", 7) in gh.calls
    assert any(c[0] == "comment" and c[1] == 7 for c in gh.calls if isinstance(c, tuple))
