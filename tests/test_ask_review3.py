"""Consult failure and ask-only hibernation contracts (PR #76 review 3)."""

import asyncio
import sys

import pytest

from labhq.gateway.server import Hub
from labhq.models import AgentSpec, AskRequest, Engine, RunnerUnavailable, Task, TaskResult
from labhq.orchestrator.cso import failure_kind
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.orchestrator.step_max_attempts = 1
    s.orchestrator.step_retry_backoff_s = 0
    return s


@pytest.fixture
def waiting_calls(monkeypatch):
    """Prove live decisions use the shared predicate, including delegated callers."""
    from labhq.models import waiting

    calls = []

    def tracked(result, **kwargs):
        calls.append(sys._getframe(1).f_code.co_name)
        return waiting(result, **kwargs)

    for module in ("labhq.gateway.server", "labhq.orchestrator.cso", "labhq.runner.daemon"):
        monkeypatch.setattr(f"{module}.waiting", tracked)
    return calls


@pytest.mark.parametrize("target", ["cso", "facilities", "colleague:peer"])
@pytest.mark.parametrize("failure", ["offline", "cli", "empty"])
async def test_failed_consult_is_rejected_with_reason(tmp_path, target, failure):
    hub = Hub(settings(tmp_path))
    routed = target.removeprefix("colleague:")
    hub.agents = {routed: {"engine": "mock"}}
    ask = AskRequest(task_id="blocked", agent_id="worker", request_id="r", to=target,
                     question="Cases or controls?", why_blocked="cohort missing")

    async def dispatch(task):
        if failure == "offline":
            raise RunnerUnavailable("runner offline")
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=failure == "empty",
                          text="ignored error output" if failure == "cli" else " \n",
                          error="CLI exit 2" if failure == "cli" else None)

    hub.dispatch = dispatch
    try:
        await hub.orchestrator.answer_ask(ask, "offline-origin")
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "rejected"
        assert answer["routed_to"] == routed
        assert {"offline": "runner offline", "cli": "CLI exit 2", "empty": "empty"}[failure] in answer["reason"]
        assert "answer" not in answer
        assert (await hub.wait_asks([ask.id]))[0] == answer
        assert hub.events[-1]["data"]["status"] == "rejected"
    finally:
        hub.store.close()


@pytest.mark.parametrize("failure", ["offline", "cli", "empty"])
async def test_blocking_decision_failed_consult_never_resumes_step(tmp_path, failure):
    hub = Hub(settings(tmp_path))
    hub.agents = {"worker": {"engine": "mock"}, "cso": {"engine": "mock"}}
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare cohorts", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use comparison", "depends_on": ["A"]}]
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running", "mode": "orchestrate",
                         "plan": {"steps": steps}, "results": {}}
    calls = []

    async def dispatch(task):
        calls.append(task)
        if task.meta["kind"] == "consult":
            if failure == "offline":
                raise RunnerUnavailable("runner offline")
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=failure == "empty",
                              error="CLI exit 2" if failure == "cli" else None)
        if len([t for t in calls if t.meta["kind"] == "step"]) == 1:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              text="blocked", blocking_decision="Cases or controls?")
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="resumed unsafely")

    hub.dispatch = dispatch
    try:
        await hub.orchestrator.run_dag("r", "study", steps, hub.result_map("r"))
        assert [t.meta["step_id"] for t in calls if t.meta["kind"] == "step"] == ["A"]
        assert hub.requests["r"]["pending_questions"] == []
        assert not hub.requests["r"].get("step_decisions")
        saved = hub.requests["r"]["results"]
        assert not saved["A"]["ok"] and saved["A"]["error_kind"] == "ask_rejected"
        assert "Cases or controls?" in saved["A"]["error"]
        assert {"offline": "runner offline", "cli": "CLI exit 2", "empty": "empty"}[failure] in saved["A"]["error"]
        assert not saved["B"]["ok"] and saved["B"]["error"].startswith("skipped: upstream A")
    finally:
        hub.store.close()


def test_ask_only_result_is_successful_wait_not_transient_failure():
    result = TaskResult(task_id="t", agent_id="worker", ok=True, pending_asks=["ask"])
    assert failure_kind(result) is None


async def test_ask_only_empty_result_waits_then_wakes_without_retry(tmp_path, waiting_calls):
    s = settings(tmp_path)
    s.orchestrator.step_max_attempts = 3
    hub = Hub(s)
    entered_wait = asyncio.Event()
    answered = asyncio.Event()
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, pending_asks=["ask"])
        assert entered_wait.is_set() and answered.is_set()
        assert task.meta["parent_task"] == calls[0].id
        assert "cases" in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="finished")

    async def wait_asks(ids):
        assert ids == ["ask"]
        entered_wait.set()
        await answered.wait()
        return [{"answer": "cases", "from": "cso", "status": "answered"}]

    hub.dispatch, hub.wait_asks = dispatch, wait_asks
    running = asyncio.create_task(hub.orchestrator.run_step(Task(agent_id="worker", prompt="compare")))
    try:
        await asyncio.wait_for(entered_wait.wait(), 1)
        assert len(calls) == 1 and not running.done()
        answered.set()
        assert (await running).text == "finished"
        assert len(calls) == 2
        assert not any(e["type"] == "request.step_retry" for e in hub.events)
        assert {"failure_kind", "run_step"} <= set(waiting_calls)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        hub.store.close()


@pytest.mark.parametrize("checkpoint_only", [False, True])
async def test_ask_only_direct_restart_requires_offline_worker(tmp_path, checkpoint_only):
    s = settings(tmp_path)
    hub = Hub(s)
    body = TaskResult(task_id="t", agent_id="worker", ok=True, pending_asks=["ask"]).model_dump(mode="json")
    hub.requests["r"] = {"id": "r", "mode": "direct", "agent_id": "worker", "text": "compare",
                         "status": "running"}
    hub.save_request("r")
    hub.store.put("step_checkpoint", "r:direct", {"result": body})
    if not checkpoint_only:
        hub.store.put("task", "t", {"request_id": "r", "kind": "direct", "accepted": True,
                                    "completed": True, "result": body, "payload": {"agent_id": "worker"}})
    hub.store.close()
    restored = Hub(s)
    resumed = None
    try:
        assert restored.completed_direct_result("r") is None
        assert restored.resume_agents("r") == {"worker"}
        resumed = asyncio.create_task(restored.resume_when_ready("r"))
        await asyncio.sleep(0)
        assert restored.requests["r"]["status"] == "waiting_for_runner"
        assert restored.events[-1]["type"] == "request.resume_waiting"
        assert restored.events[-1]["data"]["missing_agents"] == ["worker"]
    finally:
        if resumed:
            resumed.cancel()
            await asyncio.gather(resumed, return_exceptions=True)
        restored.store.close()


@pytest.mark.parametrize("jobs_finished", [False, True])
def test_ask_only_result_counts_as_hibernating_everywhere(tmp_path, jobs_finished, waiting_calls):
    hub = Hub(settings(tmp_path))
    body = TaskResult(task_id="t", agent_id="worker", ok=True, pending_asks=["ask"]).model_dump(mode="json")
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "direct", "agent_id": "worker"}
    hub.store.put("task", "t", {"request_id": "r", "kind": "direct", "accepted": True,
                                "completed": True, "result": body, "payload": {"agent_id": "worker"}})
    if jobs_finished:
        hub.jobs_done["t"] = {}
    try:
        checks = [
            (lambda: [t["state"] for t in hub.running_tasks()], ["hibernating"], "running_tasks"),
            (lambda: hub.request_summary(hub.requests["r"])["step_progress"]["steps"],
             {"direct": "hibernating"}, "running_tasks"),
            (lambda: hub.completed_direct_result("r"), None, "completed_direct_result"),
            (lambda: hub.resume_agents("r"), {"worker"}, "completed_direct_result"),
            (lambda: failure_kind(TaskResult.model_validate(body)), None, "failure_kind"),
        ]
        for check, expected, caller in checks:
            waiting_calls.clear()
            assert check() == expected
            assert caller in waiting_calls, "waiting predicate was bypassed"
    finally:
        hub.store.close()


async def test_runner_ask_only_result_emits_hibernating_status(tmp_path, monkeypatch, waiting_calls):
    runner = Runner(settings(tmp_path))
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    monkeypatch.setattr(runner.broker, "pending_for_task", lambda tid: ["ask"])
    seen = []

    class Adapter:
        async def run(self, ctx):
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True)

    async def emit(event):
        seen.append(event)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    monkeypatch.setattr(runner, "emit", emit)
    try:
        result = await runner.run_task(Task(agent_id="worker", prompt="compare"))
        assert result.pending_asks == ["ask"] and not result.pending_jobs
        status = [e.data for e in seen if e.type == "agent.status"][-1]
        assert status == {"state": "hibernating", "jobs": [], "asks": ["ask"]}
        assert waiting_calls.count("run_task") == 2  # state and event payload
    finally:
        runner.store.close()


@pytest.mark.parametrize("as_dict", [False, True])
@pytest.mark.parametrize(("jobs", "asks", "jobs_finished", "expected"), [
    ([], [], False, False), (["job"], [], False, True),
    (["job"], [], True, False), ([], ["ask"], False, True),
    ([], ["ask"], True, True), (["job"], ["ask"], True, True),
])
def test_waiting_live_and_persisted_results(as_dict, jobs, asks, jobs_finished, expected):
    from labhq.models import waiting

    result = TaskResult(task_id="t", agent_id="worker", ok=True, pending_jobs=jobs, pending_asks=asks)
    assert waiting(result.model_dump() if as_dict else result, jobs_finished=jobs_finished) is expected
