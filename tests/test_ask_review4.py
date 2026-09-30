"""Terminal ask outcomes must gate every continuation, including recovered turns."""

import asyncio
import sys

import pytest

from labhq.gateway.server import Hub
from labhq.models import AgentSpec, AskRequest, Engine, RunnerUnavailable, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.orchestrator.step_max_attempts = 1
    s.orchestrator.step_retry_backoff_s = 0
    return s


async def produce(hub, source, rejected=True):
    target = source if source in {"cso", "facilities"} else "colleague:peer" if source == "peer" else "cso"
    ask = AskRequest(task_id="blocked", agent_id="worker", request_id="r", to=target,
                     question="Cases or controls?", why_blocked="cohort missing")
    hub.agents = {a: {"engine": "mock"} for a in ("worker", "cso", "facilities", "peer")}
    if source in {"pi", "timeout"}:
        ask.question = "May I cross the restricted data zone?"

        async def approval(**kwargs):
            return {"approved": not rejected, "note": "timed out" if source == "timeout" and rejected else
                    "PI denied access" if rejected else "use cases"}

        hub.request_approval = approval
    if source in {"task_cap", "target_cap", "request_cap"} and rejected:
        for i in range({"task_cap": 3, "target_cap": 2, "request_cap": 12}[source]):
            seed = AskRequest(task_id=ask.task_id if source != "request_cap" else f"other_{i}",
                              agent_id="worker", request_id="r",
                              to="cso" if source != "task_cap" else ("cso", "facilities", "colleague:peer")[i],
                              question=f"seed {i}", why_blocked="blocked")
            hub.store.put("ask", seed.id, {"state": "resolved", "ask": seed.model_dump(),
                                           "answer": {"status": "answered", "answer": "use cases"}})
    if source == "missing" and rejected:
        hub.agents.pop("peer")
        ask.to = "colleague:peer"

    async def dispatch(task):
        if source == "offline" and rejected:
            raise RunnerUnavailable("runner offline")
        return TaskResult(task_id=task.id, agent_id=task.agent_id,
                          ok=not (source == "cli" and rejected),
                          text="" if rejected else "use cases",
                          error="CLI exit 2" if source == "cli" and rejected else None)

    hub.dispatch = dispatch
    await hub.orchestrator.answer_ask(ask, "origin")
    return hub.store.get("ask", ask.id)["answer"]


@pytest.mark.parametrize("source", ["pi", "timeout"])
async def test_4148598892_pi_denial_is_rejected(tmp_path, source):
    hub = Hub(settings(tmp_path))
    try:
        answer = await produce(hub, source)
        assert answer["status"] == "rejected"
        assert answer.get("reason")
        assert "answer" not in answer
    finally:
        hub.store.close()


@pytest.mark.parametrize("source", ["offline", "cso", "task_cap"])
async def test_4148598903_rejected_wake_fails_and_skips_downstream(tmp_path, source):
    hub = Hub(settings(tmp_path))
    calls = []
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use comparison", "depends_on": ["A"]}]
    hub.requests["r"] = {"id": "r", "text": "study", "plan": {"steps": steps}, "results": {}}
    try:
        answer = await produce(hub, source)

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              text="finished" if len(calls) > 1 else "blocked",
                              pending_asks=[answer["ask_id"]] if len(calls) == 1 else [])

        hub.dispatch = dispatch
        results = hub.result_map("r")
        await hub.orchestrator.run_dag("r", "study", steps, results)
        assert len(calls) == 1
        assert not results["A"].ok and (answer.get("reason") or answer.get("answer")) in results["A"].error
        assert results["B"].error.startswith("skipped: upstream A")
    finally:
        hub.store.close()


SOURCES = ["cso", "facilities", "peer", "pi", "timeout", "task_cap", "target_cap", "request_cap",
           "offline", "cli", "missing"]
CONSUMERS = ["session", "wake", "blocking", "persisted_blocking", "direct_recovery"]


@pytest.fixture
def outcome_calls(monkeypatch):
    """Replacing both functions proves the live paths use the shared contract."""
    from labhq.ask_results import ask_result, read_ask_results

    calls = {"producer": [], "consumer": []}

    def build(**kwargs):
        calls["producer"].append(sys._getframe(1).f_code.co_name)
        return ask_result(**kwargs)

    def read(answers):
        calls["consumer"].append(sys._getframe(1).f_code.co_name)
        return read_ask_results(answers)

    for module in ("labhq.gateway.server", "labhq.orchestrator.cso", "labhq.runner.approvals",
                   "labhq.tools.ask_mcp"):
        monkeypatch.setattr(f"{module}.ask_result", build)
    for module in ("labhq.gateway.server", "labhq.orchestrator.cso", "labhq.runner.approvals",
                   "labhq.runner.daemon"):
        monkeypatch.setattr(f"{module}.read_ask_results", read)
    return calls


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("rejected", [True, False], ids=["rejected", "answered"])
async def test_every_producer_gates_every_consumer(tmp_path, monkeypatch, outcome_calls, source, consumer, rejected):
    hub = Hub(settings(tmp_path))
    runner = None
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use comparison", "depends_on": ["A"]}]
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running", "mode": "orchestrate",
                         "plan": {"steps": steps}, "results": {}}
    calls = []
    try:
        answer = await produce(hub, source, rejected)
        assert answer["status"] == ("rejected" if rejected else "answered")
        assert "answer_ask" in outcome_calls["producer"]
        outcome_calls["consumer"].clear()
        # A successful sibling answer must never mask a rejection, in either order.
        answers = [{"status": "answered", "answer": "sibling answer", "from": "cso"}, answer]

        async def wait_asks(ids):
            return answers

        async def dispatch(task):
            calls.append(task)
            first = len(calls) == 1
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              text="blocked" if first else "finished", session_id="worker-session",
                              pending_asks=["sibling", answer["ask_id"]] if first and consumer == "wake" else [],
                              blocking_decision="Cases or controls?" if first and consumer == "blocking" else None)

        hub.wait_asks, hub.dispatch = wait_asks, dispatch
        if consumer == "session":
            runner = Runner(settings(tmp_path / "runner"))
            agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=[])
            monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)

            async def emit(event):
                pass

            async def on_ask(req):
                runner.broker.resolve_ask(req.id, answer)

            runner.broker._on_ask = on_ask
            runner.emit = emit

            class Adapter:
                async def run(self, ctx):
                    calls.append(ctx.task)
                    received = await runner.broker.request_ask(AskRequest(task_id=ctx.task.id,
                        agent_id="worker", to="cso", question="Cases?", why_blocked="blocked"))
                    assert received["status"] == answer["status"]
                    # An adapter may report success after a rejected MCP result.
                    return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="ignored result")

            monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
            result = await runner.run_task(Task(agent_id="worker", prompt="compare"))
        elif consumer == "direct_recovery":
            hub.requests["r"].update(mode="direct", agent_id="worker")
            body = TaskResult(task_id="blocked", agent_id="worker", ok=True, pending_asks=[answer["ask_id"]],
                              session_id="worker-session", workdir="prior-workdir").model_dump(mode="json")
            hub.store.put("step_checkpoint", "r:direct", {"result": body})
            hub.recovery_steps.add("r")
            # Use the real recovery dispatch until a continuation would be sent.
            original_dispatch = Hub.dispatch.__get__(hub)

            async def recover(task):
                if not task.meta.get("parent_task"):
                    return await original_dispatch(task)
                return await dispatch(task)

            hub.dispatch = recover
            await hub.orchestrator.run_request("r", resume=True)
            result = TaskResult.model_validate(hub.requests["r"]["results"]["direct"])
            assert len(calls) == (0 if rejected else 1)
        elif consumer == "persisted_blocking":
            hub.requests["r"]["step_decisions"] = {"A": {**answer, "question": "Cases?"}}
            results = hub.result_map("r")
            await hub.orchestrator.run_dag("r", "study", steps, results)
            result = results["A"]
            assert len(calls) == (0 if rejected else 2)
            if rejected:
                assert results["B"].error.startswith("skipped: upstream A")
        elif consumer == "blocking":
            # Keep the actual stored answer but avoid another consult in the consumer.
            async def answer_ask(ask, origin):
                await hub.resolve_ask(ask, origin, answer)

            hub.orchestrator.answer_ask = answer_ask
            results = hub.result_map("r")
            await hub.orchestrator.run_dag("r", "study", steps, results)
            result = results["A"]
            assert len(calls) == (1 if rejected else 3)
            if rejected:
                assert results["B"].error.startswith("skipped: upstream A")
        else:
            result = await hub.orchestrator.run_step(Task(agent_id="worker", prompt="compare"))
            assert len(calls) == (1 if rejected else 2)
        assert result.ok is not rejected
        if rejected:
            assert answer["reason"] in result.error
            assert result.error_kind == "ask_rejected"
        assert outcome_calls["consumer"], "shared consumer was bypassed"
    finally:
        if runner:
            runner.store.close()
        hub.store.close()


@pytest.mark.parametrize("reverse", [True, False])
def test_reader_rejects_any_failure_and_legacy_pi_denial(reverse):
    from labhq.ask_results import read_ask_results

    answers = [{"status": "answered", "answer": "safe"},
               {"approved": False, "answer": "unsafe legacy PI note"}]
    outcome = read_ask_results(answers[::-1] if reverse else answers)
    assert outcome["status"] == "rejected" and "unsafe legacy PI note" in outcome["reason"]


async def test_broker_unavailable_uses_shared_producer_and_consumer(outcome_calls):
    from labhq.runner.approvals import Broker

    async def noop(_):
        pass

    broker = Broker(0, noop, noop, noop)
    answer = await broker.request_ask(AskRequest(task_id="t", agent_id="worker", to="cso",
                                              question="Cases?", why_blocked="blocked"))
    assert answer["status"] == "rejected" and "unavailable" in answer["reason"]
    assert outcome_calls["producer"] and outcome_calls["consumer"]


async def test_rejected_ask_does_not_wait_for_jobs_or_retry_timeout(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.s.orchestrator.step_max_attempts = 3
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True,
                          pending_jobs=["job"], pending_asks=["ask"])

    async def wait_asks(ids):
        return [{"status": "rejected", "reason": "approval timed out"}]

    async def wait_jobs(tid):
        pytest.fail("a rejected ask must end the step without waiting for unrelated jobs")

    hub.dispatch, hub.wait_asks, hub.wait_jobs = dispatch, wait_asks, wait_jobs
    try:
        result = await hub.orchestrator.run_step(Task(agent_id="worker", prompt="compare"))
        assert not result.ok and result.error_kind == "ask_rejected"
        assert result.pending_jobs == ["job"] and len(calls) == 1
    finally:
        hub.store.close()


async def test_rejected_revision_is_failed_and_independent_branch_completes(tmp_path):
    hub = Hub(settings(tmp_path))
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Use A", "depends_on": ["A"]},
             {"id": "C", "agent_id": "worker", "instruction": "Independent", "depends_on": []}]
    hub.requests["r"] = {"id": "r", "text": "study", "plan": {"steps": steps}, "results": {},
        "step_decisions": {"A": {"status": "rejected", "reason": "PI denied access", "question": "Access?"}}}
    calls = []

    async def dispatch(task):
        calls.append(task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="independent done")

    hub.dispatch = dispatch
    results = hub.result_map("r")
    results["A"] = TaskResult(task_id="old", agent_id="worker", ok=True, text="previous version")
    try:
        await hub.orchestrator.run_dag("r", "study", steps, results, feedback={"A": "revise"})
        assert not results["A"].ok and results["A"].error_kind == "ask_rejected"
        assert results["B"].error.startswith("skipped: upstream A")
        assert results["C"].ok and calls == ["C"]
    finally:
        hub.store.close()


@pytest.mark.parametrize("pending", [False, True])
async def test_direct_restart_cannot_reuse_success_with_rejected_ask(tmp_path, pending, outcome_calls):
    s = settings(tmp_path)
    hub = Hub(s)
    body = TaskResult(task_id="blocked", agent_id="worker", ok=True, text="ignored denial",
                      pending_asks=["ask"] if pending else []).model_dump(mode="json")
    hub.requests["r"] = {"id": "r", "mode": "direct", "agent_id": "worker", "text": "compare",
                         "status": "running"}
    hub.save_request("r")
    hub.store.put("task", "blocked", {"request_id": "r", "kind": "direct", "completed": True,
                                      "result": body, "payload": {"agent_id": "worker"}})
    hub.store.put("ask", "ask", {"state": "resolved", "ask": {"task_id": "blocked"},
                                  "answer": {"status": "rejected", "reason": "PI denied access"}})
    hub.store.close()
    restored = Hub(s)
    try:
        outcome_calls["consumer"].clear()
        result = restored.completed_direct_result("r")
        assert not result.ok and "PI denied access" in result.error
        assert restored.resume_agents("r") == set()  # no worker needed to finish a denied turn
        assert "completed_direct_result" in outcome_calls["consumer"]
        restored.recovery_steps.add("r")
        await restored.orchestrator.run_request("r", resume=True)
        assert restored.requests["r"]["status"] == "failed"
        assert not restored.requests["r"]["results"]["direct"]["ok"]
        assert not any(e["type"] == "task.dispatched" for e in restored.events)
    finally:
        restored.store.close()


@pytest.mark.parametrize("status", ["answered", "rejected"])
async def test_cached_outcome_uses_shared_producer(tmp_path, outcome_calls, status):
    hub = Hub(settings(tmp_path))
    try:
        await produce(hub, "cso", rejected=status == "rejected")
        outcome_calls["producer"].clear()
        result = await produce(hub, "cso", rejected=status == "rejected")
        assert result["cached"] and result["status"] == status
        assert "answer_ask" in outcome_calls["producer"]
    finally:
        hub.store.close()


async def test_gateway_routing_exception_uses_shared_producer(tmp_path, outcome_calls):
    hub = Hub(settings(tmp_path))
    ask = AskRequest(task_id="t", agent_id="worker", to="cso", question="Cases?", why_blocked="blocked")

    async def fail(*args):
        raise RuntimeError("route failure")

    hub.orchestrator.answer_ask = fail
    try:
        hub._start_ask(ask, "origin")
        await asyncio.gather(*hub.ask_tasks.values())
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "rejected" and "route failure" in answer["reason"]
        assert "route" in outcome_calls["producer"]
    finally:
        hub.store.close()


@pytest.mark.parametrize("outcome", [
    {"approved": False, "note": "PI denied access"},
    {"approved": False, "note": "approval timed out"},
    {"approved": True, "state": "timed_out", "note": "approval timed out"},
])
def test_approval_denial_and_timeout_override_answer(outcome):
    from labhq.ask_results import ask_result

    result = ask_result(decision=outcome, answer="continue unsafely")
    assert result["status"] == "rejected" and "answer" not in result
    assert result["reason"] == outcome["note"]


async def test_mcp_transport_failure_uses_shared_producer(monkeypatch, outcome_calls):
    import json
    from labhq.tools import ask_mcp

    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise ConnectionError("broker offline")

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(ask_mcp.httpx, "AsyncClient", BrokenClient)
    result = json.loads(await ask_mcp.ask("cso", "Cases?", "blocked"))
    assert result["status"] == "rejected" and "broker offline" in result["reason"]
    assert "ask" in outcome_calls["producer"]
