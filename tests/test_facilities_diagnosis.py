"""Automatic facilities diagnosis uses fake runners, never a model or installer."""
import asyncio
import time

import pytest

from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from tests.test_consult_restart import CaptureSocket, settings, eventually, consults
from tests.test_facilities_fixes import FixHub
from labhq.facilities.signatures import diagnosis_evidence
from labhq.models import AskRequest
from labhq.orchestrator.cso import Orchestrator


def make_hub(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "team",
                         "text": "PRIVATE REQUEST", "plan": {"secret": "PRIVATE PLAN"}, "budget_usd": 10}
    hub.register_runner("local", CaptureSocket(), [
        {"id": "worker", "engine": "claude_code"}, {"id": "facilities", "engine": "codex"}], "inc-1")
    return hub


def step():
    return Task(agent_id="worker", request_id="r", prompt="PRIVATE INSTRUCTION",
                meta={"kind": "step", "step_id": "s1"})


def fake_dispatch(hub, *, error="Cannot connect to the Docker daemon", consult_error=None, step_cost=0):
    calls = []

    async def dispatch(task):
        calls.append(task)
        consult = task.meta.get("kind") == "consult"
        failure = consult_error if consult else error
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=not failure,
                          error=failure, text="Check Docker" if not failure else "",
                          cost_usd=0 if consult else step_cost)

    hub.dispatch = dispatch
    return calls


@pytest.mark.asyncio
async def test_unresolved_environment_gets_one_read_only_consult(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    try:
        outcome = await hub.orchestrator.run_step(step())
        assert [t.meta["kind"] for t in calls] == ["step", "consult"]
        assert not outcome.ok and outcome.environment["id"] == "docker_daemon_down"
        assert outcome.error == "Cannot connect to the Docker daemon"
        consult = calls[-1]
        assert consult.agent_id == "facilities"
        assert consult.meta["agent_overrides"]["sandbox"] == "read-only"
        assert "PRIVATE" not in consult.prompt and consult.resume_session_id is None
        await hub.orchestrator.run_step(step())
        assert sum(t.meta["kind"] == "consult" for t in calls) == 1
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["Cannot connect to the Docker daemon", "HTTP 503 overloaded",
                                   "quota exceeded", "authentication required"])
async def test_diagnosis_failure_never_recurses_or_retries(tmp_path, error):
    hub = make_hub(tmp_path)
    hub.s.orchestrator.step_max_attempts = 3
    calls = fake_dispatch(hub, consult_error=error)
    try:
        outcome = await hub.orchestrator.run_step(step())
        assert [t.meta["kind"] for t in calls] == ["step", "consult"]
        assert not outcome.ok and outcome.environment["hint"]
        assert len(hub.store.all("ask")) == 1
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["consult", "facilities_fix", "followup"])
async def test_auxiliary_failure_does_not_diagnose(tmp_path, kind):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub, consult_error="Cannot connect to the Docker daemon")
    try:
        await hub.orchestrator.run_step(step().model_copy(update={"meta": {"kind": kind}}))
        assert len(calls) == 1 and not hub.store.all("ask")
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, "ValueError: bad input", "HTTP 503 overloaded"])
async def test_other_outcomes_do_not_diagnose(tmp_path, error):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub, error=error)
    try:
        await hub.orchestrator.run_step(step())
        assert len(calls) == 1 and not hub.store.all("ask")
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "offline", "budget", "reserved_budget", "durable_budget", "unknown_budget",
                                  "cancelled", "asks"])
async def test_unavailable_diagnosis_keeps_original_error(tmp_path, mode):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub, step_cost=1.0 if mode == "reserved_budget" else 0)
    if mode == "missing":
        hub.agents.pop("facilities")
    elif mode == "offline":
        hub.runners.clear()
    elif mode == "budget":
        # The older check: a request that has already spent its budget still sends the step, and only the optional
        # diagnosis is refused. With the hold on (#1139) such a step itself asks first, see "reserved_budget".
        hub.s.policy.budget.reserve_before_dispatch = False
        hub.requests["r"]["budget_usd"] = 1
        hub.orchestrator.cost["r"] = 1
    elif mode == "reserved_budget":
        # With the hold on: the step holds the $1 the request has, spends it, and the optional diagnosis finds nothing left.
        hub.requests["r"]["budget_usd"] = 1
    elif mode == "durable_budget":
        hub.requests["r"]["cost_usd"] = 10
    elif mode == "unknown_budget":
        hub.s.policy.budget.reserve_before_dispatch = False  # the older check, as in "budget"
        hub.requests["r"]["cost_known"] = False
        hub.requests["r"]["budget_usd"] = 1
    elif mode == "cancelled":
        hub.requests["r"]["cancelling"] = {"reason": "PI"}
    else:
        for i in range(3):
            ask = AskRequest(to="cso", task_id=f"prior{i}", request_id="r", question="q", why_blocked="b")
            hub.store.put("ask", ask.id, {"ask": ask.model_dump(), "scope": ["r", "step", "s1"],
                                         "state": "resolved", "answer": {"status": "answered"}})
    try:
        outcome = await hub.orchestrator.run_step(step())
        assert len(calls) == 1
        assert outcome.error == "Cannot connect to the Docker daemon" and outcome.environment["hint"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,rerun_ok", [({"approved": True, "status": "approved"}, True),
    ({"approved": True, "status": "approved"}, False),
    ({"approved": False, "status": "declined"}, False),
    ({"approved": False, "status": "timed_out"}, False)])
async def test_approved_recovery_has_priority_and_never_diagnoses(decision, rerun_ok):
    hub = FixHub(decision=decision, rerun_ok=rerun_ok)

    async def forbidden(*args):
        pytest.fail("diagnosis after approval recovery")

    hub.request_facilities_diagnosis = forbidden
    await Orchestrator(hub).run_step(step())
    assert len(hub.fix_requests) == 1


@pytest.mark.asyncio
async def test_concurrent_claims_and_restart_use_the_same_ask(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    await asyncio.gather(*(hub.orchestrator.run_step(step()) for _ in range(2)))
    assert sum(t.meta["kind"] == "consult" for t in calls) == 1
    ask_id = hub.requests["r"]["facilities_diagnoses"]["s1"]
    hub.store.close()
    second = Hub(settings(tmp_path))
    second.requests["r"]["status"] = "running"
    second.register_runner("local", CaptureSocket(), [
        {"id": "worker", "engine": "claude_code"}, {"id": "facilities", "engine": "codex"}], "inc-2")
    calls = fake_dispatch(second)
    try:
        second._restart_request_asks("r")
        await second.orchestrator.run_step(step())
        assert len(calls) == 1 and list(second.store.all("ask")) == [ask_id]
    finally:
        second.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [True, False])
async def test_restart_adopts_prior_diagnosis_without_redispatch(tmp_path, known):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    await hub.orchestrator.run_step(step())
    ask_id = hub.requests["r"]["facilities_diagnoses"]["s1"]
    entry = hub.store.get("ask", ask_id)
    hub.store.put("ask", ask_id, {**entry, "state": "working"})
    prior = calls[-1]
    saved = {"kind": "consult", "request_id": "r", "payload": prior.model_dump(),
             "attempt": 1, "completed": known, "runner_id": "local", "runner_incarnation": "old"}
    if known:
        saved["result"] = TaskResult(task_id=prior.id, agent_id="facilities", ok=True,
                                     text="Saved diagnosis", cost_usd=0).model_dump()
    hub.store.put("task", prior.id, saved)
    hub.store.close()
    second = Hub(settings(tmp_path))
    second.requests["r"]["status"] = "running"
    second.register_runner("local", CaptureSocket(), [
        {"id": "worker", "engine": "claude_code"}, {"id": "facilities", "engine": "codex"}], "new")
    calls = fake_dispatch(second)
    try:
        second._restart_request_asks("r")
        await asyncio.gather(*list(second.ask_tasks.values()))
        assert not calls
        assert second.store.get("ask", ask_id)["state"] == "resolved"
        await second.orchestrator.run_step(step())
        assert len(calls) == 1
    finally:
        second.store.close()


@pytest.mark.asyncio
async def test_deadline_survives_restart_without_starting_consult(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    await hub.orchestrator.run_step(step())
    ask_id = hub.requests["r"]["facilities_diagnoses"]["s1"]
    entry = hub.store.get("ask", ask_id)
    hub.store.put("ask", ask_id, {**entry, "state": "working", "deadline_at": time.time() - 1})
    calls.clear()
    try:
        hub._restart_request_asks("r")
        await asyncio.gather(*list(hub.ask_tasks.values()))
        assert not calls and "시간 초과" in hub.store.get("ask", ask_id)["answer"]["reason"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_cancel_between_claim_and_route_does_not_dispatch(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    start = hub._start_ask

    def cancel_first(ask, runner):
        hub.requests["r"]["status"] = "cancelled"
        start(ask, runner)

    hub._start_ask = cancel_first
    try:
        outcome = await hub.orchestrator.run_step(step())
        assert len(calls) == 1 and not outcome.ok
        assert len(hub.store.all("ask")) == 1
    finally:
        hub.store.close()


def test_diagnosis_evidence_excludes_paths_tokens_and_sender_hint():
    value = diagnosis_evidence({"id": "docker_daemon_down", "hint": "PRIVATE", "cause": "PRIVATE"},
        "Cannot connect to the Docker daemon at /private/data token=PRIVATE\nPRIVATE")
    assert "PRIVATE" not in value and "/private" not in value
    assert "Cannot connect to the Docker daemon" in value


@pytest.mark.asyncio
async def test_dag_diagnoses_failure_once_and_skips_downstream(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    results = {}
    try:
        await hub.orchestrator.run_dag("r", "PRIVATE", [
            {"id": "s1", "agent_id": "worker", "instruction": "fail", "depends_on": []},
            {"id": "s2", "agent_id": "worker", "instruction": "skip", "depends_on": ["s1"]}], results)
        assert [t.meta["kind"] for t in calls] == ["step", "consult"]
        assert not results["s1"].ok and results["s1"].environment["hint"]
        assert results["s2"].error.startswith("skipped:")
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["timeout", "cancel"])
async def test_running_consult_receives_cancel_and_cannot_be_started_again(tmp_path, end):
    hub = make_hub(tmp_path)
    if end == "timeout":
        hub.s.runner.task_timeout_s = 1
    socket = hub.runners["local"]
    original = TaskResult(task_id="original", agent_id="worker", ok=False,
                          error="Cannot connect to the Docker daemon",
                          environment={"id": "docker_daemon_down", "source": "error"})
    running = asyncio.create_task(hub.request_facilities_diagnosis(step(), original, "s1"))
    try:
        await eventually(lambda: consults(socket))
        tid = consults(socket)[0]["id"]
        if end == "cancel":
            hub.cancel_result_wait_s = 0
            await hub.cancel_request("r")
        await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), 3)
        assert any(f["type"] == "task.cancel" and f["task_id"] == tid for f in socket.sent)
        await hub.request_facilities_diagnosis(step(), original, "s1")
        assert len(consults(socket)) == 1
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        hub.store.close()
