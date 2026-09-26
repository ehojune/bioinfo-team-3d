import asyncio
from collections import Counter

import pytest

from labhq.gateway.server import Hub
from labhq.models import RunnerUnavailable, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator, failure_kind, valid_review
from labhq.settings import Settings


class FakeHub:
    def __init__(self, dispatch):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.step_retry_backoff_s = 0
        self.requests = {"r": {"text": "question", "mode": "team", "budget_usd": 10}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "sci_reviewer", "worker")}
        self.events = []
        self.calls = []
        self.approvals = []
        self.approve_budget = False
        self.reply = dispatch

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        return {"approved": self.approve_budget}

    def supports_resume(self, agent_id):
        return False


def result(task, ok=True, **kwargs):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=ok, **kwargs)


STEPS = [{"id": sid, "agent_id": "worker", "instruction": sid, "depends_on": deps}
         for sid, deps in (("A", []), ("B", ["A"]), ("C", ["B"]), ("D", []))]


@pytest.mark.asyncio
async def test_failed_branch_skips_transitive_dependents_and_preserves_independent_branch():
    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": STEPS})
        assert task.meta["kind"] == "step"
        return result(task, ok=task.meta["step_id"] != "A", text="done" if task.meta["step_id"] == "D" else "",
                      error="policy denied" if task.meta["step_id"] == "A" else None)

    hub = FakeHub(dispatch)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert req["results"]["B"]["status"] == req["results"]["C"]["status"] == "skipped"
    assert "A" in req["results"]["B"]["error"]
    assert "B" in req["results"]["C"]["error"]
    assert req["results"]["D"]["status"] == "done"
    assert "SKIPPED" in req["report"] and "policy denied" in req["report"]
    assert Counter(t.meta.get("step_id") for t in hub.calls)["A"] == 1
    assert not any(t.meta.get("step_id") in ("B", "C") for t in hub.calls)
    assert sum(e["type"] == "request.step_skipped" for e in hub.events) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("review_reply", ["garbage", "missing", "unknown", "engine_error", "verdict_only"])
async def test_review_unparsed_twice_cannot_pass(review_reply):
    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured={"steps": [STEPS[0]]})
        if kind == "review":
            if review_reply == "garbage":
                return result(task, text="not JSON")
            if review_reply == "engine_error":
                return result(task, ok=False, error="approval denied")
            if review_reply == "verdict_only":
                return result(task, structured={"verdict": "accept"})
            return result(task, structured={} if review_reply == "missing" else {"verdict": "maybe"})
        if kind == "step":
            return result(task, text="done")
        pytest.fail("synthesis must not run")

    hub = FakeHub(dispatch)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "failed"
    assert hub.requests["r"]["review"]["status"] == "review_unparsed"
    reviews = [t for t in hub.calls if t.meta["kind"] == "review"]
    assert len(reviews) == 2
    assert "Return ONLY a JSON object" in reviews[1].prompt


@pytest.mark.asyncio
async def test_timeout_retries_once_then_succeeds_with_two_attempts():
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return result(task, ok=calls == 2, text="done" if calls == 2 else "",
                      error="timeout after 5s" if calls == 1 else None)

    hub = FakeHub(dispatch)
    orch = Orchestrator(hub)
    res = await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                   meta={"kind": "step", "step_id": "A"}))
    assert res.ok and calls == 2 and orch.attempts["r"]["A"] == 2
    assert hub.calls[0].id != hub.calls[1].id
    assert [e["type"] for e in hub.events].count("request.step_retry") == 1


@pytest.mark.asyncio
async def test_runner_offline_retries_once_then_succeeds():
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RunnerUnavailable("runner disconnected")
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.requests["r"].update(mode="direct", agent_id="worker")
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done" and calls == 2
    assert sum(e["type"] == "request.step_retry" for e in hub.events) == 1


@pytest.mark.asyncio
async def test_hub_wraps_websocket_send_failure_and_clears_pending_task():
    class BrokenSocket:
        async def send_text(self, payload):
            raise RuntimeError("socket closed")

    hub = Hub(Settings())
    ws = BrokenSocket()
    hub.register_runner("r", ws, [{"id": "worker"}])
    task = Task(agent_id="worker", prompt="work")
    with pytest.raises(RunnerUnavailable):
        await hub.dispatch(task)
    assert "r" not in hub.runners
    assert task.id not in hub.futures and task.id not in hub.task_runner
    with pytest.raises(RunnerUnavailable):
        await hub.send_runner("r", {"type": "task.dispatch"})


@pytest.mark.asyncio
async def test_policy_refusal_is_terminal():
    async def dispatch(task):
        return result(task, ok=False, error="approval policy denied")

    hub = FakeHub(dispatch)
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert not res.ok and len(hub.calls) == 1
    assert not any(e["type"] == "request.step_retry" for e in hub.events)


@pytest.mark.asyncio
async def test_nonzero_exit_without_transient_signal_is_terminal():
    async def dispatch(task):
        return result(task, ok=False, error="exit 1: analysis script failed")

    hub = FakeHub(dispatch)
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert not res.ok and len(hub.calls) == 1
    assert not any(e["type"] == "request.step_retry" for e in hub.events)


@pytest.mark.asyncio
async def test_retry_cost_is_in_request_budget():
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return result(task, ok=calls == 2, text="done" if calls == 2 else "",
                      error="rate limit" if calls == 1 else None, cost_usd=0.4)

    hub = FakeHub(dispatch)
    hub.requests["r"]["budget_usd"] = 0.5
    orch = Orchestrator(hub)
    res = await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert res.ok  # the completed attempt remains successful after budget denial
    assert orch.cost["r"] == pytest.approx(0.8)
    assert len(hub.calls) == 2
    assert len(hub.approvals) == 1
    with pytest.raises(BudgetExceeded):
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="next"))
    assert len(hub.calls) == 2 and len(hub.approvals) == 1


@pytest.mark.asyncio
async def test_budget_denial_preserves_failed_attempt_instead_of_skipping_it():
    async def dispatch(task):
        return result(task, ok=False, error="rate limit", cost_usd=0.6)

    hub = FakeHub(dispatch)
    hub.requests["r"]["budget_usd"] = 0.5
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert not res.ok and res.error == "rate limit"
    assert len(hub.calls) == 1 and len(hub.approvals) == 1


@pytest.mark.parametrize("outcome,kind", [
    (TaskResult(task_id="t", agent_id="a", ok=False, error="input validation failed"), "terminal"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="cancelled"), "terminal"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="exit 1: crashed"), "terminal"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="exit 1: HTTP 503 overloaded"), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="exit 1: connection refused"), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="empty CLI stream: no result event"), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=True), "transient"),
    (asyncio.TimeoutError(), "transient"),
])
def test_failure_classification(outcome, kind):
    assert failure_kind(outcome) == kind


def test_review_schema_requires_complete_typed_fields():
    valid = {"verdict": "accept", "scores": {"addresses_question": 4, "evidence": 3,
                                               "thoroughness": 5}, "issues": []}
    assert valid_review(valid)
    assert not valid_review({"verdict": "accept"})
    assert not valid_review({**valid, "scores": {**valid["scores"], "evidence": "3"}})
    assert not valid_review({**valid, "scores": {**valid["scores"], "evidence": 6}})
    assert not valid_review({**valid, "issues": [{"step_id": "A", "problem": "missing"}]})
    assert not valid_review({**valid, "extra": "unexpected"})


@pytest.mark.asyncio
async def test_cancellation_skips_dependents():
    async def dispatch(task):
        if task.meta["step_id"] == "A":
            raise asyncio.CancelledError()
        return result(task, text="independent")

    hub = FakeHub(dispatch)
    orch = Orchestrator(hub)
    results = {}
    await orch.run_dag("r", "question", STEPS, results)
    assert not results["A"].ok
    assert results["B"].error.startswith("skipped:")
    assert results["C"].error.startswith("skipped:")
    assert results["D"].ok
    assert sum(e["type"] == "request.step_skipped" for e in hub.events) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_parallel_budget_decision_preserves_completed_steps_and_controls_new_starts(approved):
    d_started = asyncio.Event()
    decision_done = asyncio.Event()

    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured={"steps": STEPS})
        if kind == "synthesis":
            return result(task, text="report")
        sid = task.meta["step_id"]
        if sid == "A":
            await d_started.wait()
            return result(task, text="A done", cost_usd=0.6)
        if sid == "D":
            d_started.set()
            await decision_done.wait()  # D started before A's decision, but completes afterward
        return result(task, text=f"{sid} done")

    hub = FakeHub(dispatch)
    hub.approve_budget = approved
    hub.requests["r"]["budget_usd"] = 0.5
    hub.s.orchestrator.reviewer_agent = None
    decide = hub.request_approval

    async def resolve_budget(**kwargs):
        answer = await decide(**kwargs)
        decision_done.set()
        return answer

    hub.request_approval = resolve_budget
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert len(hub.approvals) == 1
    assert sum(e["type"] == "request.budget_exceeded" for e in hub.events) == 1
    assert req["results"]["A"]["status"] == "done"
    assert req["results"]["D"]["status"] == "done"
    assert "Budget: $0.60 > $0.50" in req["report"]
    if approved:
        assert req["status"] == "done"
        assert req["results"]["B"]["status"] == req["results"]["C"]["status"] == "done"
    else:
        assert req["status"] == "failed"
        assert req["results"]["B"]["status"] == req["results"]["C"]["status"] == "skipped"
        assert "budget" in req["results"]["B"]["error"]
        assert not any(t.meta.get("step_id") in ("B", "C") for t in hub.calls)
