"""Logical ask limits, wake exhaustion and PI-approved restart (PR #76)."""

import asyncio

import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, Task, TaskResult, waiting
from labhq.settings import Settings


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.orchestrator.step_max_attempts = 1
    s.orchestrator.step_retry_backoff_s = 0
    return s


def save_task(hub, tid, rid="r", step="A"):
    meta = {"kind": "direct"} if step is None else {"kind": "step", "step_id": step}
    task = Task(id=tid, agent_id="worker", request_id=rid, prompt="compare", meta=meta)
    hub.store.put("task", tid, {"request_id": rid, **meta, "payload": task.model_dump(mode="json")})
    return task


@pytest.mark.parametrize("step", ["A", None], ids=["dag", "direct"])
@pytest.mark.parametrize("cap", ["step", "target", "request"])
async def test_4148905734_caps_survive_new_tasks_and_restart(tmp_path, step, cap):
    s = settings(tmp_path)
    hub = Hub(s)
    count = {"step": 3, "target": 2, "request": 12}[cap]
    for i in range(count):
        prior_step = f"other-{i}" if cap == "request" else step
        save_task(hub, f"old-{i}", step=prior_step)
        to = ("cso", "facilities", "colleague:peer")[i] if cap == "step" else "cso"
        ask = AskRequest(task_id=f"old-{i}", agent_id="worker", request_id="r", to=to,
                         question=f"Prior question {i}?", why_blocked="missing choice")
        hub.store.put("ask", ask.id, {"state": "resolved", "ask": ask.model_dump(mode="json"),
                                       "answer": {"status": "answered", "answer": "choice"}})
    hub.store.close()
    hub = Hub(s)
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="choice")

    hub.dispatch = dispatch
    hub.agents = {"cso": {"engine": "mock"}}
    save_task(hub, "wake", step=step)
    ask = AskRequest(task_id="wake", agent_id="worker", request_id="r", to="cso",
                     question="New choice?", why_blocked="still blocked")
    try:
        await hub.orchestrator.answer_ask(ask, "origin")
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "rejected" and "상한" in answer["reason"]
        assert calls == [], "no consult after the logical step/request cap"
    finally:
        hub.store.close()


@pytest.mark.parametrize("pending", ["asks", "jobs", "both"])
@pytest.mark.parametrize("mode", ["dag", "direct"])
async def test_4148905734_wake_exhaustion_is_terminal(tmp_path, pending, mode):
    hub = Hub(settings(tmp_path))
    hub.s.orchestrator.max_wake_cycles = 2
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use A", "depends_on": ["A"]}]
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running", "mode": mode,
                         "agent_id": "worker", "plan": {"steps": steps}, "results": {}}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="blocked",
                          pending_asks=["ask"] if pending != "jobs" else [],
                          pending_jobs=["job"] if pending != "asks" else [])

    async def asks(ids):
        return [{"status": "answered", "answer": "choice"}]

    async def jobs(tid):
        return {"jobs": []}

    hub.dispatch, hub.wait_asks, hub.wait_jobs = dispatch, asks, jobs
    try:
        if mode == "dag":
            results = hub.result_map("r")
            await hub.orchestrator.run_dag("r", "study", steps, results)
            result = results["A"]
            assert results["B"].error.startswith("skipped: upstream A")
            assert hub.requests["r"]["results"]["A"]["ok"] is False
        else:
            await hub.orchestrator.run_request("r")
            result = TaskResult.model_validate(hub.requests["r"]["results"]["direct"])
            assert hub.requests["r"]["status"] == "failed"
        assert len(calls) == 3
        assert not result.ok and result.error_kind == "wake_limit"
        assert "wake" in result.error and "2" in result.error
        assert not waiting(result)
    finally:
        hub.store.close()


async def settle(hub):
    if hub.ask_tasks:
        await asyncio.gather(*list(hub.ask_tasks.values()))
    await asyncio.sleep(0)


@pytest.mark.parametrize("state", ["pending", "working"])
@pytest.mark.parametrize("approved", [False, True])
async def test_4148905744_restart_waits_for_resume_decision(tmp_path, state, approved):
    s = settings(tmp_path)
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "direct",
                         "agent_id": "worker", "text": "study"}
    hub.save_request("r")
    ask = AskRequest(task_id="old", agent_id="worker", request_id="r", to="cso",
                     question="Choice?", why_blocked="missing choice")
    hub.store.put("ask", ask.id, {"state": state, "origin": "origin", "ask": ask.model_dump(mode="json")})
    hub.store.close()
    hub = Hub(s)
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="choice")

    async def resume(rid):
        # Keep the request waiting for its worker; the CSO runner is already connected.
        assert hub.requests[rid]["status"] == "waiting_for_runner"

    async def send(runner_id, message):
        assert runner_id == "origin" and message["type"] == "ask.resolved"

    hub.dispatch, hub.resume_when_ready = dispatch, resume
    hub.send_runner = send
    hub.agents = {"cso": {"engine": "mock"}}
    try:
        assert hub.requests["r"]["status"] == "interrupted"
        await hub.flush_ask_answers("origin")
        await settle(hub)
        assert calls == [], "consult dispatch must wait for PI approval"
        assert hub.store.get("ask", ask.id)["state"] == state
        aid = next(aid for aid, entry in hub.approvals.items() if entry["approval"]["kind"] == "resume")
        await hub.resolve_approval(aid, approved)
        await settle(hub)
        entry = hub.store.get("ask", ask.id)
        if approved:
            assert len(calls) == 1 and calls[0].meta["kind"] == "consult"
            assert entry["answer"]["status"] == "answered"
        else:
            assert calls == [] and entry["answer"]["status"] == "rejected"
            assert "resume declined" in entry["answer"]["reason"]
        # A reconnect cannot dispatch or resolve the ask a second time.
        await hub.flush_ask_answers("origin")
        await settle(hub)
        assert len(calls) == int(approved)
    finally:
        hub.store.close()


@pytest.mark.parametrize("status", ["failed", "cancelled", "rejected", "done"])
async def test_4148905744_terminal_requests_discard_asks(tmp_path, status):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "status": status, "error": "request stopped"}
    ask = AskRequest(task_id="old", agent_id="worker", request_id="r", to="cso",
                     question="Choice?", why_blocked="missing choice")
    hub.store.put("ask", ask.id, {"state": "working", "origin": "origin", "ask": ask.model_dump(mode="json")})

    async def forbidden(*args):
        pytest.fail("terminal requests cannot dispatch consults")

    hub.orchestrator.answer_ask = forbidden
    try:
        await hub.flush_ask_answers("origin")
        await settle(hub)
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "rejected" and "request stopped" in answer["reason"]
    finally:
        hub.store.close()


@pytest.mark.parametrize("step", ["A", None], ids=["dag", "direct"])
async def test_logical_cap_applies_during_actual_wakes(tmp_path, step):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "status": "running"}
    hub.agents = {"cso": {"engine": "mock"}}
    workers, consults = [], []

    async def dispatch(task):
        if task.meta.get("kind") == "consult":
            consults.append(task)
            return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="choice")
        workers.append(task)
        hub.store.put("task", task.id, {"request_id": task.request_id, **task.meta,
                                        "payload": task.model_dump(mode="json")})
        ask = AskRequest(task_id=task.id, agent_id="worker", request_id="r", to="cso",
                         question=f"Choice {len(workers)}?", why_blocked="blocked")
        await hub.orchestrator.answer_ask(ask, "origin")
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, pending_asks=[ask.id])

    hub.dispatch = dispatch
    try:
        task = save_task(hub, "first", step=step)
        result = await hub.orchestrator.run_step(task)
        assert not result.ok and result.error_kind == "ask_rejected"
        assert "상한" in result.error
        assert len(workers) == 3 and len(consults) == 2
        assert len({task.id for task in workers}) == 3
    finally:
        hub.store.close()


@pytest.mark.parametrize("rid,step", [("r", "B"), ("other", "A")])
async def test_other_steps_and_requests_have_separate_caps(tmp_path, rid, step):
    hub = Hub(settings(tmp_path))
    hub.agents = {"cso": {"engine": "mock"}}
    for i in range(2):
        save_task(hub, f"old-{i}")
        ask = AskRequest(task_id=f"old-{i}", request_id="r", agent_id="worker", to="cso",
                         question=f"Old {i}?", why_blocked="blocked")
        hub.store.put("ask", ask.id, {"state": "resolved", "ask": ask.model_dump(),
                                      "answer": {"status": "answered", "answer": "choice"}})
    save_task(hub, "new", rid, step)
    ask = AskRequest(task_id="new", request_id=rid, agent_id="worker", to="cso",
                     question="New?", why_blocked="blocked")

    async def dispatch(task):
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="choice")

    hub.dispatch = dispatch
    try:
        await hub.orchestrator.answer_ask(ask, "origin")
        assert hub.store.get("ask", ask.id)["answer"]["status"] == "answered"
    finally:
        hub.store.close()


async def test_wake_limit_cannot_restore_old_revision_success(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.s.orchestrator.max_wake_cycles = 0
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use A", "depends_on": ["A"]}]
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running", "results": {}}

    async def dispatch(task):
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, pending_asks=["ask"])

    hub.dispatch = dispatch
    results = hub.result_map("r")
    results["A"] = TaskResult(task_id="old", agent_id="worker", ok=True, text="prior revision")
    try:
        await hub.orchestrator.run_dag("r", "study", steps, results, feedback={"A": "revise"})
        assert not results["A"].ok and results["A"].error_kind == "wake_limit"
        assert results["B"].error.startswith("skipped: upstream A")
    finally:
        hub.store.close()


async def test_request_changes_before_scheduled_route(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "status": "running"}
    ask = AskRequest(task_id="t", request_id="r", agent_id="worker", to="cso",
                     question="Choice?", why_blocked="blocked")
    hub.store.put("ask", ask.id, {"state": "pending", "origin": "origin", "ask": ask.model_dump()})

    async def forbidden(*args):
        pytest.fail("request became interrupted before routing")

    hub.orchestrator.answer_ask = forbidden
    try:
        hub._start_ask(ask, "origin")
        hub.requests["r"]["status"] = "interrupted"
        await settle(hub)
        assert hub.store.get("ask", ask.id)["state"] == "pending"
    finally:
        hub.store.close()


async def test_terminal_checkpoint_cancels_working_ask(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "status": "running"}
    ask = AskRequest(task_id="t", request_id="r", agent_id="worker", to="cso",
                     question="Choice?", why_blocked="blocked")
    hub.store.put("ask", ask.id, {"state": "pending", "origin": "origin", "ask": ask.model_dump()})
    started = asyncio.Event()

    async def route(*args):
        started.set()
        await asyncio.Future()

    hub.orchestrator.answer_ask = route
    try:
        hub._start_ask(ask, "origin")
        previous = hub.ask_tasks[ask.id]
        await asyncio.wait_for(started.wait(), 1)
        hub.requests["r"].update(status="cancelled", error="PI cancelled")
        hub.commit_terminal("r", "request.failed", {"error": "PI cancelled"})
        await settle(hub)
        await asyncio.gather(previous, return_exceptions=True)
        assert previous.cancelled()
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "rejected" and "PI cancelled" in answer["reason"]
        event = next(e for e in hub.events if e["type"] == "agent.answer")
        assert event["data"]["to"] == "worker" and event["data"]["question"] == "Choice?"
    finally:
        hub.store.close()
