"""Consult resource isolation and answered asks in transient retries (PR #76)."""

import asyncio

import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, Task, TaskResult
from labhq.orchestrator.cso import ASK_WAKE_PROMPT
from labhq.settings import Settings


@pytest.fixture
def hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.orchestrator.step_max_attempts = 3
    settings.orchestrator.step_retry_backoff_s = 0
    hub = Hub(settings)
    hub.agents = {a: {"engine": "claude_code"} for a in ("cso", "facilities", "worker")}
    hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running",
                         "cso_session_id": "shared-session", "cso_workdir": str(tmp_path / "shared")}
    yield hub
    hub.store.close()


def question(index, target="cso"):
    return AskRequest(task_id=f"source-{index}", agent_id="worker", request_id="r", to=target,
                      question=f"Which cohort for comparison {index}?", why_blocked="cohort missing")


def prior_task(hub, target, *, session="shared-session", workdir=None, completed=True):
    task = Task(id="prior", agent_id=target, request_id="r", prompt="compare",
                resume_session_id=session, meta={"kind": "step", "step_id": "A", "workdir": workdir})
    hub.store.put("task", task.id, {"request_id": "r", "kind": "step", "accepted": True,
                                   "completed": completed, "dispatched_at": 1,
                                   "payload": task.model_dump(mode="json"),
                                   "result": {"session_id": session, "workdir": workdir} if completed else {}})
    return task


@pytest.mark.parametrize("target", ["cso", "facilities"])
@pytest.mark.parametrize("resource", ["both", "session", "workdir"])
async def test_4149114506_shared_consults_never_overlap(hub, target, resource):
    session = "shared-session" if resource != "workdir" else None
    workdir = hub.requests["r"]["cso_workdir"] if resource != "session" else None
    hub.requests["r"].update(cso_session_id=session, cso_workdir=workdir)
    prior_task(hub, target, session=session, workdir=workdir)
    # _last_agent_session requires a session. CSO also retains a workdir-only context.
    if target == "facilities" and resource == "workdir":
        prior_task(hub, target, workdir=workdir)
        hub.supports_resume = lambda _agent: False
    active = maximum = 0
    calls = []

    async def dispatch(task):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append(task)
        await asyncio.sleep(0.02)
        active -= 1
        return TaskResult(task_id=task.id, agent_id=target, ok=True, text="use cases",
                          session_id=f"consult-session-{len(calls)}", workdir=workdir)

    hub.dispatch = dispatch
    await asyncio.gather(*(hub.orchestrator.answer_ask(question(i, target), "origin") for i in range(2)))
    assert maximum == 1
    assert len(calls) == 2
    if target == "cso":
        assert calls[1].resume_session_id == "consult-session-1"
        assert hub.requests["r"]["cso_session_id"] == "consult-session-2"
    assert not hub.orchestrator.consult_locks


@pytest.mark.parametrize("target", ["cso", "facilities"])
@pytest.mark.parametrize("collision", ["session", "workdir"])
async def test_4149114506_running_step_gets_separate_consult(hub, target, collision):
    workdir = hub.requests["r"]["cso_workdir"]
    prior_task(hub, target, workdir=workdir)
    running = Task(agent_id=target, request_id="r", prompt="continue comparison",
                   resume_session_id="shared-session" if collision == "session" else "other-session",
                   meta={"kind": "step", "step_id": "B",
                         "workdir": workdir if collision == "workdir" else str(workdir) + "-other"})
    entered, finish = asyncio.Event(), asyncio.Event()
    consults = []

    async def dispatch(task):
        if task.meta["kind"] == "step":
            hub.store.put("task", task.id, {"request_id": "r", "kind": "step", "accepted": True,
                                           "completed": False, "payload": task.model_dump(mode="json")})
            entered.set()
            await finish.wait()
        else:
            consults.append(task)
        return TaskResult(task_id=task.id, agent_id=target, ok=True, text="use cases",
                          session_id="isolated", workdir="isolated-workdir")

    hub.dispatch = dispatch
    step = asyncio.create_task(hub.orchestrator.run_step(running))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await hub.orchestrator.answer_ask(question(0, target), "origin")
        assert len(consults) == 1
        assert consults[0].resume_session_id is None
        assert "workdir" not in consults[0].meta
    finally:
        finish.set()
        await step


async def test_consult_lock_cleanup_with_cancelled_waiter(hub):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(task):
        calls.append(task)
        started.set()
        await finish.wait()
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="use cases")

    hub.dispatch = dispatch
    first = asyncio.create_task(hub.orchestrator.answer_ask(question(0), "origin"))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(hub.orchestrator.answer_ask(question(1), "origin"))
        # Give the second coroutine a turn to register as a waiter.
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert hub.orchestrator.consult_locks[("r", "cso")][1] == 2
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        assert hub.orchestrator.consult_locks[("r", "cso")][1] == 1
    finally:
        finish.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert not hub.orchestrator.consult_locks


async def test_consult_lock_cleanup_on_dispatch_cancellation(hub):
    async def dispatch(task):
        raise asyncio.CancelledError

    hub.dispatch = dispatch
    with pytest.raises(asyncio.CancelledError):
        await hub.orchestrator.answer_ask(question(0), "origin")
    assert not hub.orchestrator.consult_locks


async def test_consult_locks_do_not_block_other_requests(hub):
    hub.requests["other"] = {"id": "other", "text": "independent comparison"}
    both_started = asyncio.Event()
    active = 0

    async def dispatch(task):
        nonlocal active
        active += 1
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="use cases")

    hub.dispatch = dispatch
    await asyncio.gather(hub.orchestrator.answer_ask(question(0), "origin"),
                         hub.orchestrator.answer_ask(question(1).model_copy(update={"request_id": "other"}),
                                                     "origin"))
    assert not hub.orchestrator.consult_locks


@pytest.mark.parametrize("completed", [True, False])
async def test_consult_only_avoids_unfinished_resources(hub, completed):
    workdir = hub.requests["r"]["cso_workdir"]
    prior_task(hub, "cso", workdir=workdir, completed=completed)
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="use cases")

    hub.dispatch = dispatch
    await hub.orchestrator.answer_ask(question(0), "origin")
    assert calls[0].resume_session_id == ("shared-session" if completed else None)
    assert calls[0].meta.get("workdir") == (workdir if completed else None)


@pytest.mark.parametrize("engine", ["claude_code", "gemini", "cli"])
@pytest.mark.parametrize("raises", [False, True], ids=["result", "exception"])
async def test_4149114513_retry_preserves_in_session_answers(hub, engine, raises):
    hub.agents["worker"] = {"engine": engine}
    calls = []
    answer = {"status": "answered", "answer": "Use cases cohort C7", "from": "cso"}
    expected = ASK_WAKE_PROMPT.format(answers="- cso: Use cases cohort C7")

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            hub.store.put("ask", "answered", {"state": "resolved", "ask": {"task_id": task.id},
                                               "answer": answer})
        if len(calls) < 3:
            if raises:
                raise TimeoutError("transient timeout")
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error="transient timeout",
                              text="partial comparison", session_id="attempt-session", workdir="attempt-workdir")
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="finished")

    hub.dispatch = dispatch
    task = Task(agent_id="worker", request_id="r", prompt="Compare C7 with controls",
                context="Keep age matched", meta={"kind": "step", "step_id": "A"})
    result = await hub.orchestrator.run_step(task)
    assert result.ok and len(calls) == 3
    for retry in calls[1:]:
        assert expected in retry.prompt
        resumable = engine == "claude_code" and not raises
        assert retry.resume_session_id == ("attempt-session" if resumable else None)
        if not resumable:
            assert task.prompt in retry.prompt and task.context in retry.prompt


@pytest.mark.parametrize("engine", ["cli", "gemini"])
async def test_consult_is_refused_when_the_target_engine_cannot_stay_read_only(hub, engine):
    hub.agents["facilities"] = {"engine": engine}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="facilities", ok=True, text="edited the pipeline")

    hub.dispatch = dispatch
    ask = question(0, "facilities")
    await hub.orchestrator.answer_ask(ask, "origin")
    assert calls == []
    answer = hub.store.get("ask", ask.id)["answer"]
    assert answer["status"] == "rejected" and "읽기 전용" in answer["reason"] and engine in answer["reason"]
