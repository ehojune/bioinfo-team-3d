"""Consults that were running when the gateway restarted (#93)."""

import asyncio
import json

import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, Task, TaskResult
from labhq.settings import Settings

ROSTER = [{"id": "cso", "engine": "claude_code"}, {"id": "worker", "engine": "claude_code"}]


class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        self.sent.append(json.loads(body))

    async def close(self, code=1000):
        pass


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.gateway.resume_wait_s = 2
    s.orchestrator.step_max_attempts = 1
    s.orchestrator.step_retry_backoff_s = 0
    return s


def question(ask_id=None):
    ask = AskRequest(task_id="source", agent_id="worker", request_id="r", to="cso",
                     question="Which cohort should the comparison use?", why_blocked="cohort missing")
    return ask.model_copy(update={"id": ask_id}) if ask_id else ask


def consults(socket):
    return [frame["task"] for frame in socket.sent
            if frame["type"] == "task.dispatch" and frame["task"]["meta"].get("kind") == "consult"]


async def eventually(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def settle(hub):
    if hub.ask_tasks:
        await asyncio.wait_for(asyncio.gather(*list(hub.ask_tasks.values())), 3)
    await asyncio.sleep(0)


def result_frame(task_id, text, session):
    result = TaskResult(task_id=task_id, agent_id="cso", ok=True, text=text, session_id=session)
    return {"type": "task.result", "task_id": task_id, "request_id": "r", "data": result.model_dump(mode="json")}


async def start_consult_then_crash(s, workdir):
    """Gateway 1 routes an ask; the runner accepts the consult; the gateway dies mid-consult."""
    first = Hub(s)
    first.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running", "mode": "direct",
                           "agent_id": "worker", "cso_session_id": "shared-session", "cso_workdir": workdir}
    first.save_request("r")
    socket = CaptureSocket()
    first.register_runner("local", socket, ROSTER, "inc-1")
    ask = question()
    await first.on_runner_message("local", {"type": "ask.requested", "data": ask.model_dump(mode="json")})
    await eventually(lambda: consults(socket))
    running = consults(socket)[0]
    assert running["resume_session_id"] == "shared-session" and running["meta"]["workdir"] == workdir
    await first.on_runner_message("local", {"type": "task.accepted", "task_id": running["id"], "request_id": "r"})
    tasks = list(first.ask_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert first.store.get("ask", ask.id)["state"] == "working"
    first.store.close()
    return ask, running


async def approve_resume(hub):
    async def wait_for_worker(rid):
        assert hub.requests[rid]["status"] == "waiting_for_runner"

    hub.resume_when_ready = wait_for_worker
    aid = next(aid for aid, entry in hub.approvals.items() if entry["approval"]["kind"] == "resume")
    await hub.resolve_approval(aid, True)


@pytest.mark.parametrize("runner", ["same_generation", "late_reconnect"])
async def test_restart_adopts_running_consult_instead_of_overlapping(tmp_path, runner):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    ask, running = await start_consult_then_crash(s, workdir)

    second = Hub(s)
    socket = CaptureSocket()
    try:
        assert second.requests["r"]["status"] == "interrupted"
        if runner == "same_generation":
            second.register_runner("local", socket, ROSTER, "inc-1")
        await approve_resume(second)
        await asyncio.sleep(0.1)
        if runner == "late_reconnect":
            assert second.store.get("ask", ask.id)["state"] == "working", "wait for the runner, do not fail"
            second.register_runner("local", socket, ROSTER, "inc-1")
            await asyncio.sleep(0.1)
        # The old consult still owns the CSO session and workdir on the runner.
        for task in consults(socket):
            assert task["id"] == running["id"] or (
                task["resume_session_id"] != "shared-session" and task["meta"].get("workdir") != workdir)
        assert [task for task in consults(socket) if task["id"] != running["id"]] == []

        await second.on_runner_message("local", result_frame(running["id"], "Use cohort C7", "rotated-session"))
        await settle(second)
        entry = second.store.get("ask", ask.id)
        assert entry["state"] == "resolved"
        assert entry["answer"]["status"] == "answered" and entry["answer"]["answer"] == "Use cohort C7"
        assert second.requests["r"]["cso_session_id"] == "rotated-session"
        assert [frame["type"] for frame in socket.sent].count("task.dispatch") == 0
    finally:
        second.store.close()


async def test_restart_isolates_consult_when_runner_generation_changed(tmp_path):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    ask, running = await start_consult_then_crash(s, workdir)

    second = Hub(s)
    socket = CaptureSocket()
    try:
        # The runner restarted too: the old consult's outcome is unknown, so it is not adopted.
        second.register_runner("local", socket, ROSTER, "inc-2")
        await approve_resume(second)
        await eventually(lambda: consults(socket))
        retry = consults(socket)[0]
        assert retry["id"] != running["id"]
        assert retry["resume_session_id"] is None and "workdir" not in retry["meta"]
        await second.on_runner_message("local", result_frame(retry["id"], "Use cohort C9", "isolated-session"))
        await settle(second)
        answer = second.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "answered" and answer["answer"] == "Use cohort C9"
    finally:
        second.store.close()


async def test_unfinished_consult_in_ledger_keeps_its_session_busy(tmp_path):
    hub = Hub(settings(tmp_path))
    workdir = str(tmp_path / "cso-workdir")
    try:
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running",
                             "cso_session_id": "shared-session", "cso_workdir": workdir}
        old = Task(id="old-consult", agent_id="cso", request_id="r", prompt="answer another ask",
                   resume_session_id="shared-session",
                   meta={"kind": "consult", "ask_id": "another-ask", "workdir": workdir})
        hub.store.put("task", old.id, {"request_id": "r", "kind": "consult", "accepted": True,
                                       "completed": False, "payload": old.model_dump(mode="json")})
        calls = []

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="Use cohort C7")

        hub.dispatch = dispatch
        await hub.orchestrator.answer_ask(question(), "origin")
        assert len(calls) == 1
        assert calls[0].resume_session_id is None and "workdir" not in calls[0].meta
    finally:
        hub.store.close()


async def test_recovery_never_answers_an_ask_with_another_asks_consult(tmp_path):
    hub = Hub(settings(tmp_path))
    socket = CaptureSocket()
    try:
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running"}
        hub.register_runner("local", socket, ROSTER, "inc-1")
        earlier = Task(id="earlier-consult", agent_id="cso", request_id="r", prompt="answer the first ask",
                       meta={"kind": "consult", "ask_id": "first-ask"})
        done = TaskResult(task_id=earlier.id, agent_id="cso", ok=True, text="answer for the first ask")
        hub.store.put("task", earlier.id, {"request_id": "r", "kind": "consult", "accepted": True,
                                           "completed": True, "runner_id": "local",
                                           "runner_incarnation": "inc-1", "dispatched_at": 1,
                                           "payload": earlier.model_dump(mode="json"),
                                           "result": done.model_dump(mode="json")})
        hub.recovery_steps.add("r")
        ask = question("second-ask")
        routed = asyncio.create_task(hub.orchestrator.answer_ask(ask, "local"))
        await eventually(lambda: consults(socket) or routed.done())
        assert consults(socket), "the second ask needs its own consult"
        fresh = consults(socket)[0]
        assert fresh["meta"]["ask_id"] == "second-ask"
        await hub.on_runner_message("local", result_frame(fresh["id"], "answer for the second ask", None))
        await asyncio.wait_for(routed, 2)
        assert hub.store.get("ask", ask.id)["answer"]["answer"] == "answer for the second ask"
    finally:
        hub.store.close()
