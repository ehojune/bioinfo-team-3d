"""Runner-only shutdown must leave background run_step callers retryable (#655)."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from labhq.gateway.server import Hub
from labhq.interruption import ShutdownInterrupted
from labhq.models import AskRequest, Task, TaskResult
from labhq.recruit import paper_skills, planning
from labhq.settings import Settings


@pytest.fixture
def hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    instance = Hub(settings)
    instance.agents = {a: {"id": a, "engine": "mock"} for a in ("cso", "worker", "editor")}
    instance.requests["r"] = {"id": "r", "text": "Explain the public report", "status": "done",
                              "mode": "orchestrate", "report": "Public findings", "pi_reviewed_at": 1}
    instance.save_request("r")
    yield instance
    instance.store.close()


async def stopped(task):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, exit_code=143,
                      error="runner stopping", shutdown_evidence={"stopping": True, "exit_code": 143,
                                                                  "source": "runner"})


async def answer(task):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="Public answer",
                      structured={"readme": "Edited public findings"})


def followup(hub):
    entry = {"id": "fu", "text": "Why?", "agent_id": "cso", "status": "running"}
    hub.requests["r"]["followups"] = [entry]
    return entry


def ask(hub):
    hub.requests["r"]["status"] = "running"
    question = AskRequest(task_id="original", agent_id="worker", request_id="r", to="cso",
                          question="Which public study?", why_blocked="study missing")
    hub.store.put("ask", question.id, {"ask": question.model_dump(mode="json"), "origin": "origin"})
    return question


def editor(hub):
    hub.requests["r"]["result_storage"] = {"mode": "existing", "repo": "owner/study", "status": "selected"}
    hub.request_approval = AsyncMock(return_value={"approved": False, "state": "timed_out"})
    hub.result_storage.upload = AsyncMock(side_effect=AssertionError("no real publication"))


def test_shutdown_is_an_exception_but_not_asyncio_cancellation():
    assert issubclass(ShutdownInterrupted, Exception)
    assert not issubclass(ShutdownInterrupted, asyncio.CancelledError)


async def test_runner_shutdown_followup_can_be_asked_again(hub, monkeypatch):
    entry = followup(hub)
    hub.dispatch = stopped
    await hub.orchestrator.run_followup("r", "fu")
    assert entry["status"] == "interrupted"
    assert hub.store.get("request", "r")["followups"][0]["status"] == "interrupted"
    assert hub.requests["r"]["status"] == "done" and not hub.stopping
    hub.dispatch = answer
    finished = asyncio.Event()
    original = hub.orchestrator.run_followup

    async def again(rid, fid):
        try:
            await original(rid, fid)
        finally:
            finished.set()

    monkeypatch.setattr(hub.orchestrator, "run_followup", again)
    retry = hub.start_followup("r", "Why, again?")
    await asyncio.wait_for(finished.wait(), 2)
    assert retry["status"] == "done"


async def test_runner_shutdown_consult_resolves_original_waiter(hub):
    question = ask(hub)
    hub.dispatch = stopped
    waiter = asyncio.get_running_loop().create_future()
    hub.ask_waiters[question.id] = waiter
    hub._start_ask(question, "origin")
    await hub.ask_tasks[question.id]
    entry = hub.store.get("ask", question.id)
    assert entry["state"] == "resolved"
    assert waiter.done() and waiter.result()["status"] == "rejected"
    assert waiter.result()["interrupted"] and waiter.result()["retryable"]
    assert hub.requests["r"]["status"] == "running" and not hub.stopping
    assert not hub.orchestrator.consult_locks


@pytest.mark.parametrize("reconnect_at", ["after", "cleanup", "result"])
async def test_interrupted_editor_reschedules_when_runner_returns(hub, monkeypatch, reconnect_at):
    editor(hub)
    roster = [{"id": "editor", "engine": "mock"}]
    old = AsyncMock()
    hub.register_runner("local", old, roster, "inc", process="old")
    hub.dispatch = stopped
    original_publish = hub.publish
    reconnected = False

    def reconnect():
        nonlocal reconnected
        reconnected = True
        hub.unregister_runner("local", old)
        hub.dispatch = answer
        hub.register_runner("local", AsyncMock(), roster, "inc", process="new")

    async def publish(event):
        if (reconnect_at == "cleanup" and not reconnected and event["type"] == "request.storage"
                and event["data"]["result_storage"]["status"] == "interrupted"):
            reconnect()
        await original_publish(event)

    async def late_result(task):
        reconnect()
        return await stopped(task)

    if reconnect_at == "result":
        hub.dispatch = late_result
    monkeypatch.setattr(hub, "publish", publish)
    hub.result_storage.schedule("r")
    first = hub.result_storage.tasks["r"]
    await first
    if reconnect_at == "after":
        assert hub.requests["r"]["result_storage"]["status"] == "interrupted"
        assert hub.store.get("request", "r")["result_storage"]["status"] == "interrupted"
        hub.request_approval.assert_not_awaited()
        reconnect()
    # A reconnect during finish's final publish queues the retry after that task ends.
    await asyncio.sleep(0)
    retry = hub.result_storage.tasks["r"]
    assert retry is not first
    await retry
    assert hub.requests["r"]["result_storage"]["status"] == "draft"
    assert "Edited public findings" in hub.request_approval.await_args.kwargs["detail"]["documents"]["README.md"]
    hub.result_storage.upload.assert_not_awaited()


@pytest.mark.parametrize("caller", ["followup", "ask", "editor", "request", "step"])
async def test_real_asyncio_cancellation_still_propagates(hub, caller):
    async def cancelled(task):
        raise asyncio.CancelledError

    hub.dispatch = cancelled
    if caller == "followup":
        followup(hub)
        job = hub.orchestrator.run_followup("r", "fu")
    elif caller == "ask":
        question = ask(hub)
        hub._start_ask(question, "origin")
        job = hub.ask_tasks[question.id]
    elif caller == "editor":
        editor(hub)
        job = hub.result_storage.finish("r")
    elif caller == "request":
        hub.requests["r"].update(status="running", mode="direct", agent_id="worker")
        job = hub.orchestrator.run_request("r")
    else:
        job = hub.orchestrator.run_step(Task(agent_id="cso", request_id="r", prompt="read"))
    with pytest.raises(asyncio.CancelledError):
        await job


@pytest.mark.parametrize("caller", ["recruit_check", "plan_recruit", "paper_skill"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_plan_preparation_callbacks_propagate_interruption(hub, caller, cancelled):
    import copy
    from labhq.research.contract import plan_sha256
    from tests.test_paper_skill_attach import PRECEDENTS, SELECTED
    from tests.test_recruit_before_plan import proposal

    plan = {"steps": [{"id": "s1", "agent_id": "worker"}], "recruit": [proposal()],
            "paper_skills": [SELECTED]}
    hub.agents["recruiter"] = {"id": "recruiter", "engine": "mock"}
    hub.request_approval = AsyncMock(return_value={"approved": True})

    dispatched = []

    async def dispatch(task):
        dispatched.append(task.meta["kind"])
        if task.meta["kind"] == caller:
            if cancelled:
                raise asyncio.CancelledError
            return await stopped(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured={"status": "ready"})

    hub.dispatch = dispatch
    if caller == "paper_skill":
        from tests.test_research_protocol import valid_plan

        # #798: preparation cannot extract. The callback moves to the approved step,
        # where runner shutdown/cancellation must still escape instead of becoming a failed receipt.
        plan = {**valid_plan(), "paper_skills": [copy.deepcopy(SELECTED)]}
        prepare_callback = AsyncMock(wraps=hub.orchestrator.run_step)
        plan = await paper_skills.prepare_plan(plan, PRECEDENTS, list(hub.agents.values()), "r", hub.s,
                                               prepare_callback)
        prepare_callback.assert_not_awaited()
        assert dispatched == [] and plan["paper_skill_results"][0]["status"] == "deferred"
        frozen = copy.deepcopy(plan)
        digest = plan_sha256(plan)
        hub.requests["r"].update(plan=plan, research_contract={"plan_sha256": digest,
            "approval": {"approved": True, "target_sha256": digest}})
        job = hub.orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="approved fixture",
            meta={"kind": "step", "step_id": "s1",
                  "paper_skills": paper_skills.attachments(plan, "s1", "worker", "r")}))
    else:
        job = planning.prepare_plan(plan, "r", hub, hub.orchestrator.run_step)
    with pytest.raises(asyncio.CancelledError if cancelled else ShutdownInterrupted):
        await job
    if caller == "paper_skill":
        assert dispatched == ["paper_skill"]  # The interrupted build never dispatches the research step.
        assert plan == frozen and plan_sha256(plan) == digest
        assert hub.requests["r"]["research_contract"]["approval"]["target_sha256"] == digest


async def test_precedent_shutdown_interrupts_request_before_planning(tmp_path):
    from labhq.orchestrator.cso import Orchestrator
    from tests.test_topic_checklists_precedents import ParallelHub

    instance = ParallelHub(precedent_result=await stopped(Task(agent_id="lit_scout", prompt="read")))
    instance.s.gateway.state_dir = str(tmp_path / "state")
    await Orchestrator(instance).run_request("r")
    assert instance.requests["r"]["status"] == "interrupted"
    assert instance.requests["r"]["shutdown_evidence"]["kind"] == "precedent"
    assert not any(t.meta["kind"] == "plan" for t in instance.calls)
