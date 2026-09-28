import asyncio
from collections import Counter

import pytest

from labhq.gateway.server import Hub
from labhq.models import RunnerUnavailable, Task, TaskResult
from labhq.orchestrator.cso import (BudgetExceeded, Orchestrator, failure_kind, valid_review,
                                    format_roster, validate_steps)
from labhq.runner.daemon import Runner
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
        self.runner_online = asyncio.Event()
        self.runner_online.set()
        self.waiting_for_runner = asyncio.Event()
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

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass

    async def wait_agent_online(self, agent_id, timeout_s):
        self.waiting_for_runner.set()
        await self.runner_online.wait()
        return True


def result(task, ok=True, **kwargs):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=ok, **kwargs)


STEPS = [{"id": sid, "agent_id": "worker", "instruction": sid, "depends_on": deps}
         for sid, deps in (("A", []), ("B", ["A"]), ("C", ["B"]), ("D", []))]


def test_roster_and_dependencies():
    roster = format_roster([{"id": "reader", "name": "Reader", "role": "inspect",
                             "engine": "codex", "model": "small", "sandbox": "read-only",
                             "tools": ["Read"], "hpc_tools": False, "max_turns": 8}])
    assert "read-only" in roster and "labhq_hpc=no" in roster and "max_turns=8" in roster
    raw = [{"id": "A", "agent_id": "worker", "instruction": "produce", "outputs": ["table.tsv"], "depends_on": []},
           {"id": "B", "agent_id": "worker", "instruction": "Use table.tsv from A", "depends_on": []},
           {"id": "R", "agent_id": "sci_reviewer", "instruction": "review", "depends_on": []}]
    steps, warnings = validate_steps(raw, {"worker", "sci_reviewer"}, 10)
    assert [s["id"] for s in steps] == ["A", "B"]
    assert steps[1]["depends_on"] == ["A"]
    assert len(warnings) == 2
    with pytest.raises(ValueError, match="invalid dependencies"):
        validate_steps([{**raw[0], "depends_on": ["missing"]}], {"worker"}, 10)
    with pytest.raises(ValueError, match="cycle"):
        validate_steps([{**raw[0], "depends_on": ["B"]},
                        {**raw[1], "depends_on": ["A"]}], {"worker"}, 10)


@pytest.mark.asyncio
async def test_plan_waits_for_answer_and_replans_before_dispatch():
    calls = []

    async def dispatch(task):
        calls.append(task)
        if task.meta["kind"] == "plan":
            assert "scheduler=none" in task.prompt and "labhq_hpc=no" in task.prompt
            questions = [] if "PI clarification (questions and answer):" in task.prompt else ["Which cohort?"]
            return result(task, structured={"steps": [STEPS[0]], "clarifying_questions": questions})
        if task.meta["kind"] == "step":
            assert len([t for t in calls if t.meta["kind"] == "plan"]) == 2
            return result(task, text="done")
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.agents["worker"].update(scheduler="none", hpc_tools=False)
    hub.s.orchestrator.reviewer_agent = None
    async def answer(**kwargs):
        hub.approvals.append(kwargs)
        return {"approved": True, "note": "cases"}
    hub.request_approval = answer
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done", hub.requests["r"]
    assert [t.meta["kind"] for t in calls[:3]] == ["plan", "plan", "step"]
    assert hub.approvals[0]["kind"] == "clarify"
    entry = hub.requests["r"]["clarifications"][0]  # durable for a resume after restart
    assert entry == {"questions": ["Which cohort?"], "answer": "cases"}
    assert "Q1. Which cohort?\nPI answer: cases" in calls[1].prompt  # the re-plan sees what was answered


@pytest.mark.asyncio
async def test_blocking_step_waits_then_reruns_before_dependent():
    calls = []

    async def dispatch(task):
        calls.append(task)
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured={"steps": STEPS[:2]})
        if kind == "step" and task.meta["step_id"] == "A":
            if "Your earlier blocking question and the PI's answer:" not in task.prompt:
                return result(task, text="blocked", structured={"blocking_decision": "Cases or controls?"})
            assert not any(t.meta.get("step_id") == "B" for t in calls)
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    shared: dict = {}
    hub.result_map = lambda rid: shared
    async def answer(**kwargs):
        hub.approvals.append(kwargs)
        assert "A" not in shared  # the question-only result is gone before the wait (restart-safe)
        return {"approved": True, "note": "cases"}
    hub.request_approval = answer
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done", hub.requests["r"]
    assert [t.meta.get("step_id") for t in calls if t.meta["kind"] == "step"] == ["A", "A", "B"]
    assert hub.approvals[0]["kind"] == "clarify"
    rerun = [t for t in calls if t.meta.get("step_id") == "A"][1]
    assert "Q1. Cases or controls?\nPI answer: cases" in rerun.prompt  # the answer arrives with its question
    assert hub.requests["r"]["step_decisions"]["A"] == {"question": "Cases or controls?", "answer": "cases"}


def test_configured_orchestration_agents_are_not_workers():
    from labhq.orchestrator.cso import validate_steps

    raw = [{"id": "s1", "agent_id": "boss", "instruction": "plan more", "outputs": []},
           {"id": "s2", "agent_id": "worker", "instruction": "work", "outputs": []}]
    steps, warnings = validate_steps(raw, {"boss", "worker"}, 10, {"boss"})
    assert [s["id"] for s in steps] == ["s2"] and any("orchestration role removed" in w for w in warnings)


@pytest.mark.asyncio
async def test_blocking_decision_denial_cancels_dispatch():
    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": STEPS[:2]})
        assert task.meta.get("step_id") != "B"
        return result(task, structured={"blocking_decision": "Choose a cohort"})
    hub = FakeHub(dispatch)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "failed"
    assert "Choose a cohort" in hub.requests["r"]["report"]
    assert "Pending PI decisions/questions" in hub.requests["r"]["report"]
    assert not any(t.meta.get("step_id") == "B" for t in hub.calls)


def test_resume_uses_adapter_flag(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = Hub(settings)
    hub.agents = {e: {"engine": e} for e in ("claude_code", "codex", "gemini", "antigravity", "mock")}
    assert hub.supports_resume("claude_code")
    assert hub.supports_resume("codex")
    assert not hub.supports_resume("gemini")
    assert not hub.supports_resume("antigravity")
    assert not hub.supports_resume("missing")
    hub.agents["cli_plain"] = {"engine": "cli", "cli_resume": False}
    hub.agents["cli_resumable"] = {"engine": "cli", "cli_resume": True}
    assert not hub.supports_resume("cli_plain")
    assert hub.supports_resume("cli_resumable")


def test_runner_reports_effective_compute_capabilities():
    from types import SimpleNamespace
    runner = object.__new__(Runner)
    runner.s = Settings()
    runner.s.hpc.scheduler = "none"
    runner.s.runner.force_engine = "mock"
    runner.registry = SimpleNamespace(roster=lambda: [{"id": "analyst", "engine": "claude_code"}])
    runner.incarnation = "test"
    hello = runner.hello()
    assert hello["capabilities"] == {"scheduler": "none", "compute_backends": ["local CLI"],
                                      "hpc_tools": False}
    assert hello["agents"][0]["engine"] == "mock"


@pytest.mark.asyncio
async def test_clarification_can_be_disabled():
    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": [STEPS[0]],
                                            "clarifying_questions": ["Which cohort?"]})
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.wait_for_clarification = False
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"
    assert hub.approvals == []
    assert any(e["type"] == "request.questions" for e in hub.events)


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
    assert "A [terminal]: policy denied" in req["report"]
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
                      error="timeout after 5s" if calls == 1 else None,
                      session_id="worker-session")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    orch = Orchestrator(hub)
    res = await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                   meta={"kind": "step", "step_id": "A"}))
    assert res.ok and calls == 2 and orch.attempts["r"]["A"] == 2
    assert hub.calls[0].id != hub.calls[1].id
    assert hub.calls[1].resume_session_id == "worker-session"
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
    hub.runner_online.clear()
    hub.requests["r"].update(mode="direct", agent_id="worker")
    running = asyncio.create_task(Orchestrator(hub).run_request("r"))
    await asyncio.wait_for(hub.waiting_for_runner.wait(), 1)
    assert calls == 1 and not running.done()
    hub.runner_online.set()  # reconnection signal; no clock-based sleep
    await running
    assert hub.requests["r"]["status"] == "done" and calls == 2
    assert sum(e["type"] == "request.step_retry" for e in hub.events) == 1


@pytest.mark.asyncio
async def test_hub_online_wait_uses_registration_signal(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = Hub(settings)
    hub.set_roster("r", [{"id": "worker"}])  # roster remains while its runner is offline
    waiting = asyncio.create_task(hub.wait_agent_online("worker", 30))
    await asyncio.sleep(0)
    assert not waiting.done()
    ws = object()
    hub.register_runner("r", ws, [{"id": "worker"}])
    assert await asyncio.wait_for(waiting, 1)
    hub.unregister_runner("r", ws)
    assert not await hub.wait_agent_online("worker", 0)


@pytest.mark.asyncio
async def test_hub_preserves_uncertain_task_after_websocket_send_failure(tmp_path):
    class BrokenSocket:
        async def send_text(self, payload):
            raise RuntimeError("socket closed")

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.orchestrator.runner_reconnect_timeout_s = 0.01
    hub = Hub(settings)
    ws = BrokenSocket()
    hub.register_runner("r", ws, [{"id": "worker"}])
    task = Task(agent_id="worker", prompt="work")
    outcome = await hub.dispatch(task)
    assert not outcome.ok and "delivery uncertain" in outcome.error
    assert "r" not in hub.runners
    assert task.id not in hub.futures and task.id not in hub.task_runner
    assert hub.store.get("task", task.id)["payload"]["id"] == task.id
    assert hub.store.get("task", task.id)["accepted"] is False
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


def test_shared_output_labels_do_not_create_inferred_cycles():
    from labhq.orchestrator.cso import validate_steps

    raw = [{"id": "a", "agent_id": "analyst", "instruction": "write summary", "outputs": ["summary"]},
           {"id": "b", "agent_id": "analyst", "instruction": "write summary", "outputs": ["summary"]},
           {"id": "c", "agent_id": "analyst", "instruction": "use counts.tsv", "outputs": []},
           {"id": "d", "agent_id": "analyst", "instruction": "make counts.tsv", "outputs": ["counts.tsv"]}]
    steps, warnings = validate_steps(raw, {"analyst"}, 10)
    by = {s["id"]: s for s in steps}
    assert by["a"]["depends_on"] == [] and by["b"]["depends_on"] == []  # ambiguous label: no inference
    assert by["c"]["depends_on"] == ["d"]  # unique producer: inferred


def test_inferred_dependency_that_would_close_a_cycle_is_skipped():
    from labhq.orchestrator.cso import validate_steps

    raw = [{"id": "s1", "agent_id": "analyst", "instruction": "then hand to s2", "outputs": []},
           {"id": "s2", "agent_id": "analyst", "instruction": "read s1 output", "depends_on": ["s1"], "outputs": []}]
    steps, warnings = validate_steps(raw, {"analyst"}, 10)
    assert [s["depends_on"] for s in steps] == [[], ["s1"]]
    assert any("would create a cycle" in w for w in warnings)


def test_saved_results_pop_is_durable(tmp_path):
    from labhq.gateway.server import Hub, SavedResults

    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s)
    hub.requests["r"] = {"id": "r", "text": "t", "status": "running", "results": {}}
    results = SavedResults(hub, "r")
    results["s1"] = TaskResult(task_id="t1", agent_id="a", ok=True, text="question only")
    assert "s1" in hub.requests["r"]["results"]
    results.pop("s1", None)
    assert "s1" not in hub.requests["r"]["results"] and "s1" not in results
    assert "s1" not in (Hub(s).requests["r"].get("results") or {})  # survives a restart


@pytest.mark.asyncio
async def test_failed_revision_keeps_first_result_and_workspace():
    steps = [{"id": "A", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]
    seen = []

    async def dispatch(task):
        seen.append(task)
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured={"steps": steps})
        if kind == "step":
            if task.meta["revision"]:
                assert task.meta["workdir"] == "runs/A"
                assert task.resume_session_id == "worker-session"
                return result(task, ok=False, error="revision broke", workdir="runs/A")
            return result(task, text="good evidence", workdir="runs/A", workdir_id="A",
                          session_id="worker-session")
        if kind == "review":
            revise = task.meta["revision"] == 0
            return result(task, structured={"verdict": "revise" if revise else "accept",
                                            "scores": {"addresses_question": 4, "evidence": 4,
                                                       "thoroughness": 4},
                                            "issues": [{"step_id": "A", "problem": "check", "request": "retry"}]
                                            if revise else []})
        return result(task, text="final")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: agent_id == "worker"
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done"
    assert req["results"]["A"]["text"] == "good evidence"
    assert "revision broke" in req["results"]["A"]["revision_failed"]
    assert "revision failed" in req["report"]
    assert all(t.resume_session_id is None for t in seen if t.meta["kind"] == "review")


@pytest.mark.asyncio
async def test_retry_reuses_workdir_and_dependent_receives_artifact_paths():
    calls = []

    async def dispatch(task):
        calls.append(task)
        sid = task.meta["step_id"]
        if sid == "A" and len([x for x in calls if x.meta["step_id"] == "A"]) == 1:
            return result(task, ok=False, error="timeout", workdir="runs/A")
        if sid == "A":
            assert task.meta["workdir"] == "runs/A"
            return result(task, text="done", workdir="runs/A", workdir_id="A",
                          outputs=["outputs/table.tsv"])
        assert task.meta["upstream_dirs"] == ["runs/A"]
        assert '"workdir_id": "A"' in task.context
        assert "runs/A/outputs/table.tsv" in task.context.replace("\\", "/")
        return result(task, text="used table")

    hub = FakeHub(dispatch)
    steps = [{"id": "A", "agent_id": "worker", "instruction": "make table", "depends_on": [],
              "outputs": ["table.tsv"]},
             {"id": "B", "agent_id": "worker", "instruction": "use table", "depends_on": ["A"]}]
    outcomes = {}
    await Orchestrator(hub).run_dag("r", "question", steps, outcomes)
    assert outcomes["A"].ok and outcomes["B"].ok
    assert calls[0].id != calls[1].id


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [True, False])
async def test_max_turns_wraps_once_and_keeps_failure(resume):
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            assert task.resume_session_id == "session-1"
            assert task.meta["agent_overrides"]["max_turns"] == 2
            assert task.meta["workdir"] == "runs/A"
            return result(task, text="saved", workdir="runs/A", outputs=["outputs/PARTIAL_STATUS.md"])
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns",
                      session_id="session-1", workdir="runs/A")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: resume
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                                meta={"kind": "step", "step_id": "A"}))
    assert not res.ok and res.partial_results is resume
    assert res.outputs == (["outputs/PARTIAL_STATUS.md"] if resume else [])
    assert len(hub.calls) == (2 if resume else 1)


@pytest.mark.asyncio
async def test_declared_missing_output_is_incomplete_and_skips_dependent():
    steps = [{"id": "A", "agent_id": "worker", "instruction": "make table", "depends_on": [],
              "outputs": ["table.tsv"]},
             {"id": "B", "agent_id": "worker", "instruction": "read A", "depends_on": ["A"]}]

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": steps})
        assert task.meta["step_id"] == "A"
        return result(task, text="claimed done", workdir_id="A")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert req["results"]["A"]["status"] == "incomplete"
    assert req["results"]["A"]["missing_outputs"] == ["table.tsv"]
    assert "A [terminal]" in req["report"] and "table.tsv" in req["report"]
    assert req["results"]["B"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_cso_reuses_session_for_replan_and_synthesis():
    async def dispatch(task):
        if task.meta["kind"] == "plan":
            first = task.resume_session_id is None
            return result(task, session_id="cso-session", workdir="runs/cso",
                          structured={"steps": [STEPS[0]],
                                      "clarifying_questions": ["Which group?"] if first else []})
        return result(task, text="done", session_id="cso-session", workdir="runs/cso")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: agent_id == "cso"
    hub.s.orchestrator.reviewer_agent = None
    hub.request_approval = lambda **kwargs: asyncio.sleep(0, result={"approved": True, "note": "cases"})
    await Orchestrator(hub).run_request("r")
    control = [task for task in hub.calls if task.agent_id == "cso"]
    assert hub.requests["r"]["status"] == "done"
    assert [task.meta["kind"] for task in control] == ["plan", "plan", "synthesis"]
    assert [task.resume_session_id for task in control] == [None, "cso-session", "cso-session"]
    assert control[1].meta["workdir"] == control[2].meta["workdir"] == "runs/cso"


@pytest.mark.asyncio
async def test_runner_records_declared_artifact_and_reuses_workspace(tmp_path):
    from pathlib import Path
    from labhq.models import AgentSpec, Engine

    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.hpc.scheduler = "none"
    runner = Runner(settings)
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.mock)
    first = Task(agent_id="worker", request_id="r", prompt="Your step: make [artifact]",
                 meta={"kind": "step", "outputs": ["artifact.txt"]})
    produced = await runner.run_task(first)
    assert produced.outputs == ["outputs/artifact.txt"]
    assert produced.workdir_id == Path(produced.workdir).name
    second = Task(agent_id="worker", request_id="r", prompt="continue",
                  meta={"kind": "step", "workdir": produced.workdir,
                        "upstream_dirs": [produced.workdir]})
    resumed = await runner.run_task(second)
    assert resumed.workdir == produced.workdir
    assert (Path(resumed.workdir) / "outputs" / "artifact.txt").read_text() == "mock artifact\n"
    assert (Path(resumed.workdir) / f"TASK_{second.id}.md").exists()
    assert second.id in (Path(resumed.workdir) / "manifest.json").read_text()


@pytest.mark.parametrize("name,expected", [
    ("table.tsv", "outputs/table.tsv"), ("./table.tsv", "outputs/table.tsv"),
    (r"outputs\table.tsv", "outputs/table.tsv"), ("outputs/dir/../table.tsv", "outputs/table.tsv"),
    ("../escape.tsv", None), ("/abs/t.tsv", None), ("C:/x/t.tsv", None),
])
def test_declared_outputs_normalize_like_the_runner(name, expected):
    from labhq.util import output_relpath

    assert output_relpath(name) == expected
