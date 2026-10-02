"""Restart and reconnect contracts for the gateway and runner."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, SavedResults, create_app
from labhq.models import ApprovalRequest, Task, TaskResult
from labhq.orchestrator.cso import failure_kind
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
                structured={"verdict": "revise" if revise else "accept",
                            "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
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
    await runner._track_job({"job_id": "42", "task_id": "t1", "agent_id": "a", "name": "job"})
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


def test_runner_outbox_preserves_terminal_and_control_events(tmp_path):
    s = settings(tmp_path)
    s.runner.outbox_limit = 2
    runner = Runner(s)
    runner.send({"type": "task.result", "task_id": "t", "data": {"ok": True}})
    runner.send({"type": "agent.log", "data": {"n": 1}})
    runner.send({"type": "approval.requested", "data": {"id": "a"}})
    runner.send({"type": "agent.log", "data": {"n": 2}})
    runner.send({"type": "jobs.finished", "task_id": "t", "data": {"jobs": []}})
    assert [e["type"] for e in runner.store.pending()] == [
        "task.result", "approval.requested", "jobs.finished"]
    runner.reload_outbox()
    assert [json.loads(runner.outbox.get_nowait())["type"] for _ in range(runner.outbox.qsize())] == [
        "task.result", "approval.requested", "jobs.finished"]


def test_roster_update_is_not_lost_when_another_event_is_queued(tmp_path):
    runner = Runner(settings(tmp_path))
    runner.send({"type": "runner.roster", "agents": [{"id": "new"}]})
    runner.send({"type": "agent.log", "data": {"message": "ready"}})
    assert [json.loads(runner.outbox.get_nowait())["type"] for _ in range(runner.outbox.qsize())] == [
        "runner.roster", "agent.log"]


class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        self.sent.append(json.loads(body))


@pytest.mark.asyncio
async def test_late_result_becomes_request_checkpoint_before_resume(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "text": "study", "mode": "orchestrate", "status": "running",
                           "plan": {"steps": [{"id": "s1", "agent_id": "worker", "instruction": "analyze",
                                               "depends_on": []}]}}
    first.save_request("r")
    socket = CaptureSocket()
    first.register_runner("local", socket, [{"id": "worker"}], "inc-1")
    task = Task(id="task-1", agent_id="worker", request_id="r", prompt="analyze",
                meta={"kind": "step", "step_id": "s1"})
    pending = asyncio.create_task(first.dispatch(task))
    await asyncio.sleep(0)
    assert first.store.get("task", "task-1")["step_id"] == "s1"
    pending.cancel()
    try:
        await pending
    except asyncio.CancelledError:
        pass
    first.store.close()

    restored = Hub(s)
    restored.register_runner("local", socket, [{"id": a} for a in ("worker", "cso", "sci_reviewer")], "inc-1")
    result = TaskResult(task_id="task-1", agent_id="worker", ok=True, text="finished", cost_usd=1.25)
    await restored.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                                "request_id": "r", "task_id": "task-1",
                                                "data": result.model_dump(mode="json")})
    assert "s1" not in restored.requests["r"].get("results", {})
    assert restored.store.get("task", "task-1")["result"]["text"] == "finished"
    assert restored.requests["r"]["cost_usd"] == 1.25
    calls = []
    run_step = restored.orchestrator.run_step

    async def fake_step(t):
        calls.append(t.meta["kind"])
        if t.meta["kind"] == "step":
            return await run_step(t)
        if t.meta["kind"] == "review":
            return TaskResult(task_id=t.id, agent_id=t.agent_id, ok=True,
                              structured={"verdict": "accept", "scores": {
                                  "addresses_question": 4, "evidence": 4, "thoroughness": 4}, "issues": []})
        return TaskResult(task_id=t.id, agent_id=t.agent_id, ok=True, text="report")

    restored.orchestrator.run_step = fake_step
    aid = next(aid for aid, entry in restored.approvals.items() if entry["approval"]["kind"] == "resume")
    await restored.resolve_approval(aid, True)
    for _ in range(100):
        if restored.requests["r"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert restored.requests["r"]["status"] == "done"
    assert calls == ["step", "review", "synthesis"]
    assert restored.requests["r"]["cost_usd"] == 1.25


@pytest.mark.asyncio
async def test_hpc_submission_checkpoint_wakes_without_reissuing_step(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running",
                           "plan": {"steps": [{"id": "s", "agent_id": "a", "depends_on": []}]}}
    first.save_request("r")
    first.store.put("task", "submitted", {"request_id": "r", "step_id": "s", "kind": "step"})
    first.store.close()
    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    submitted = TaskResult(task_id="submitted", agent_id="a", ok=True, text="submitted",
                           pending_jobs=["42"], workdir="work", cost_usd=0.5)
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                          "request_id": "r", "task_id": "submitted",
                                          "data": submitted.model_dump(mode="json")})
    await hub.on_runner_message("local", {"type": "jobs.finished", "runner_seq": 2,
                                          "request_id": "r", "task_id": "submitted",
                                          "data": {"jobs": [{"job_id": "42", "state": "completed"}]}})
    hub.recovery_steps.add("r")
    recovered = await hub.dispatch(Task(agent_id="a", request_id="r", prompt="analyze",
                                        meta={"kind": "step", "step_id": "s"}))
    assert recovered.task_id == "submitted" and recovered.pending_jobs == ["42"]
    assert recovered.cost_usd == 0.0 and hub.requests["r"]["cost_usd"] == 0.5
    assert await hub.wait_jobs("submitted") == {"jobs": [{"job_id": "42", "state": "completed"}]}
    assert not any(m.get("type") == "task.dispatch" for m in socket.sent)


@pytest.mark.asyncio
async def test_hpc_completion_survives_crash_between_wait_and_wake(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    submitted = TaskResult(task_id="submitted", agent_id="a", ok=True, text="submitted",
                           pending_jobs=["42"], workdir="work")
    first.store.put("task", "submitted", {"request_id": "r", "step_id": "s", "kind": "step",
                                            "completed": True, "result": submitted.model_dump(mode="json")})
    completion = {"jobs": [{"job_id": "42", "state": "completed"}]}
    first.store.put("jobs_done", "submitted", completion)
    first.jobs_done["submitted"] = completion
    assert await first.wait_jobs("submitted") == completion
    assert first.store.get("jobs_done", "submitted") == completion
    first.store.close()  # the gateway stops before the wake task is dispatched

    restored = Hub(s)
    restored.recovery_steps.add("r")
    real_dispatch = restored.dispatch
    calls = []

    async def dispatch(task):
        calls.append(task.meta.get("parent_task"))
        if task.meta.get("parent_task") == "submitted":
            return TaskResult(task_id=task.id, agent_id="a", ok=True, text="wake complete")
        return await real_dispatch(task)

    restored.dispatch = dispatch
    result = await asyncio.wait_for(restored.orchestrator.run_step(Task(
        agent_id="a", request_id="r", prompt="analyze", meta={"kind": "step", "step_id": "s"})), 1)
    assert result.text == "wake complete" and calls == [None, "submitted"]
    assert restored.store.get("jobs_done", "submitted") == completion
    restored.result_map("r")["s"] = result
    assert restored.store.get("jobs_done", "submitted") is None


def test_terminal_checkpoint_replays_once_to_reporter_after_restart(tmp_path):
    s = settings(tmp_path)
    s.projects = [ProjectSettings(id="p", repo="example/private", commit_reports=False)]

    async def checkpoint_then_stop():
        first = Hub(s)
        first.requests["r"] = {"id": "r", "mode": "direct", "text": "study", "status": "running",
                               "project_id": "p"}
        first._queue_terminal_delivery = lambda event: None  # process exits just after commit
        first.orchestrator._finish("r", "finished", {}, ok=True)
        assert first.store.get("request", "r")["status"] == "done"
        assert [e["type"] for e in first.store.events_since(0)] == ["request.completed"]
        assert len(first.store.all("terminal_delivery")) == 1
        first.store.close()

    asyncio.run(checkpoint_then_stop())
    app = create_app(s)
    restored = app.state.hub
    received = []

    async def handle(event):
        received.append(event)

    restored.reporter.handle = handle
    with TestClient(app):
        for _ in range(100):
            if received:
                break
            time.sleep(0.01)
    assert [e["type"] for e in received] == ["request.created", "request.completed"]
    assert restored.store.all("terminal_delivery") == {}
    assert [e["type"] for e in restored.store.events_since(0)] == ["request.completed"]


def test_restart_creates_missing_issue_before_terminal_report(tmp_path):
    s = settings(tmp_path)
    s.projects = [ProjectSettings(id="p", repo="example/private", commit_reports=False)]

    async def checkpoint_then_stop():
        first = Hub(s)
        first.requests["r"] = {"id": "r", "mode": "direct", "text": "study", "status": "running",
                               "project_id": "p", "created_at": time.time()}
        first.save_request("r")
        first.reporter.submit = lambda event: None  # event persisted, issue not yet opened
        await first.publish({"type": "request.created", "request_id": "r", "data": {"text": "study"}})
        assert [event["type"] for event in first.store.all("reporter_delivery").values()] == ["request.created"]
        first.orchestrator._finish("r", "finished", {}, ok=True)
        first.store.close()

    asyncio.run(checkpoint_then_stop())
    calls = []

    class GitHub:
        async def create_issue(self, *args):
            calls.append("create")
            return {"number": 7, "html_url": "https://example.test/7"}

        async def comment(self, *args):
            calls.append("comment")
            return {"html_url": "https://example.test/7#comment"}

        async def close_issue(self, *args):
            calls.append("close")

    app = create_app(s)
    hub = app.state.hub
    hub.reporter.client = lambda: GitHub()
    with TestClient(app):
        for _ in range(100):
            if "close" in calls:
                break
            time.sleep(0.01)
    assert calls == ["create", "comment", "close"]
    assert hub.store.get("github_issue", "r") == {"number": 7}
    assert hub.store.all("terminal_delivery") == {}
    assert hub.store.all("reporter_delivery") == {}
    assert [e["type"] for e in hub.store.events_since(0) if e["type"].startswith("request.")] == [
        "request.created", "request.completed"]


@pytest.mark.asyncio
async def test_terminal_event_is_not_sent_twice_to_client_joining_after_commit(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "direct", "status": "done"}
    existing, joining = CaptureSocket(), CaptureSocket()
    hub.clients.add(existing)
    hub.commit_terminal("r", "request.completed", {"ok": True})
    hub.clients.add(joining)  # its WebSocket handshake replays from the event store
    await asyncio.sleep(0)
    assert [message["seq"] for message in existing.sent] == [1]
    assert joining.sent == []
    assert [event["seq"] for event in hub.store.events_since(0)] == [1]


@pytest.mark.asyncio
async def test_terminal_live_event_precedes_later_sequence(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "direct", "status": "done"}
    client = CaptureSocket()
    hub.clients.add(client)
    hub.commit_terminal("r", "request.completed", {"ok": True})
    await hub.publish({"type": "github.posted", "request_id": "r", "data": {}})
    await asyncio.sleep(0)
    assert [(event["seq"], event["type"]) for event in client.sent] == [
        (1, "request.completed"), (2, "github.posted")]


@pytest.mark.asyncio
async def test_resume_waits_for_inflight_task_result_before_dispatch(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    old_task = Task(id="old-task", agent_id="a", request_id="r", prompt="original",
                    meta={"kind": "step", "step_id": "s"})
    first.store.put("task", "old-task", {"request_id": "r", "step_id": "s", "kind": "step",
                                           "accepted": True, "runner_id": "local", "runner_incarnation": "inc",
                                           "payload": old_task.model_dump(mode="json"),
                                           "dispatched_at": 1.0})
    first.store.close()
    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    hub.recovery_steps.add("r")
    pending = asyncio.create_task(hub.dispatch(Task(agent_id="a", request_id="r", prompt="again",
                                                    meta={"kind": "step", "step_id": "s"})))
    await asyncio.sleep(0)
    assert not pending.done()
    assert not any(m.get("type") == "task.dispatch" for m in socket.sent)
    result = TaskResult(task_id="old-task", agent_id="a", ok=True, text="late")
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                          "task_id": "old-task", "request_id": "r",
                                          "data": result.model_dump(mode="json")})
    assert (await pending).text == "late"
    assert "s" not in hub.requests["r"].get("results", {})
    hub.result_map("r")["s"] = result
    assert hub.requests["r"]["results"]["s"]["text"] == "late"
    assert not any(m.get("type") == "task.dispatch" for m in socket.sent)


@pytest.mark.asyncio
async def test_accepted_recovery_can_finish_after_runner_connection_timeout(tmp_path):
    s = settings(tmp_path)
    s.gateway.resume_wait_s = 0.05
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    original = Task(id="long-task", agent_id="a", request_id="r", prompt="work",
                    meta={"kind": "step", "step_id": "s"})
    first.store.put("task", original.id, {"request_id": "r", "step_id": "s", "kind": "step",
                                           "accepted": True, "runner_id": "local", "runner_incarnation": "inc",
                                           "payload": original.model_dump(mode="json")})
    first.store.close()

    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    hub.recovery_steps.add("r")
    pending = asyncio.create_task(hub.dispatch(Task(agent_id="a", request_id="r", prompt="resume",
                                                    meta={"kind": "step", "step_id": "s"})))
    await asyncio.sleep(0.12)
    assert not pending.done()
    assert not any(msg.get("type") == "task.dispatch" for msg in socket.sent)
    result = TaskResult(task_id=original.id, agent_id="a", ok=True, text="finished")
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                          "task_id": original.id, "request_id": "r",
                                          "data": result.model_dump(mode="json")})
    assert (await asyncio.wait_for(pending, 1)).text == "finished"


@pytest.mark.asyncio
async def test_recovery_timeout_leaves_the_prior_task_cost_unaccounted(tmp_path):
    s = settings(tmp_path)
    s.gateway.resume_wait_s = 0.05
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    original = Task(id="sent-task", agent_id="a", request_id="r", prompt="work",
                    meta={"kind": "step", "step_id": "s"})
    # Sent before the restart; whether the runner took it is not known.
    first.store.put("task", original.id, {"request_id": "r", "step_id": "s", "kind": "step",
                                           "runner_id": "local", "runner_incarnation": "inc",
                                           "payload": original.model_dump(mode="json")})
    first.store.close()

    hub = Hub(s)
    hub.register_runner("local", CaptureSocket(), [{"id": "a"}], "inc")
    hub.recovery_steps.add("r")
    result = await asyncio.wait_for(hub.dispatch(Task(agent_id="a", request_id="r", prompt="resume",
                                                      meta={"kind": "step", "step_id": "s"})), 2)

    assert result.task_id == original.id and "recovery runner unavailable" in result.error
    req = hub.requests["r"]
    assert req["cost_items"][original.id]["reason"] == "outcome_unknown"  # never a $0 task (#270)
    assert req["cost_known"] is False and req["cost_summary"]["unknown_count"] == 1


@pytest.mark.asyncio
async def test_new_runner_generation_ends_recovered_accepted_task_without_retry(tmp_path):
    s = settings(tmp_path)
    s.gateway.resume_wait_s = 0.05
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    original_runner = Runner(s)
    old = CaptureSocket()
    first.register_runner("local", old, [{"id": "a"}], original_runner.incarnation)
    task = Task(id="accepted-old", agent_id="a", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "s"})
    pending = asyncio.create_task(first.dispatch(task))
    await asyncio.sleep(0)
    await first.on_runner_message("local", {"type": "task.accepted", "runner_seq": 1,
                                            "task_id": task.id, "request_id": "r"})
    assert first.store.get("task", task.id)["runner_incarnation"] == original_runner.incarnation
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    first.store.close()
    original_runner.store.close()

    s.runner.state_dir = str(tmp_path / "fresh-runner-state")  # previous runner ledger is unavailable
    replacement = Runner(s)
    assert replacement.incarnation != original_runner.incarnation
    restored = Hub(s)
    newer = CaptureSocket()
    restored.register_runner("local", newer, [{"id": "a"}], replacement.incarnation)
    restored.recovery_steps.add("r")
    result = await asyncio.wait_for(restored.dispatch(Task(agent_id="a", request_id="r", prompt="resume",
                                                           meta={"kind": "step", "step_id": "s"})), 1)
    assert result.task_id == task.id and not result.ok
    assert "generation changed" in result.error and failure_kind(result) == "terminal"
    assert restored.store.get("task", task.id)["completed"] is True
    assert not any(msg.get("type") == "task.dispatch" for msg in newer.sent)


@pytest.mark.asyncio
async def test_new_runner_generation_ends_live_accepted_future(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "direct", "status": "running", "agent_id": "a"}
    old = CaptureSocket()
    hub.register_runner("local", old, [{"id": "a"}], "old-incarnation")
    task = Task(id="live-old", agent_id="a", request_id="r", prompt="work", meta={"kind": "direct"})
    waiting = asyncio.create_task(hub.orchestrator.run_step(task))
    await asyncio.sleep(0)
    await hub.on_runner_message("local", {"type": "task.accepted", "runner_seq": 1,
                                          "task_id": task.id, "request_id": "r"})
    newer = CaptureSocket()
    hub.register_runner("local", newer, [{"id": "a"}], "new-incarnation")
    result = await asyncio.wait_for(waiting, 1)
    assert not result.ok and failure_kind(result) == "terminal"
    assert hub.store.get("task", task.id)["completed"] is True
    assert [msg["task"]["id"] for msg in old.sent if msg.get("type") == "task.dispatch"] == [task.id]
    assert not any(msg.get("type") == "task.dispatch" for msg in newer.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_ok", [True, False])
async def test_restart_during_retry_adopts_only_final_attempt(tmp_path, retry_ok):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running",
                           "plan": {"steps": [{"id": "s", "agent_id": "a", "instruction": "analyze",
                                               "depends_on": []}]}}
    first.save_request("r")
    first.register_runner("local", CaptureSocket(), [], "inc")
    for attempt, tid in [(1, "failed-attempt"), (2, "retry-attempt")]:
        first.store.put("task", tid, {"request_id": "r", "step_id": "s", "kind": "step",
                                       "attempt": attempt, "revision": 0, "accepted": True,
                                       "dispatched_at": float(attempt)})
    failure = TaskResult(task_id="failed-attempt", agent_id="a", ok=False,
                         error="connection reset", cost_usd=0.3)
    await first.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                            "task_id": "failed-attempt", "request_id": "r",
                                            "data": failure.model_dump(mode="json")})
    assert "s" not in first.requests["r"].get("results", {})
    assert first.store.get("task", "failed-attempt")["attempt"] == 1
    first.store.close()

    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": a} for a in ("a", "cso", "sci_reviewer")], "inc")
    outcome = TaskResult(task_id="retry-attempt", agent_id="a", ok=retry_ok,
                         text="final" if retry_ok else "", error=None if retry_ok else "connection reset",
                         cost_usd=0.2)
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 2,
                                          "task_id": "retry-attempt", "request_id": "r",
                                          "data": outcome.model_dump(mode="json")})
    assert "s" not in hub.requests["r"].get("results", {})
    run_step = hub.orchestrator.run_step

    async def fake_step(task):
        if task.meta["kind"] == "step":
            return await run_step(task)
        if task.meta["kind"] == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"verdict": "accept", "scores": {
                                  "addresses_question": 4, "evidence": 4, "thoroughness": 4}, "issues": []})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="report")

    hub.orchestrator.run_step = fake_step
    aid = next(iter(hub.approvals))
    await hub.resolve_approval(aid, True)
    for _ in range(100):
        if hub.requests["r"]["status"] in {"done", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert hub.requests["r"]["status"] == ("done" if retry_ok else "failed")
    assert hub.requests["r"]["results"]["s"]["task_id"] == "retry-attempt"
    assert hub.requests["r"]["results"]["s"]["ok"] is retry_ok
    assert hub.requests["r"]["cost_usd"] == 0.5
    assert not any(m.get("type") == "task.dispatch" for m in socket.sent)


@pytest.mark.asyncio
async def test_restart_during_revision_preserves_feedback_and_adopts_revision(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    first.register_runner("local", CaptureSocket(), [
        {"id": a, "name": a, "role": "test", "engine": "mock"}
        for a in ("a", "cso", "sci_reviewer")])
    original_run_dag = first.orchestrator.run_dag

    async def stop_before_revision(*args, **kwargs):
        if kwargs.get("feedback"):
            assert "s" not in first.requests["r"].get("results", {})
            assert first.requests["r"]["pending_revisions"]["s"]["revision"] == 1
            raise asyncio.CancelledError
        return await original_run_dag(*args, **kwargs)

    async def first_step(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"steps": [{"id": "s", "agent_id": "a", "instruction": "analyze",
                                                    "depends_on": []}]})
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"verdict": "revise", "scores": {
                                  "addresses_question": 4, "evidence": 3, "thoroughness": 4},
                                  "issues": [{"step_id": "s", "problem": "weak", "request": "check again"}]})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="original")

    first.orchestrator.run_step = first_step
    first.orchestrator.run_dag = stop_before_revision
    with pytest.raises(asyncio.CancelledError):
        await first.orchestrator.run_request("r")
    assert first.requests["r"]["pending_revisions"]["s"]["previous_result"]["text"] == "original"
    first.store.put("task", "revision-task", {"request_id": "r", "step_id": "s", "kind": "step",
                                                 "attempt": 1, "revision": 1, "accepted": True,
                                                 "dispatched_at": 1.0})
    revised = TaskResult(task_id="revision-task", agent_id="a", ok=True, text="revised")
    await first.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                            "task_id": "revision-task", "request_id": "r",
                                            "data": revised.model_dump(mode="json")})
    assert "s" not in first.requests["r"].get("results", {})
    first.store.close()

    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": a} for a in ("a", "cso", "sci_reviewer")])
    run_step = hub.orchestrator.run_step
    prompts = []

    async def resumed_step(task):
        if task.meta["kind"] == "step":
            prompts.append((task.prompt, task.context, task.meta["revision"]))
            return await run_step(task)
        if task.meta["kind"] == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"verdict": "accept", "scores": {
                                  "addresses_question": 4, "evidence": 4, "thoroughness": 4}, "issues": []})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="report")

    hub.orchestrator.run_step = resumed_step
    await hub.resolve_approval(next(iter(hub.approvals)), True)
    for _ in range(100):
        if hub.requests["r"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert hub.requests["r"]["status"] == "done"
    assert hub.requests["r"]["results"]["s"]["text"] == "revised"
    assert hub.requests["r"].get("pending_revisions") == {}
    assert len(prompts) == 1 and "check again" in prompts[0][0]
    assert "Previous turn:\noriginal" in prompts[0][0] and prompts[0][2] == 1
    assert "Your step (s): analyze" in prompts[0][0]
    assert not any(m.get("type") == "task.dispatch" for m in socket.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [
    {"kind": "briefing"}, {"kind": "plan"},
    {"kind": "review", "revision": 1, "parse_attempt": 2}, {"kind": "synthesis"},
])
@pytest.mark.parametrize("completed", [False, True])
async def test_control_phase_recovers_exact_task_identity(tmp_path, meta, completed):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running",
                           "cost_usd": 0.2 if completed else 0.0}
    first.save_request("r")
    original = Task(id="control-original", agent_id="a", request_id="r", prompt="control", meta=meta)
    result = TaskResult(task_id=original.id, agent_id="a", ok=True, text="saved", cost_usd=0.2)
    first.store.put("task", original.id, {"request_id": "r", "kind": meta["kind"],
                                           "step_id": None, "revision": meta.get("revision", 0),
                                           "parse_attempt": meta.get("parse_attempt", 0),
                                           "accepted": True, "runner_id": "local", "runner_incarnation": "inc",
                                           "payload": original.model_dump(mode="json"),
                                          "completed": completed,
                                          "result": result.model_dump(mode="json") if completed else None})
    if meta["kind"] == "review":
        first.store.put("task", "other-parse", {"request_id": "r", "kind": "review", "step_id": None,
                                                 "revision": 1, "parse_attempt": 1, "accepted": True,
                                                 "completed": True, "result": result.model_dump(mode="json")})
    first.store.close()

    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    hub.recovery_steps.add("r")
    recovering = asyncio.create_task(hub.dispatch(Task(agent_id="a", request_id="r", prompt="new", meta=meta)))
    await asyncio.sleep(0)
    if not completed:
        assert not recovering.done()
        await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                              "request_id": "r", "task_id": original.id,
                                              "data": result.model_dump(mode="json")})
    recovered = await asyncio.wait_for(recovering, 1)
    assert recovered.task_id == original.id and recovered.text == "saved"
    if completed:
        assert recovered.cost_usd == 0 and hub.requests["r"]["cost_usd"] == 0.2
    assert not any(msg.get("type") == "task.dispatch" for msg in socket.sent)


@pytest.mark.asyncio
async def test_failed_revision_fallback_is_durable(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    step = {"id": "s", "agent_id": "a", "instruction": "analyze", "depends_on": []}
    original = TaskResult(task_id="first", agent_id="a", ok=True, text="evidence",
                          workdir="runs/s", workdir_id="s", outputs=["outputs/data.tsv"])
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running",
                         "plan": {"steps": [step]}, "results": {},
                         "pending_revisions": {"s": {"revision": 1, "feedback": "check again",
                                                      "previous_result": original.model_dump(mode="json")}}}
    hub.save_request("r")
    results = SavedResults(hub, "r")
    dict.__setitem__(results, "s", original)

    async def fail_revision(task):
        assert task.meta["workdir"] == "runs/s"
        return TaskResult(task_id=task.id, agent_id="a", ok=False, error="revision error")

    hub.orchestrator.run_step = fail_revision
    await hub.orchestrator.run_dag("r", "study", [step], results, only={"s"},
                                   feedback={"s": "check again"})
    assert hub.requests["r"]["results"]["s"]["text"] == "evidence"
    assert hub.requests["r"]["pending_revisions"] == {}
    hub.store.close()
    restored = Hub(s)
    assert "revision error" in restored.requests["r"]["results"]["s"]["revision_failed"]


@pytest.mark.asyncio
async def test_review_revision_budget_survives_restart(tmp_path):
    s = settings(tmp_path)
    s.orchestrator.max_revisions = 2
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running",
                           "plan": {"steps": [{"id": "s", "agent_id": "a", "instruction": "analyze",
                                               "depends_on": []}]},
                           "results": {"s": TaskResult(task_id="initial", agent_id="a", ok=True,
                                                       text="initial").model_dump(mode="json")}}
    first.save_request("r")
    first.register_runner("local", CaptureSocket(), [{"id": a} for a in ("a", "cso", "sci_reviewer")])

    def revise(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured={"verdict": "revise", "scores": {
                              "addresses_question": 4, "evidence": 3, "thoroughness": 4},
                              "issues": [{"step_id": "s", "problem": "weak", "request": "revise"}]})

    async def before_restart(task):
        if task.meta["kind"] == "review":
            if task.meta["revision"] == 1:
                raise asyncio.CancelledError
            return revise(task)
        if task.meta["kind"] == "step":
            return TaskResult(task_id=task.id, agent_id="a", ok=True, text="revision one")
        raise AssertionError(task.meta["kind"])

    first.orchestrator.run_step = before_restart
    with pytest.raises(asyncio.CancelledError):
        await first.orchestrator.run_request("r", resume=True)
    assert first.store.get("request", "r")["review_progress"]["next_revision"] == 1
    assert first.store.get("request", "r")["review_progress"]["last_completed_revision"] == 1
    first.store.close()

    restored = Hub(s)
    restored.register_runner("local", CaptureSocket(), [{"id": a} for a in ("a", "cso", "sci_reviewer")])
    reviews, revisions = [], []

    async def after_restart(task):
        if task.meta["kind"] == "review":
            reviews.append(task.meta["revision"])
            return revise(task)
        if task.meta["kind"] == "step":
            revisions.append(task.meta["revision"])
            return TaskResult(task_id=task.id, agent_id="a", ok=True, text="revision two")
        raise AssertionError(task.meta["kind"])

    restored.orchestrator.run_step = after_restart
    await restored.orchestrator.run_request("r", resume=True)
    assert reviews == [1, 2] and revisions == [2]
    assert restored.requests["r"]["status"] == "failed"
    assert restored.requests["r"]["review_progress"]["last_completed_review"] == 2
    assert restored.requests["r"]["review_progress"]["last_completed_revision"] == 2


def test_resume_synthesis_does_not_wait_for_finished_reviewer(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "status": "interrupted",
                         "plan": {"steps": [{"id": "s", "agent_id": "retired", "depends_on": []}]},
                         "results": {"s": {"ok": True}},
                         "review_progress": {"phase": "synthesis", "next_revision": 1}}
    assert hub.resume_agents("r") == {"cso"}


def test_plan_only_resume_before_plan_waits_only_for_planners(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "plan_only", "status": "interrupted"}

    assert hub.resume_agents("r") == {"chief_of_staff", "cso"}


def test_plan_only_resume_after_plan_waits_for_no_agents(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "mode": "plan_only", "status": "interrupted", "plan": {"steps": [
        {"id": "s", "agent_id": "worker", "depends_on": []},
    ]}}

    assert hub.resume_agents("r") == set()


@pytest.mark.asyncio
async def test_unaccepted_dispatch_is_resent_with_original_task_id(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    original = Task(id="original-id", agent_id="a", request_id="r", prompt="work",
                    meta={"kind": "step", "step_id": "s", "attempt": 1, "revision": 0})
    first.store.put("task", original.id, {"request_id": "r", "step_id": "s", "kind": "step",
                                            "attempt": 1, "revision": 0, "accepted": False,
                                            "runner_id": "local", "runner_incarnation": "inc",
                                            "payload": original.model_dump(mode="json"), "dispatched_at": 1.0})
    first.store.close()
    hub = Hub(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    hub.recovery_steps.add("r")
    recovering = asyncio.create_task(hub.dispatch(Task(agent_id="a", request_id="r", prompt="new",
                                                       meta={"kind": "step", "step_id": "s", "attempt": 1})))
    for _ in range(50):
        if any(m.get("type") == "task.dispatch" for m in socket.sent):
            break
        await asyncio.sleep(0.01)
    sends = [m for m in socket.sent if m.get("type") == "task.dispatch"]
    assert len(sends) == 1 and sends[0]["task"]["id"] == "original-id"
    await hub.on_runner_message("local", {"type": "task.accepted", "runner_seq": 1,
                                          "task_id": "original-id", "request_id": "r"})
    assert hub.store.get("task", "original-id")["accepted"] is True
    result = TaskResult(task_id="original-id", agent_id="a", ok=True, text="done")
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 2,
                                          "task_id": "original-id", "request_id": "r",
                                          "data": result.model_dump(mode="json")})
    assert (await recovering).task_id == "original-id"
    assert len([m for m in socket.sent if m.get("type") == "task.dispatch"]) == 1


@pytest.mark.asyncio
async def test_runner_acknowledges_duplicate_task_without_running_twice(tmp_path):
    s = settings(tmp_path)
    runner = Runner(s)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_run(task):
        calls.append(task.id)
        started.set()
        await release.wait()

    runner._run_guarded = fake_run
    task = Task(id="one-task", agent_id="a", request_id="r", prompt="work")
    message = {"type": "task.dispatch", "task": task.model_dump(mode="json")}
    await runner._on_message(message)
    await started.wait()
    await runner._on_message(message)
    assert calls == ["one-task"]
    assert runner.store.get("accepted_task", "one-task")["state"] == "running"
    assert [e["type"] for e in runner.store.pending()] == ["task.accepted", "task.accepted"]
    release.set()
    await runner.tasks["one-task"]
    runner.store.close()

    restored = Runner(s)
    await restored._on_message(message)
    assert "one-task" not in restored.tasks
    assert any(e["type"] == "task.result" and e["task_id"] == "one-task"
               for e in restored.store.pending())


@pytest.mark.asyncio
async def test_runner_restart_finishes_accepted_task_for_live_gateway(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "mode": "direct", "status": "running", "agent_id": "a"}
    hub.save_request("r")
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], "inc")
    task = Task(id="accepted", agent_id="a", request_id="r", prompt="work", meta={"kind": "direct"})
    waiting = asyncio.create_task(hub.dispatch(task))
    await asyncio.sleep(0)
    first = Runner(s)
    first.store.put("accepted_task", task.id, {"state": "running", "task": task.model_dump(mode="json")})
    first.store.close()

    restored = Runner(s)
    results = [e for e in restored.store.pending() if e["type"] == "task.result"]
    assert len(results) == 1 and results[0]["task_id"] == task.id
    assert "runner restarted" in results[0]["data"]["error"]
    assert failure_kind(TaskResult.model_validate(results[0]["data"])) == "transient"
    await restored._on_message({"type": "task.dispatch", "task": task.model_dump(mode="json")})
    assert len([e for e in restored.store.pending() if e["type"] == "task.result"]) == 1
    await hub.on_runner_message("local", results[0])
    assert (await asyncio.wait_for(waiting, 1)).task_id == task.id


@pytest.mark.asyncio
async def test_runner_restart_with_tracked_hpc_job_wakes_without_resubmitting(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    hub.save_request("r")
    first = Runner(s)
    socket = CaptureSocket()
    hub.register_runner("local", socket, [{"id": "a"}], first.incarnation)
    task = Task(id="submitted-once", agent_id="a", request_id="r", prompt="analyze",
                meta={"kind": "step", "step_id": "s"})
    running = asyncio.create_task(hub.orchestrator.run_step(task))
    await asyncio.sleep(0)
    assert [m["task"]["id"] for m in socket.sent if m.get("type") == "task.dispatch"] == [task.id]
    await hub.on_runner_message("local", {"type": "task.accepted", "task_id": task.id,
                                          "request_id": "r"})
    first.store.put("accepted_task", task.id, {"state": "running", "task": task.model_dump(mode="json")})
    first.task_req[task.id] = "r"

    class Workspace:
        dir = tmp_path / "existing-work"

        def append_job(self, body):
            pass

        def append_event(self, body):
            pass

    first.workspaces[task.id] = Workspace()
    await first._track_job({"job_id": "42", "task_id": task.id, "agent_id": "a", "name": "analysis"})
    first.store.close()

    restored = Runner(s)
    assert restored.incarnation == first.incarnation
    assert restored.jobs["42"]["workdir"] == str(Workspace.dir)
    result_events = [e for e in restored.store.pending() if e["type"] == "task.result"]
    assert len(result_events) == 1
    recovered = TaskResult.model_validate(result_events[0]["data"])
    assert recovered.ok and recovered.pending_jobs == ["42"]
    assert failure_kind(recovered) is None
    delivered = 0
    for event in restored.store.pending():
        await hub.on_runner_message("local", event)
        delivered = event["runner_seq"]
    await restored._poll_jobs()
    for event in restored.store.pending():
        if event["runner_seq"] > delivered:
            await hub.on_runner_message("local", event)
            delivered = event["runner_seq"]
    for _ in range(100):
        dispatches = [m for m in socket.sent if m.get("type") == "task.dispatch"]
        if len(dispatches) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(dispatches) == 2
    wake = dispatches[1]["task"]
    assert wake["meta"]["parent_task"] == task.id and wake["meta"]["workdir"] == str(Workspace.dir)
    assert [e["type"] for e in restored.store.pending()].count("job.submitted") == 1
    assert restored.notified == {task.id}
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": delivered + 1,
                                          "task_id": wake["id"], "request_id": "r",
                                          "data": TaskResult(task_id=wake["id"], agent_id="a", ok=True,
                                                             text="existing HPC result checked").model_dump(mode="json")})
    assert (await asyncio.wait_for(running, 1)).text == "existing HPC result checked"


@pytest.mark.asyncio
async def test_uncertain_send_reconnects_with_same_task_id(tmp_path):
    s = settings(tmp_path)
    s.orchestrator.runner_reconnect_timeout_s = 1
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "status": "running", "text": "study"}
    hub.save_request("r")

    class FailedAfterFrame(CaptureSocket):
        async def send_text(self, body):
            message = json.loads(body)
            self.sent.append(message)
            if message.get("type") == "task.dispatch":
                raise OSError("connection lost after frame")

    old = FailedAfterFrame()
    hub.register_runner("local", old, [{"id": "a"}], "inc")
    task = Task(id="original-id", agent_id="a", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "s"})
    pending = asyncio.create_task(hub.orchestrator.run_step(task))
    for _ in range(50):
        if any(m.get("type") == "task.dispatch" for m in old.sent):
            break
        await asyncio.sleep(0.01)
    assert not pending.done()
    assert hub.store.get("task", task.id)["accepted"] is False
    assert list(hub.store.all("task")) == [task.id]

    new = CaptureSocket()
    hub.register_runner("local", new, [{"id": "a"}], "inc")
    for _ in range(50):
        if any(m.get("type") == "task.dispatch" for m in new.sent):
            break
        await asyncio.sleep(0.01)
    resent = [m for m in new.sent if m.get("type") == "task.dispatch"]
    assert len(resent) == 1 and resent[0]["task"]["id"] == task.id
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                          "task_id": task.id, "request_id": "r",
                                          "data": TaskResult(task_id=task.id, agent_id="a", ok=True,
                                                             text="done").model_dump(mode="json")})
    assert (await asyncio.wait_for(pending, 1)).text == "done"
    assert list(hub.store.all("task")) == [task.id]


@pytest.mark.asyncio
async def test_uncertain_send_to_new_incarnation_requires_manual_recovery(tmp_path):
    s = settings(tmp_path)
    s.orchestrator.runner_reconnect_timeout_s = 1
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "status": "running", "text": "study"}

    class FailedAfterFrame(CaptureSocket):
        async def send_text(self, body):
            message = json.loads(body)
            self.sent.append(message)
            if message.get("type") == "task.dispatch":
                raise OSError("connection lost after frame")

    old = FailedAfterFrame()
    hub.register_runner("local", old, [{"id": "a"}], "old-incarnation")
    task = Task(id="uncertain-id", agent_id="a", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "s"})
    running = asyncio.create_task(hub.orchestrator.run_step(task))
    for _ in range(50):
        if any(msg.get("type") == "task.dispatch" for msg in old.sent):
            break
        await asyncio.sleep(0.01)
    entry = hub.store.get("task", task.id)
    assert not entry["accepted"] and entry["runner_incarnation"] == "old-incarnation"
    replacement = CaptureSocket()
    hub.register_runner("local", replacement, [{"id": "a"}], "new-incarnation")
    result = await asyncio.wait_for(running, 1)
    assert not result.ok and failure_kind(result) == "terminal"
    assert hub.store.get("task", task.id)["completed"] is True
    assert [msg["task"]["id"] for msg in old.sent if msg.get("type") == "task.dispatch"] == [task.id]
    assert not any(msg.get("type") == "task.dispatch" for msg in replacement.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_incarnation", ["old-incarnation", None])
async def test_unaccepted_recovery_never_resends_to_different_incarnation(tmp_path, previous_incarnation):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "running"}
    first.save_request("r")
    task = Task(id="uncertain-id", agent_id="a", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "s"})
    first.store.put("task", task.id, {"request_id": "r", "kind": "step", "step_id": "s",
                                      "payload": task.model_dump(mode="json"), "accepted": False,
                                      "runner_id": "local", "runner_incarnation": previous_incarnation})
    first.store.close()
    restored = Hub(s)
    replacement = CaptureSocket()
    restored.register_runner("local", replacement, [{"id": "a"}], "new-incarnation")
    restored.recovery_steps.add("r")
    result = await restored.dispatch(Task(agent_id="a", request_id="r", prompt="resume",
                                          meta={"kind": "step", "step_id": "s"}))
    assert not result.ok and failure_kind(result) == "terminal"
    assert result.task_id == task.id
    assert not any(msg.get("type") == "task.dispatch" for msg in replacement.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_only", [False, True])
async def test_completed_direct_result_finishes_without_original_agent(tmp_path, checkpoint_only):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "direct", "agent_id": "retired", "text": "work",
                           "status": "running"}
    first.save_request("r")
    first.register_runner("local", CaptureSocket(), [{"id": "retired"}], "inc")
    task = Task(id="direct-done", agent_id="retired", request_id="r", prompt="work",
                meta={"kind": "direct"})
    waiting = asyncio.create_task(first.dispatch(task))
    await asyncio.sleep(0)
    result = TaskResult(task_id=task.id, agent_id="retired", ok=True, text="finished", cost_usd=0.3)
    await first.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                            "task_id": task.id, "request_id": "r",
                                            "data": result.model_dump(mode="json")})
    assert (await waiting).ok
    if checkpoint_only:
        first.store.delete("task", task.id)
    first.store.close()

    restored = Hub(s)
    assert restored.resume_agents("r") == set()
    approval = next(aid for aid, entry in restored.approvals.items()
                    if entry["approval"]["kind"] == "resume")
    await restored.resolve_approval(approval, True)
    for _ in range(100):
        if restored.requests["r"]["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert restored.requests["r"]["status"] == "done"
    assert restored.requests["r"]["report"] == "finished"
    assert restored.requests["r"]["cost_usd"] == 0.3
    assert not any(event["type"] == "request.resume_waiting" for event in restored.events)


@pytest.mark.asyncio
async def test_completed_step_agent_is_not_required_for_resume(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "mode": "orchestrate", "text": "study", "status": "interrupted",
                         "plan": {"steps": [
                             {"id": "done", "agent_id": "retired", "depends_on": []},
                             {"id": "todo", "agent_id": "worker", "depends_on": ["done"]}]},
                         "results": {"done": TaskResult(task_id="t", agent_id="retired", ok=True).model_dump(mode="json")}}
    assert hub.resume_agents("r") == {"worker", "cso", "sci_reviewer"}


@pytest.mark.asyncio
async def test_replaced_runner_socket_cannot_advance_new_incarnation_cursor(tmp_path):
    hub = Hub(settings(tmp_path))

    class Socket(CaptureSocket):
        def __init__(self):
            super().__init__()
            self.closed = False
        async def close(self, code=1000):
            self.closed = True

    old, new = Socket(), Socket()
    hub.register_runner("local", old, [], "old")
    await hub.on_runner_message("local", {"type": "agent.log", "runner_seq": 9}, old, "old")
    hub.register_runner("local", new, [], "new")
    await asyncio.sleep(0)
    assert old.closed and hub.store.runner_seen("local") == 0
    await hub.on_runner_message("local", {"type": "agent.log", "runner_seq": 10}, old, "old")
    assert hub.store.runner_seen("local") == 0
    future = asyncio.get_running_loop().create_future()
    hub.futures["t"] = future
    await hub.on_runner_message("local", {"type": "task.result", "runner_seq": 1,
                                           "task_id": "t", "data": TaskResult(task_id="t", agent_id="a",
                                                                                 ok=True).model_dump(mode="json")},
                                new, "new")
    assert future.done() and hub.store.runner_seen("local") == 1


@pytest.mark.asyncio
async def test_resume_denial_publishes_request_failed(tmp_path):
    s = settings(tmp_path)
    first = Hub(s)
    first.requests["r"] = {"id": "r", "mode": "direct", "agent_id": "a", "text": "work", "status": "running"}
    first.save_request("r")
    first.store.close()
    restored = Hub(s)
    aid = next(iter(restored.approvals))
    await restored.resolve_approval(aid, False)
    assert restored.requests["r"]["status"] == "failed"
    assert [e["type"] for e in restored.events if e.get("request_id") == "r"][-2:] == [
        "approval.resolved", "request.failed"]


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
