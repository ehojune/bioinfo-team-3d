import asyncio
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from labhq.gateway.server import Hub
from labhq.models import RunnerUnavailable, Task, TaskResult
from labhq.orchestrator.cso import (FINISH_PROMPT, BudgetExceeded, Orchestrator, failure_kind, valid_review,
                                    format_roster, validate_steps)
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.tools.scheduler import Scheduler


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


@pytest.fixture
def continuations(monkeypatch):
    from labhq.orchestrator import cso

    calls = []
    original = cso.continuation_prompt

    def spy(task, updates, **kwargs):
        prompt = original(task, updates, **kwargs) + f"\n[continuation call {len(calls)}]"
        calls.append({"task": task, "updates": updates, "prompt": prompt, **kwargs})
        return prompt

    monkeypatch.setattr(cso, "continuation_prompt", spy)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["ask", "job", "both", "blocking", "revision", "retry"])
@pytest.mark.parametrize("resume_mode", ["unsupported", "supported", "missing_session"])
async def test_every_step_continuation_uses_common_prompt(tmp_path, continuations, route, resume_mode):
    session = None if resume_mode == "missing_session" else "worker-session"
    workdir = str(tmp_path / "workspace")
    request, instruction, context = "Original PI request", "Analyze selected inputs", "QC-passing upstream inputs"
    prior_text = "Prior step evidence"

    async def dispatch(task):
        if len(hub.calls) == 1 and route != "revision":
            return result(task, ok=route != "retry", text=prior_text, session_id=session, workdir=workdir,
                          error="HTTP 503 overloaded" if route == "retry" else None,
                          pending_asks=["ask_1"] if route in {"ask", "both"} else [],
                          pending_jobs=["job_1"] if route in {"job", "both"} else [],
                          blocking_decision="Cases or controls?" if route == "blocking" else None)
        return result(task, text="completed", session_id=session, workdir=workdir)

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: resume_mode != "unsupported"
    hub.wait_asks = lambda ids: asyncio.sleep(0, result=[{"from": "cso", "answer": "Selected answer"}])
    hub.wait_jobs = lambda tid: asyncio.sleep(0, result={
        "jobs": [{"job_id": "job_1", "state": "done", "exit_status": 0}]})
    hub.request_approval = lambda **kw: asyncio.sleep(0, result={"approved": True, "note": "Selected answer"})
    orch = Orchestrator(hub)
    if route in {"blocking", "revision"}:
        steps = [{"id": "U", "agent_id": "worker", "instruction": "upstream", "depends_on": []},
                 {"id": "A", "agent_id": "worker", "instruction": instruction, "depends_on": ["U"]}]
        results = {"U": TaskResult(task_id="u", agent_id="worker", ok=True, text=context)}
        if route == "revision":
            results["A"] = TaskResult(task_id="prior", agent_id="worker", ok=True, text=prior_text,
                                      session_id=session, workdir=workdir)
        await orch.run_dag("r", request, steps, results, only={"A"},
                           feedback={"A": "Verify the evidence"} if route == "revision" else None)
        assert results["A"].ok and results["A"].text == "completed"
    else:
        original = Task(agent_id="worker", request_id="r", prompt=f"{request}\nYour step: {instruction}",
                        context=context, meta={"kind": "step", "step_id": "A"})
        res = await orch.run_step(original)
        assert res.ok and res.text == "completed"
    assert len(continuations) == 1
    call, continued = continuations[0], hub.calls[-1]
    assert continued.prompt == call["prompt"]  # a helper call that is discarded must fail
    assert continued.meta["workdir"] == workdir
    can_resume = resume_mode == "supported"
    assert call["resumable"] is can_resume
    assert continued.resume_session_id == (session if can_resume else None)
    if can_resume:
        assert request not in continued.prompt and instruction not in continued.prompt
        assert prior_text not in continued.prompt
        # A resumed revision re-reads its current upstream results (PR #368); other continuations do not.
        assert (context in continued.prompt) is (route == "revision")
    else:
        for required in (request, instruction, context, prior_text):
            assert required in continued.prompt
    if route in {"ask", "both", "blocking"}:
        assert "Selected answer" in continued.prompt
    if route in {"job", "both"}:
        assert "job_1" in continued.prompt and "exit=0" in continued.prompt
    if route == "revision":
        assert "Verify the evidence" in continued.prompt


@pytest.mark.asyncio
async def test_blocking_continuation_restores_previous_result_after_restart(continuations):
    import json

    async def dispatch(task):
        return result(task, text="completed")

    hub = FakeHub(dispatch)
    previous = TaskResult(task_id="blocked", agent_id="worker", ok=True,
                          text="Saved blocking turn", structured={"blocking_decision": "Which inputs?"})
    hub.requests["r"]["step_decisions"] = json.loads(json.dumps({"A": {
        "question": "Which inputs?", "answer": "QC-passing", "previous_result": previous.model_dump(mode="json")}}))
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Compare inputs", "depends_on": []}]
    results = {}
    await Orchestrator(hub).run_dag("r", "Original request", steps, results)
    assert results["A"].ok and len(continuations) == 1
    assert hub.calls[0].prompt == continuations[0]["prompt"]
    for required in ("Original request", "Compare inputs", "Saved blocking turn", "Which inputs?", "QC-passing"):
        assert required in hub.calls[0].prompt


@pytest.mark.asyncio
async def test_worker_revision_checks_that_its_session_is_free_before_dispatch(continuations, tmp_path):
    workdir = str(tmp_path / "worker")

    async def dispatch(task):
        return result(task, text="revised")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    checked = []

    async def wait_session_free(agent_id, session_id, held_workdir, **kwargs):
        checked.append((agent_id, session_id, held_workdir, kwargs))
        return None, None

    hub.wait_session_free = wait_session_free
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Revise analysis", "depends_on": []}]
    outcomes = {"A": TaskResult(task_id="prior", agent_id="worker", ok=True, text="old",
                                  session_id="worker-session", workdir=workdir)}
    await Orchestrator(hub).run_dag("r", "Original request", steps, outcomes, only={"A"},
                                    feedback={"A": "Check the evidence"})

    assert checked == [("worker", "worker-session", workdir, {"request_id": "r", "step_id": "A"})]
    assert hub.calls[0].resume_session_id is None
    assert "workdir" not in hub.calls[0].meta


@pytest.mark.asyncio
async def test_run_request_exception_terminal_keeps_saved_cost():
    async def dispatch(task):
        raise AssertionError("dispatch should not be reached")

    hub = FakeHub(dispatch)
    hub.requests["r"].update(plan={"steps": [{"id": "A", "agent_id": "worker",
                                               "instruction": "Analyze", "depends_on": []}]},
                             cost_usd=1.25, cost_known=False)
    hub.result_map = lambda rid: (_ for _ in ()).throw(RuntimeError("broken checkpoint"))
    terminal = []
    hub.commit_terminal = lambda rid, typ, data: terminal.append((rid, typ, data))

    await Orchestrator(hub).run_request("r", resume=True)

    assert len(terminal) == 1
    rid, typ, data = terminal[0]
    assert (rid, typ) == ("r", "request.failed")
    assert data["error"] == "RuntimeError: broken checkpoint"
    assert "A · worker (FAILED)" in data["report_appendix"]
    assert data["cost_usd"] == 1.25 and data["cost_known"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [("ask", "job", "ask", "job"),
                                   ("job", "ask", "job"), ("both",)])
async def test_step_rechecks_jobs_and_asks_after_each_wake(tmp_path, turns):
    events = []
    dispatched = []
    workdir = str(tmp_path / "workspace")

    async def dispatch(task):
        index = len(dispatched)
        dispatched.append(task)
        events.append(("dispatch", task.id))
        if index == len(turns):
            return result(task, text="completed", workdir=workdir)
        pending = turns[index]
        return result(task, text=f"waiting {index}", workdir=workdir,
                      pending_jobs=[f"job_{index}"] if pending in {"job", "both"} else [],
                      pending_asks=[f"ask_{index}"] if pending in {"ask", "both"} else [])

    async def wait_jobs(task_id):
        events.append(("job", task_id))
        await asyncio.sleep(0)
        return {"jobs": [{"job_id": "job_done", "state": "done", "exit_status": 0}]}

    async def wait_asks(ask_ids):
        events.append(("ask", ask_ids))
        await asyncio.sleep(0)
        return [{"from": "cso", "answer": "Use the selected inputs"}]

    hub = FakeHub(dispatch)
    hub.wait_jobs, hub.wait_asks = wait_jobs, wait_asks
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert res.ok and res.text == "completed"
    assert not res.pending_jobs and not res.pending_asks
    expected = []
    for index, pending in enumerate(turns):
        expected.append(("dispatch", dispatched[index].id))
        if pending in {"ask", "both"}:
            expected.append(("ask", [f"ask_{index}"]))
            assert "Use the selected inputs" in dispatched[index + 1].prompt
        if pending in {"job", "both"}:
            expected.append(("job", dispatched[index].id))
            assert "job_done" in dispatched[index + 1].prompt
        assert dispatched[index + 1].meta["parent_task"] == dispatched[index].id
        assert dispatched[index + 1].meta["workdir"] == workdir
    expected.append(("dispatch", dispatched[-1].id))
    assert events == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_resume", [False, True])
async def test_nonresumable_ask_wake_keeps_original_instruction_and_context(tmp_path, supports_resume):
    workdir = str(tmp_path / "workspace")
    original = Task(agent_id="worker", request_id="r", prompt="Compare the selected groups and write summary.md",
                    context="Upstream: only use the QC-passing inputs",
                    output_schema={"type": "object"}, meta={"kind": "step", "step_id": "A"})

    async def dispatch(task):
        index = len(hub.calls) - 1
        return result(task, text=f"progress {index}", workdir=workdir,
                      session_id=f"session_{index}", pending_asks=[f"ask_{index}"] if index < 2 else [])

    async def wait_asks(ask_ids):
        return [{"from": "cso", "answer": f"Advice for {ask_ids[0]}"}]

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: supports_resume
    hub.wait_asks = wait_asks
    res = await Orchestrator(hub).run_step(original)
    assert res.ok and len(hub.calls) == 3
    for index, wake in enumerate(hub.calls[1:]):
        assert f"Advice for ask_{index}" in wake.prompt
        assert wake.meta["workdir"] == workdir and wake.meta["step_id"] == "A"
        assert wake.output_schema == original.output_schema
        if supports_resume:
            assert wake.resume_session_id == f"session_{index}"
            assert wake.context == ""
            assert original.prompt not in wake.prompt
        else:
            assert wake.resume_session_id is None
            assert original.prompt in wake.prompt
            assert original.context in wake.prompt
            assert f"progress {index}" in wake.prompt
            assert wake.context == ""  # the common prompt carries all fallback context


async def test_failed_attempt_with_submitted_job_does_not_resubmit():
    settings = Settings()
    settings.hpc.scheduler = "mock"
    scheduler = Scheduler(settings.hpc)
    submissions = []

    async def dispatch(task):
        jid = scheduler.submit("fixture.sh", "fixture")
        submissions.append(jid)
        return result(task, ok=False, error="timeout after submission", pending_jobs=[jid],
                      session_id="fixture-session", workdir="runs/fixture")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda _: True
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="submit"))
    assert len(submissions) == 1
    assert not res.ok and res.pending_jobs == submissions
    assert not any(e["type"] == "request.step_retry" for e in hub.events)


STEPS = [{"id": sid, "agent_id": "worker", "instruction": sid, "depends_on": deps}
         for sid, deps in (("A", []), ("B", ["A"]), ("C", ["B"]), ("D", []))]


def test_roster_and_dependencies():
    roster = format_roster([{"id": "reader", "name": "Reader", "role": "inspect",
                             "engine": "codex", "model": "small", "sandbox": "read-only",
                             "tools": ["Read"], "hpc_tools": False, "max_turns": 8},
                            {"id": "lit_scout", "name": "Scout", "role": "search",
                             "engine": "codex", "model": "small", "sandbox": "workspace-write",
                             "tools": ["WebSearch"], "hpc_tools": False, "max_turns": 8}])
    assert "read-only" in roster and "labhq_hpc=no" in roster and "max_turns=8" in roster
    assert "lit_scout: Scout – search (codex/small); write-capable" in roster
    raw = [{"id": "A", "agent_id": "worker", "instruction": "produce", "outputs": ["table.tsv"], "depends_on": []},
           {"id": "B", "agent_id": "worker", "instruction": "Use table.tsv from A", "depends_on": []},
           {"id": "R", "agent_id": "sci_reviewer", "instruction": "review", "depends_on": []}]
    steps, warnings = validate_steps(raw[:2], {"worker"}, 10)
    assert [s["id"] for s in steps] == ["A", "B"]
    assert steps[1]["depends_on"] == ["A"]
    assert len(warnings) == 2
    with pytest.raises(ValueError, match="unavailable or orchestration agents"):
        validate_steps(raw, {"worker", "sci_reviewer"}, 10)
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
                return result(task, text="blocked", structured={"blocking_decision": "Cases or controls?"},
                              tool_errors=["fixture lookup failed", "shared lookup failed"])
            assert not any(t.meta.get("step_id") == "B" for t in calls)
            return result(task, text="done", tool_errors=["shared lookup failed", "second lookup failed"])
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
    decision = hub.requests["r"]["step_decisions"]["A"]
    assert decision["question"] == "Cases or controls?" and decision["answer"] == "cases"
    assert decision["previous_result"]["text"] == "blocked"
    assert decision["previous_result"]["structured"] == {"blocking_decision": "Cases or controls?"}
    assert decision["previous_result"]["tool_errors"] == ["fixture lookup failed", "shared lookup failed"]
    assert shared["A"].tool_errors == ["fixture lookup failed", "shared lookup failed", "second lookup failed"]
    appendix = hub.requests["r"]["report_appendix"]
    assert "A: 실패한 조회 — 증거도 부재 증명도 아님 3건" in appendix
    assert "fixture lookup failed" in appendix


def test_configured_orchestration_agents_are_not_workers():
    from labhq.orchestrator.cso import validate_steps

    raw = [{"id": "s1", "agent_id": "boss", "instruction": "plan more", "outputs": []},
           {"id": "s2", "agent_id": "worker", "instruction": "work", "outputs": []}]
    with pytest.raises(ValueError, match="unavailable or orchestration agents"):
        validate_steps(raw, {"boss", "worker"}, 10, {"boss"})


@pytest.mark.asyncio
async def test_runner_preflight_removes_staff_from_plan_roster(tmp_path, monkeypatch):
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "cso.yaml").write_text(
        "id: cso\nname: CSO\nrole: plan\nengine: mock\n", encoding="utf-8")
    (agents / "worker.yaml").write_text(
        "id: worker\nname: Worker\nrole: analysis\nengine: claude_code\n"
        "plugin_dirs: ['${MISSING_STAFF_PLUGIN}']\n", encoding="utf-8")
    settings = Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state"), "workspace_root": str(tmp_path / "runs"),
                   "agents_dir": str(agents.parent)},
        "gateway": {"state_dir": str(tmp_path / "gateway")},
    })
    monkeypatch.delenv("MISSING_STAFF_PLUGIN", raising=False)
    runner = Runner(settings)
    runner.registry.load()
    roster = runner.roster()
    assert [agent["id"] for agent in roster] == ["cso"]

    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"available-again","version":"1"}', encoding="utf-8")
    monkeypatch.setenv("MISSING_STAFF_PLUGIN", str(plugin))
    restarted = Runner(settings)
    restarted.registry.load()
    assert [agent["id"] for agent in restarted.roster()] == ["cso", "worker"]

    planned = []

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            planned.append(task)
            return result(task, structured={"steps": []})
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.agents = {agent["id"]: agent for agent in roster}
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")
    assert len(planned) == 1
    assert planned[0].meta["roster"] == []
    assert "worker: Worker" not in planned[0].prompt
    assert "No workers available" in planned[0].prompt


@pytest.mark.asyncio
async def test_general_plan_rejects_unavailable_staff_and_replans():
    calls = []

    async def dispatch(task):
        calls.append(task)
        if task.meta["kind"] == "plan":
            agent = "missing-worker" if len([t for t in calls if t.meta["kind"] == "plan"]) == 1 else "worker"
            return result(task, structured={"steps": [{
                "id": "A", "agent_id": agent, "instruction": "analyze", "outputs": [], "depends_on": []}]})
        if task.meta["kind"] == "step":
            assert task.agent_id == "worker"
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")

    plans = [task for task in calls if task.meta["kind"] == "plan"]
    assert len(plans) == 2
    assert "unavailable or orchestration agents" in plans[1].prompt
    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("report")


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
    assert "Choose a cohort" in hub.requests["r"]["report_appendix"]
    assert "question was rejected or unanswered" in hub.requests["r"]["report_appendix"]
    assert not hub.requests["r"].get("pending_questions")
    assert not hub.requests["r"]["results"]["A"]["ok"]
    assert hub.requests["r"]["results"]["A"]["error_kind"] == "ask_rejected"
    assert hub.requests["r"]["results"]["B"]["status"] == "skipped"
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
    from labhq.models import AgentSpec

    runner = object.__new__(Runner)
    runner.s = Settings()
    runner.s.hpc.scheduler = "none"
    runner.s.runner.force_engine = "mock"
    agent = AgentSpec(id="analyst", name="Analyst", role="analysis")
    runner.registry = SimpleNamespace(agents={agent.id: agent}, roster=lambda: [agent.summary()])
    runner.ws_root = Path(".")
    runner.incarnation = "test"
    runner.engine_versions = {"mock": "unreported"}
    runner.local_software = {"r": {"available": False, "version": None}}
    hello = runner.hello()
    assert hello["capabilities"] == {
        "scheduler": "none", "compute_backends": ["local CLI"], "hpc_tools": False,
        "engine_cli_versions": {"mock": "unreported"},
        "local_software": {"r": {"available": False, "version": None}},
    }
    assert hello["agents"][0]["engine"] == "mock"


def test_cso_capabilities_include_each_runner_local_software_and_unknown():
    from labhq.orchestrator.cso import format_capabilities

    roster = [
        {"id": "analyst", "runner_id": "runner-a", "scheduler": "none", "hpc_tools": False,
         "compute_backends": ["local CLI"]},
        {"id": "reader", "runner_id": "runner-b", "scheduler": "none", "hpc_tools": False,
         "compute_backends": ["local CLI"]},
    ]
    runner_capabilities = {"runner-a": {"local_software": {
        "r": {"available": False, "version": None},
        "python": {"version": "3.12.7", "packages": {
            "pandas": True, "numpy": True, "scipy": True, "matplotlib": True,
            "statsmodels": True, "scikit-learn": True, "gseapy": False, "pydeseq2": False,
        }},
        "tools": {"docker": False, "nextflow": False, "java": True, "wsl": False},
        "ignored_path": r"C:\Users\private-user\R",
    }}}

    text = format_capabilities(roster, runner_capabilities)

    assert "runner runner-a local software: R=missing; Python=3.12.7" in text
    assert "gseapy=no" in text and "java=yes" in text
    assert "runner runner-b local software: unknown" in text
    assert "private-user" not in text


def test_environment_rule_uses_binary_wheels_and_falls_back_without_asking_pi():
    from labhq.orchestrator.cso import ENV_STEP_RULE, PLAN_PROMPT, REPLAN_PROMPT, RESEARCH_PLAN_PROMPT

    assert "pip install --only-binary=:all:" in ENV_STEP_RULE
    assert "alternative" in ENV_STEP_RULE
    assert "do not ask the PI" in ENV_STEP_RULE
    assert "outputs/env/" in ENV_STEP_RULE
    assert all(ENV_STEP_RULE in prompt for prompt in (PLAN_PROMPT, REPLAN_PROMPT, RESEARCH_PLAN_PROMPT))


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
    assert "SKIPPED" in req["report_appendix"] and "policy denied" in req["report_appendix"]
    assert "A [terminal]: policy denied" in req["report_appendix"]
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
    assert hub.approvals[0]["detail"] == {"spent_usd": 0.8, "limit_usd": 0.5,
                                          "requested_budget_usd": 1.0}
    with pytest.raises(BudgetExceeded):
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="next"))
    assert len(hub.calls) == 2 and len(hub.approvals) == 1


@pytest.mark.asyncio
async def test_unknown_cost_is_held_at_the_per_task_budget_and_blocks_the_next_task_when_denied():
    async def dispatch(task):
        return result(task, text="done", cost_usd=None, cost_known=False,
                      usage={"input_tokens": 10, "output_tokens": 2}, usage_known=True)

    hub = FakeHub(dispatch)
    hub.agents["worker"].update(engine="codex", model=None)  # Codex default model: no price row
    hub.requests["r"]["budget_usd"] = 10
    hub.s.policy.budget.per_task_usd = 4
    orch = Orchestrator(hub)
    for prompt in ("first", "second"):  # $0 counted, 2 x $4 held: within $10
        assert (await orch.run_step(Task(agent_id="worker", request_id="r", prompt=prompt))).ok
    assert hub.approvals == [] and orch.cost["r"] == 0  # counted apart, never added as $0
    third = await orch.run_step(Task(agent_id="worker", request_id="r", prompt="third"))

    assert third.ok and len(hub.approvals) == 1  # 3 x $4 = $12 may pass the $10 cap
    approval = hub.approvals[0]
    assert "예산 판정 불가" in approval["summary"] and "미집계 3건" in approval["summary"]
    assert approval["detail"] == {"spent_usd": 0, "limit_usd": 10, "requested_budget_usd": 20,
                                  "unknown_count": 3, "unknown_reserve_usd": 4.0}
    with pytest.raises(BudgetExceeded, match="unaccounted"):
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="fourth"))
    assert len(hub.calls) == 3


@pytest.mark.asyncio
async def test_approved_unknown_reservation_raises_the_cap_like_an_overrun():
    async def dispatch(task):
        return result(task, text="done", usage={"input_tokens": 10}, usage_known=False)

    hub = FakeHub(dispatch)
    hub.agents["worker"].update(engine="codex", model="gpt-6.1-sol")
    hub.approve_budget = True
    orch = Orchestrator(hub)
    for index in range(5):  # $5 held per task (the default per_task_usd) against $10, then $20
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt=f"step {index}"))

    assert len(hub.calls) == 5 and hub.requests["r"]["budget_usd"] == 40
    assert [(o["unknown_count"], o["limit_usd"]) for o in orch.budget_outcomes["r"]] == [(3, 10), (5, 20)]


@pytest.mark.asyncio
async def test_per_task_budget_off_holds_an_unknown_task_at_the_whole_cap():
    async def dispatch(task):
        return result(task, text="done", cost_usd=0.5)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.per_task_usd = 0
    hub.requests["r"].update(cost_items={"old": {"task_id": "old", "engine": "gemini", "status": "unknown",
                                                 "usd": None, "reason": "price_missing"}})
    orch = Orchestrator(hub)
    orch._seed_cost("r", hub.requests["r"])
    await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work"))

    assert len(hub.approvals) == 1 and hub.approvals[0]["detail"]["unknown_reserve_usd"] == 10


@pytest.mark.asyncio
async def test_estimated_codex_cost_counts_toward_the_request_budget():
    async def dispatch(task):
        return result(task, text="done", usage={"input_tokens": 1_000_000, "cached_input_tokens": 0,
                                                "cache_write_input_tokens": 0, "output_tokens": 1_000_000})

    hub = FakeHub(dispatch)
    hub.agents["worker"].update(engine="codex", model="gpt-6.1-sol")
    orch = Orchestrator(hub)
    await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work"))

    assert orch.cost["r"] == pytest.approx(12.0)  # $2 input + $10 output, over the $10 cap
    assert hub.approvals[0]["detail"] == {"spent_usd": pytest.approx(12.0), "limit_usd": 10,
                                          "requested_budget_usd": pytest.approx(20.0)}


@pytest.mark.asyncio
async def test_resumed_request_remembers_unknown_costs_from_the_gateway_record():
    hub = FakeHub(lambda task: None)
    hub.requests["r"].update(budget_usd=5, cost_usd=0.5, cost_known=False, cost_by_task={"t0": 0.0, "t1": 0.5},
                             cost_items={"t0": {"task_id": "t0", "engine": "codex", "status": "unknown",
                                                "usd": None, "reason": "price_missing"},
                                         "t1": {"task_id": "t1", "engine": "claude_code", "status": "actual",
                                                "usd": 0.5}})
    orch = Orchestrator(hub)
    orch.cost["r"] = 0.5
    orch._seed_cost("r", hub.requests["r"])
    with pytest.raises(BudgetExceeded, match="unaccounted"):  # $0.50 + one task held at $5 > $5
        await orch._check_budget("r")
    hub.requests["r"]["budget_usd"] = 11  # raised before the restart
    orch.budget_denials.clear()
    await orch._check_budget("r")
    assert len(hub.approvals) == 1


@pytest.mark.asyncio
async def test_report_keeps_confirmed_estimated_and_unaccounted_costs_apart():
    from labhq.costs import aggregate_costs, classify_cost

    hub = FakeHub(lambda task: None)
    items = {
        "t1": classify_cost(engine="claude_code", model="opus", usage={}, usage_known=True,
                            cost_usd=1.0, cost_known=True, task_id="t1"),
        "t2": classify_cost(engine="codex", model="gpt-6.1-sol", usage={
            "input_tokens": 250_000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
            "output_tokens": 0}, usage_known=True, cost_usd=None, cost_known=False, task_id="t2"),
        "t3": classify_cost(engine="codex", model=None, usage={}, usage_known=True,
                            cost_usd=None, cost_known=False, task_id="t3"),
    }
    hub.requests["r"].update(plan={"steps": [{"id": "A"}]}, cost_known=False, cost_usd=1.5,
                             cost_items=items, cost_summary=aggregate_costs(items))
    orch = Orchestrator(hub)
    orch.cost["r"] = 1.5
    orch.budget_outcomes["r"] = [{"spent_usd": 1.5, "limit_usd": 10, "approved": False, "unknown_count": 1}]
    orch._finish("r", "Narrative", {"A": {"status": "done"}}, ok=True)
    report = hub.requests["r"]["report_appendix"]

    assert ("비용: 확인 $1.00 + 추정 $0.50 + 미집계 1건 "
            "(claude_code 확인 $1.00 · codex 추정 $0.50 + 미집계 1건); 추정은") in report
    assert "청구액이 아닙니다" in report and "$0.00" not in report
    assert "Budget: $1.50 + 미집계 1건 / $10.00; denied." in report


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
    (TaskResult(task_id="t", agent_id="a", ok=False, error='invalid model selection (--model "gemini-3.8-flash-high" --effort ""): model gemini-3.8-flash-high is not recognized'), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="model catalog unavailable; try again"), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="model catalog not found; try again"), "transient"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="permission denied; try again"), "terminal"),
    (TaskResult(task_id="t", agent_id="a", ok=False, error="invalid model selection: unsupported model"), "terminal"),
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
    issue = {"step_id": "A", "priority": "P1", "problem": "wrong model", "request": "use paired data"}
    assert valid_review({**valid, "issues": [issue]})
    assert not valid_review({**valid, "issues": [{key: value for key, value in issue.items()
                                                  if key != "priority"}]})
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
async def test_targeted_revision_waits_for_a_transitive_ancestor_outside_its_direct_dependencies():
    """s9 must wait for revised s5 even though unchanged s8 is its only direct dependency."""
    s5_started = asyncio.Event()
    release_s5 = asyncio.Event()
    s5_done = asyncio.Event()
    s9_started = asyncio.Event()

    async def dispatch(task):
        sid = task.meta["step_id"]
        if sid == "s5":
            s5_started.set()
            await release_s5.wait()
            s5_done.set()
        elif sid == "s9":
            assert s5_done.is_set(), "s9 started before its revised transitive ancestor s5 finished"
            s9_started.set()
        return result(task, text=f"{sid} revised")

    steps = [
        {"id": "s5", "agent_id": "worker", "instruction": "revise source", "depends_on": []},
        {"id": "s8", "agent_id": "worker", "instruction": "unchanged bridge", "depends_on": ["s5"]},
        {"id": "s9", "agent_id": "worker", "instruction": "revise report", "depends_on": ["s8"]},
    ]
    outcomes = {sid: TaskResult(task_id=f"old-{sid}", agent_id="worker", ok=True, text=f"old {sid}")
                for sid in ("s5", "s8", "s9")}
    run = asyncio.create_task(Orchestrator(FakeHub(dispatch)).run_dag(
        "r", "question", steps, outcomes, only={"s5", "s9"},
        feedback={"s5": "fix source", "s9": "use corrected source"}))
    await s5_started.wait()
    await asyncio.sleep(0)
    assert not s9_started.is_set()
    release_s5.set()
    await run
    assert s9_started.is_set()


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
    assert "Budget: $0.60 > $0.50" in req["report_appendix"]
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
                                            "issues": [{"step_id": "A", "priority": "P1", "problem": "check",
                                                        "request": "retry"}]
                                            if revise else []})
        return result(task, text="final")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: agent_id == "worker"
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done"
    assert req["results"]["A"]["text"] == "good evidence"
    assert "revision broke" in req["results"]["A"]["revision_failed"]
    assert "revision failed" in req["report_appendix"]
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
async def test_max_turns_wraps_once_and_keeps_failure(resume, continuations):
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            assert task.resume_session_id == "session-1"
            assert task.meta["agent_overrides"]["max_turns"] == 4
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
    assert len(continuations) == (1 if resume else 0)
    if resume:
        assert hub.calls[1].prompt == continuations[0]["prompt"]
        assert continuations[0]["resumable"] is True


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
    assert req["results"]["A"]["missing_outputs"] == ["outputs/table.tsv"]
    assert "A [terminal]" in req["report_appendix"] and "table.tsv" in req["report_appendix"]
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
    (r"outputs\table.tsv", "outputs/table.tsv"), (" outputs/table.tsv ", "outputs/table.tsv"),
    ("outputs/dir/../table.tsv", None), ("tmp/../table.tsv", None), ("~/table.tsv", None),
    ("../escape.tsv", None), ("/abs/t.tsv", None), ("C:/x/t.tsv", None),
])
def test_declared_outputs_normalize_like_the_runner(name, expected):
    from labhq.util import output_relpath

    assert output_relpath(name) == expected


# ---- #220: declared step outputs stay under the step's outputs/ ----------------------------------

PENGUINS_PLAN = Path(__file__).parent / "fixtures" / "plans" / "penguins_outputs_root.json"
ROOT_REF = r"(?<![A-Za-z0-9_./\-])\./answer\.md"


def literal_agent(task):
    """Write exactly the `./path` files the step instruction names, then report what the runner collects.

    The runner keeps a declared output only when it exists under the workspace outputs/ folder."""
    from labhq.util import output_relpath

    written = {p for p in re.findall(r"(?<![A-Za-z0-9_./\-])\./([A-Za-z0-9_./-]+\.[A-Za-z]+)",
                                     task.meta["instruction"])}
    found = [output_relpath(o) for o in task.meta.get("outputs", []) if output_relpath(o) in written]
    return result(task, text=f"{task.meta['step_id']} done", workdir_id=task.meta["step_id"], outputs=found)


def penguins_hub(plans):
    fixture = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))
    plans = [fixture["plan"] if p == "fixture" else p for p in plans]

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured=plans[min(len([t for t in hub.calls if t.meta["kind"] == "plan"]),
                                                      len(plans)) - 1])
        if task.meta["kind"] == "synthesis":
            return result(task, text="final report")
        return literal_agent(task)

    hub = FakeHub(dispatch)
    hub.requests["r"]["text"] = fixture["request"]
    hub.s.orchestrator.reviewer_agent = None
    for name in ("data_steward", "analyst", "qc_reviewer"):
        hub.agents[name] = {"id": name, "name": name, "role": "test", "engine": "mock"}
    return hub


@pytest.mark.asyncio
async def test_penguins_plan_answer_at_workspace_root_is_moved_under_outputs():
    hub = penguins_hub(["fixture"])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert [req["results"][s]["status"] for s in ("materialize_data", "compute_qc_metrics", "qc_review")] == [
        "done", "done", "done"]
    by = {s["id"]: s for s in req["plan"]["steps"]}
    assert by["compute_qc_metrics"]["outputs"] == ["outputs/qc_calculations.md", "outputs/answer.md"]
    assert "./outputs/answer.md" in by["compute_qc_metrics"]["instruction"]
    assert not re.search(ROOT_REF, by["compute_qc_metrics"]["instruction"])
    assert any("answer.md" in w and "outputs/answer.md" in w for w in req["plan"]["warnings"])
    step = next(t for t in hub.calls if t.meta.get("step_id") == "compute_qc_metrics")
    assert "./outputs/qc_calculations.md, ./outputs/answer.md" in step.prompt


@pytest.mark.parametrize("bad", ["../answer.md", "/tmp/answer.md", "C:/work/answer.md", r"outputs\..\..\answer.md"])
def test_output_outside_the_workspace_outputs_is_rejected(bad):
    raw = [{"id": "a", "agent_id": "analyst", "instruction": "write the report", "outputs": ["outputs/ok.md", bad]},
           {"id": "b", "agent_id": "analyst", "instruction": "write notes", "outputs": ["/srv/notes.md"]}]
    with pytest.raises(ValueError, match="outside its outputs/") as error:
        validate_steps(raw, {"analyst"}, 10)
    assert type(error.value).__name__ == "PlanOutputsError"
    assert f"step a: outputs [{bad!r}]" in str(error.value) and "step b: outputs ['/srv/notes.md']" in str(error.value)


def test_output_normalization_touches_only_root_references_to_own_outputs():
    raw = [{"id": "a", "agent_id": "analyst", "outputs": ["outputs/t.tsv", r".\notes.md"],
            "instruction": r"Write ./t.tsv and .\notes.md. Keep ../t.tsv, ./outputs/t.tsv and ./other.tsv as they are."},
           {"id": "b", "agent_id": "analyst", "outputs": ["table.tsv"], "instruction": "make table.tsv"},
           {"id": "c", "agent_id": "analyst", "outputs": [], "instruction": "compare ./t.tsv with table.tsv"}]
    steps, warnings = validate_steps(raw, {"analyst"}, 10)
    by = {s["id"]: s for s in steps}
    assert by["a"]["outputs"] == ["outputs/t.tsv", "outputs/notes.md"]
    assert by["a"]["instruction"] == ("Write ./outputs/t.tsv and ./outputs/notes.md. "
                                      "Keep ../t.tsv, ./outputs/t.tsv and ./other.tsv as they are.")
    assert by["b"] == {**raw[1], "id": "b", "instruction": "make ./outputs/table.tsv",
                       "outputs": ["outputs/table.tsv"], "depends_on": []}
    assert by["c"]["instruction"] == raw[2]["instruction"]  # another step's reference is not rewritten
    assert by["c"]["depends_on"] == ["a", "b"]  # normalized references drive both dependencies
    assert sum("moved under outputs/" in w for w in warnings) == 3


def test_output_normalization_rewrites_instruction_paths_case_insensitively_and_deduplicates():
    raw = [{"id": "a", "agent_id": "analyst", "outputs": [" answer.md ", "./answer.md"],
            "instruction": "Save /abs/ANSWER.md"}]
    steps, warnings = validate_steps(raw, {"analyst"}, 10)
    assert steps[0]["outputs"] == ["outputs/answer.md"]
    assert steps[0]["instruction"] == "Save ./outputs/answer.md"
    assert len([w for w in warnings if "moved under outputs/" in w]) == 1


def test_bare_workspace_output_path_is_rewritten_from_a_postposed_action():
    raw = [{"id": "a", "agent_id": "analyst", "outputs": ["Answer.md"],
            "instruction": "작업 폴더의 answer.md에 저장"}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    assert steps[0]["instruction"] == "작업 폴더의 ./outputs/Answer.md에 저장"


@pytest.mark.parametrize("instruction", [
    "Read /datasets/report.md, then write report.md",
    "Write report.md after reading /datasets/report.md",
    "Save a summary of /datasets/report.md to report.md",
])
def test_same_basename_input_path_is_not_rewritten_as_the_declared_output(instruction):
    raw = [{"id": "a", "agent_id": "analyst", "outputs": ["report.md"], "instruction": instruction}]
    with pytest.raises(ValueError, match="ambiguous instruction paths"):
        validate_steps(raw, {"analyst"}, 10)
    assert "/datasets/report.md" in raw[0]["instruction"]


@pytest.mark.parametrize("source", ["/datasets/report.md", "~/datasets/report.md", "C:/datasets/report.md"])
def test_single_external_input_same_basename_requires_an_explicit_output_reference(source):
    raw = [{"id": "a", "agent_id": "analyst", "outputs": ["report.md"],
            "instruction": f"Save a summary of {source}"}]
    with pytest.raises(ValueError, match="ambiguous instruction paths"):
        validate_steps(raw, {"analyst"}, 10)
    assert raw[0]["instruction"] == f"Save a summary of {source}"


def test_nested_home_output_path_is_rewritten_as_one_reference():
    raw = [{"id": "a", "agent_id": "analyst", "outputs": ["report.md"],
            "instruction": "Save ~/documents/reports/report.md"}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    assert steps[0]["instruction"] == "Save ./outputs/report.md"


def test_normalized_output_names_drive_dependency_inference():
    raw = [{"id": "producer", "agent_id": "analyst", "outputs": ["./answer.md"],
            "instruction": "write ./answer.md"},
           {"id": "consumer", "agent_id": "analyst", "outputs": [],
            "instruction": "read answer.md", "depends_on": []}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    by = {step["id"]: step for step in steps}
    assert by["producer"]["outputs"] == ["outputs/answer.md"]
    assert by["consumer"]["depends_on"] == ["producer"]


@pytest.mark.parametrize("source", ["/datasets/report.md", "~/datasets/report.md", "C:/datasets/report.md"])
def test_external_input_basename_does_not_drive_dependency_inference(source):
    raw = [{"id": "producer", "agent_id": "analyst", "outputs": ["report.md"],
            "instruction": "write report.md"},
           {"id": "independent", "agent_id": "analyst", "outputs": [],
            "instruction": f"Read {source}", "depends_on": []}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    by = {step["id"]: step for step in steps}
    assert by["independent"]["depends_on"] == []


@pytest.mark.parametrize("reference", ["outputs/report.md", "./outputs/report.md"])
def test_canonical_output_path_drives_dependency_inference(reference):
    raw = [{"id": "producer", "agent_id": "analyst", "outputs": ["report.md"],
            "instruction": "write report.md"},
           {"id": "consumer", "agent_id": "analyst", "outputs": [],
            "instruction": f"Read {reference}", "depends_on": []}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    by = {step["id"]: step for step in steps}
    assert by["consumer"]["depends_on"] == ["producer"]


@pytest.mark.parametrize("name", ["input.csv", "read.txt", "input/report.md"])
def test_action_words_inside_output_path_are_not_instruction_actions(name):
    raw = [{"id": "a", "agent_id": "analyst", "outputs": [name],
            "instruction": f"Save {name}"}]
    steps, _ = validate_steps(raw, {"analyst"}, 10)
    assert steps[0]["instruction"] == f"Save ./outputs/{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [True, False])
async def test_plan_with_output_outside_outputs_is_replanned_before_dispatch(corrected):
    fixture = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))["plan"]
    bad = json.loads(json.dumps(fixture))
    bad["steps"][1]["outputs"] = ["outputs/qc_calculations.md", "../answer.md"]
    hub = penguins_hub([bad, fixture if corrected else bad])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    plans = [t for t in hub.calls if t.meta["kind"] == "plan"]
    assert len(plans) == 2
    assert "failed validation" in plans[1].prompt and "../answer.md" in plans[1].prompt
    steps = [t for t in hub.calls if t.meta["kind"] == "step"]
    if corrected:
        assert req["status"] == "done", req.get("report")
        assert all(o.startswith("outputs/") for t in steps for o in t.meta["outputs"])
    else:
        assert req["status"] == "failed" and "after correction" in req["error"]
        assert steps == []


@pytest.mark.asyncio
async def test_corrected_plan_with_new_questions_does_not_dispatch():
    fixture = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))["plan"]
    bad = json.loads(json.dumps(fixture))
    bad["steps"][1]["outputs"] = ["/tmp/answer.md"]
    asking = {**fixture, "clarifying_questions": ["Which species should the QC cover?"]}
    hub = penguins_hub([bad, asking])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert req["pending_questions"] == ["Which species should the QC cover?"]
    assert [t for t in hub.calls if t.meta["kind"] == "step"] == []


@pytest.mark.asyncio
async def test_corrected_plan_questions_follow_disabled_clarification_policy():
    fixture = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))["plan"]
    bad = json.loads(json.dumps(fixture))
    bad["steps"][1]["outputs"] = ["/tmp/answer.md"]
    asking = {**fixture, "clarifying_questions": ["Which species should the QC cover?"]}
    hub = penguins_hub([bad, asking])
    hub.s.orchestrator.wait_for_clarification = False
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"
    assert hub.requests["r"]["pending_questions"] == ["Which species should the QC cover?"]
    assert [t for t in hub.calls if t.meta["kind"] == "step"]


@pytest.mark.asyncio
async def test_resume_revalidates_and_normalizes_stored_plan_outputs():
    async def dispatch(task):
        return result(task, text="done", outputs=["outputs/answer.md"])

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    hub.requests["r"]["plan"] = {"steps": [{"id": "A", "agent_id": "worker",
                                               "instruction": "write ./answer.md",
                                               "outputs": ["answer.md"], "depends_on": []}]}
    await Orchestrator(hub).run_request("r", resume=True)
    assert hub.requests["r"]["plan"]["steps"][0]["outputs"] == ["outputs/answer.md"]
    assert hub.calls[0].meta["outputs"] == ["outputs/answer.md"]


@pytest.mark.asyncio
async def test_resume_rejects_stored_plan_when_max_steps_was_reduced():
    """#282: a stored plan longer than today's max_steps fails loudly instead of losing its tail steps."""
    async def dispatch(task):
        raise AssertionError("an over-limit stored plan must not dispatch")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.max_steps = 1
    hub.s.orchestrator.reviewer_agent = None
    hub.requests["r"]["plan"] = {"steps": [
        {"id": "A", "agent_id": "worker", "instruction": "first", "outputs": [], "depends_on": []},
        {"id": "B", "agent_id": "worker", "instruction": "second", "outputs": [], "depends_on": ["A"]},
    ]}
    hub.requests["r"]["results"] = {"A": result(Task(agent_id="worker", prompt="first"),
                                                text="A done").model_dump(mode="json")}

    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert [step["id"] for step in req["plan"]["steps"]] == ["A", "B"]
    assert "2 steps" in req["error"] and "maximum is 1" in req["error"]
    assert "### B · worker (FAILED" in req["report_appendix"]
    assert hub.calls == []


def test_new_plan_keeps_truncating_to_max_steps():
    """#282 changes only stored plans; a fresh CSO plan is still cut to max_steps as before."""
    raw = [{"id": sid, "agent_id": "worker", "instruction": sid, "depends_on": []} for sid in ("A", "B", "C")]
    steps, _ = validate_steps(raw, {"worker"}, 2)
    assert [step["id"] for step in steps] == ["A", "B"]
    with pytest.raises(ValueError, match="has 3 steps; maximum is 2"):
        validate_steps(raw, {"worker"}, 2, reject_excess=True)


def replan_plan(steps, drop=(), questions=(), notes="re-plan"):
    return {"clarifying_questions": list(questions), "steps": steps, "drop": list(drop), "recruit": [],
            "notes": notes}


def replan_hub(original, on_step, on_replan, *, max_replans=1, max_failure_replans=None, on_review=None):
    """A FakeHub whose CSO plans ``original`` once and answers every re-plan with ``on_replan``."""
    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured=replan_plan(original, notes="initial"))
        if kind == "replan":
            return result(task, structured=on_replan(task))
        if kind == "step":
            return on_step(task)
        if kind == "review" and on_review:
            return result(task, structured=on_review(task))
        assert kind == "synthesis", kind
        return result(task, text="final")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.max_replans = max_replans
    hub.s.orchestrator.max_failure_replans = (max_replans if max_failure_replans is None
                                               else max_failure_replans)
    if on_review is None:
        hub.s.orchestrator.reviewer_agent = None
    return hub


def kinds(hub, kind):
    return [task for task in hub.calls if task.meta["kind"] == kind]


def step_ids(hub):
    return [task.meta["step_id"] for task in kinds(hub, "step")]


@pytest.mark.asyncio
async def test_failure_replan_can_be_disabled_and_failure_report_is_unchanged():
    assert Settings().orchestrator.max_failure_replans == 1
    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, ok=False, error="tool unavailable"),
                     lambda task: pytest.fail("failure re-plan must stay off"),
                     max_replans=1, max_failure_replans=0)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed" and not kinds(hub, "replan")
    assert "replan_progress" not in req and "Re-plan" not in req["report"]


@pytest.mark.asyncio
async def test_fallback_path_using_the_same_declared_filename_is_not_incomplete():
    original = [{"id": "fetch", "agent_id": "worker", "instruction": "fetch raw counts; use RPKM if unavailable",
                 "outputs": ["outputs/expression_matrix.tsv.gz"], "depends_on": []},
                {"id": "analyze", "agent_id": "worker", "instruction": "analyze counts",
                 "depends_on": ["fetch"]}]
    fallback_text = """## Findings
RPKM data were downloaded.
## Evidence
- `outputs/expression_matrix.tsv.gz`
## Not established
Raw counts were unavailable.
## Method changes
Used RPKM instead of raw counts.
"""

    def on_step(task):
        if task.meta["step_id"] == "fetch":
            return result(task, text=fallback_text, outputs=["outputs/expression_matrix.tsv.gz"])
        assert task.meta["step_id"] == "analyze"
        return result(task, text="analysis complete", outputs=["outputs/analysis.md"])

    def on_replan(task):
        pytest.fail("the fallback used the declared filename, so no failure re-plan is needed")

    hub = replan_hub(original, on_step, on_replan, max_replans=0, max_failure_replans=1)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["fetch", "analyze"]
    assert req["results"]["fetch"]["status"] == "done"
    assert "replan_history" not in req


@pytest.mark.asyncio
async def test_review_replan_uses_max_replans_not_failure_cap():
    original = [{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "method",
                            "request": "revise in place"}]
                if revise else []}

    hub = replan_hub(original, lambda task: result(task, text="analysis done"),
                     lambda task: pytest.fail("review revise must not use the failure cap"),
                     max_replans=0, max_failure_replans=1, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done"
    assert not kinds(hub, "replan")
    assert step_ids(hub) == ["analysis", "analysis"]


@pytest.mark.asyncio
async def test_failed_in_place_revision_keeps_its_result_and_does_not_failure_replan():
    """PR #378 review: a failed in-place revision keeps the step's last good result (revision_failed), so it is
    not a step failure and spends no failure re-plan; the request goes on to the next review."""
    original = [{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]

    def on_step(task):
        sid = task.meta["step_id"]
        if sid == "analysis" and task.meta["revision"]:
            return result(task, ok=False, error="revision broke")
        return result(task, text=f"{sid} done")

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "method",
                            "request": "revise in place"}]
                if revise else []}

    def on_replan(task):
        raise AssertionError("a failed in-place revision must not trigger a failure re-plan")

    hub = replan_hub(original, on_step, on_replan, max_replans=0, max_failure_replans=1,
                     on_review=on_review)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["analysis", "analysis"]
    assert "revision broke" in req["results"]["analysis"]["revision_failed"]
    assert not req.get("replan_history")


@pytest.mark.asyncio
async def test_failed_upstream_replans_only_remaining_dag_and_keeps_success():
    original = [
        {"id": "seed", "agent_id": "worker", "instruction": "make seed", "outputs": ["outputs/seed.tsv"],
         "depends_on": []},
        {"id": "primary", "agent_id": "worker", "instruction": "primary analysis", "depends_on": ["seed"]},
        {"id": "report", "agent_id": "worker", "instruction": "use primary", "depends_on": ["primary"]},
    ]

    def on_step(task):
        sid = task.meta["step_id"]
        if sid == "seed":
            return result(task, text="seed complete", workdir="runs/seed", workdir_id="seed",
                          outputs=["outputs/seed.tsv"])
        if sid == "primary":
            return result(task, ok=False, error="primary analysis failed")
        assert sid == "alternative", sid
        assert "outputs/seed.tsv" in task.context
        return result(task, text="fallback complete", outputs=["outputs/alternative.md"])

    def on_replan(task):
        assert "primary analysis failed" in task.prompt and "outputs/seed.tsv" in task.prompt
        assert task.meta["revision"] == 1
        return replan_plan([{"id": "alternative", "agent_id": "worker", "instruction": "use seed another way",
                             "outputs": ["outputs/alternative.md"], "depends_on": ["seed"]}])

    hub = replan_hub(original, on_step, on_replan)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["seed", "primary", "alternative"]
    assert [step["id"] for step in req["plan"]["steps"]] == ["seed", "alternative"]
    assert set(req["results"]) == {"seed", "alternative"}
    entry = req["replan_history"][0]
    assert entry["status"] == "applied" and entry["trigger"] == "step_failure"
    assert entry["retired"] == ["primary", "report"] and entry["added"] == ["alternative"]
    assert entry["prior_results"]["primary"]["error"] == "primary analysis failed"
    assert req["replan_progress"] == {"attempts": 1, "max": 1, "in_flight": False}
    # The PI still reads why the original method was replaced, not only that it was.
    assert ("Re-plan history:\n- #1 step_failure: applied; retired: primary (primary analysis failed), report; "
            "added: alternative") in req["report_appendix"]


@pytest.mark.asyncio
async def test_reviewer_sees_which_failed_step_a_replan_replaced():
    """A reviewer judging the fallback must know the primary method failed, or it reviews a different question."""
    original = [{"id": "primary", "agent_id": "worker", "instruction": "primary analysis", "depends_on": []}]
    accept = {"verdict": "accept", "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
              "issues": []}
    hub = replan_hub(original,
                     lambda task: (result(task, ok=False, error="primary analysis failed")
                                   if task.meta["step_id"] == "primary" else result(task, text="fallback done")),
                     lambda task: replan_plan([{"id": "fallback", "agent_id": "worker", "instruction": "fallback",
                                                "depends_on": []}]),
                     on_review=lambda task: accept)
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("report")
    prompt = kinds(hub, "review")[0].prompt
    assert "re-plan history" in prompt and "retired: primary (primary analysis failed)" in prompt


@pytest.mark.asyncio
async def test_failure_replan_saves_new_plan_and_retired_results_together():
    """A restart between two saves must never see the old plan without a result it already has (#271).

    The old plan minus a failed step's result would rerun that step; the real SavedResults saves on every pop.
    """
    from labhq.gateway.server import SavedResults

    original = [
        {"id": "kept", "agent_id": "worker", "instruction": "keep", "depends_on": []},
        {"id": "broken", "agent_id": "worker", "instruction": "break", "depends_on": ["kept"]},
        {"id": "after", "agent_id": "worker", "instruction": "use broken", "depends_on": ["broken"]},
    ]
    hub = replan_hub(original,
                     lambda task: (result(task, ok=False, error="tool unavailable")
                                   if task.meta["step_id"] == "broken" else result(task, text="ok")),
                     lambda task: replan_plan([{"id": "fix", "agent_id": "worker", "instruction": "fix",
                                                "depends_on": ["kept"]}]))
    hub.clear_step_jobs = lambda rid, sid: None
    hub.result_map = lambda rid: SavedResults(hub, rid)
    saves = []

    def save(rid):
        req = hub.requests[rid]
        saves.append(({step["id"] for step in (req.get("plan") or {}).get("steps") or []},
                      set(req.get("results") or {})))

    hub.save_request = save
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("report")
    ran = set()
    for plan_ids, result_ids in saves:
        lost = (ran & plan_ids) - result_ids
        assert not lost, f"saved the plan {sorted(plan_ids)} without the results of {sorted(lost)}"
        ran |= result_ids & plan_ids


@pytest.mark.asyncio
async def test_replan_does_not_route_around_a_cancelled_step():
    """A cancelled task is a PI decision like a rejected question; the CSO must not plan around it."""
    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, ok=False, error="cancelled"),
                     lambda task: pytest.fail("a cancelled step must not be re-planned around"))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed" and not kinds(hub, "replan")
    assert req["replan_history"][0]["status"] == "blocked"
    assert "A: cancelled" in req["report_appendix"] and req["replan_progress"]["attempts"] == 0


@pytest.mark.asyncio
async def test_review_revise_can_add_only_needed_step_without_rerunning_successes():
    original = [
        {"id": "evidence", "agent_id": "worker", "instruction": "collect evidence",
         "outputs": ["outputs/evidence.tsv"], "depends_on": []},
        {"id": "analysis", "agent_id": "worker", "instruction": "analyze evidence",
         "outputs": ["outputs/analysis.md"], "depends_on": ["evidence"]},
    ]

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "robustness",
                            "request": "add a sensitivity check"}] if revise else []}

    def on_replan(task):
        assert "add a sensitivity check" in task.prompt and task.meta["trigger"] == "review_revise"
        return replan_plan([{"id": "sensitivity", "agent_id": "worker", "instruction": "check robustness",
                             "outputs": ["outputs/sensitivity.md"], "depends_on": ["analysis"]}])

    hub = replan_hub(original, lambda task: result(task, text=f"{task.meta['step_id']} done",
                                                   outputs=task.meta["outputs"]),
                     on_replan, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["evidence", "analysis", "sensitivity"]
    assert req["results"]["analysis"]["text"] == "analysis done"
    assert [step["id"] for step in req["plan"]["steps"]] == ["evidence", "analysis", "sensitivity"]
    assert len(kinds(hub, "review")) == 2


@pytest.mark.asyncio
async def test_review_replan_replaces_flagged_step_with_its_dependents_only():
    original = [
        {"id": "evidence", "agent_id": "worker", "instruction": "collect evidence",
         "outputs": ["outputs/evidence.tsv"], "depends_on": []},
        {"id": "analysis", "agent_id": "worker", "instruction": "analyze evidence",
         "outputs": ["outputs/analysis.md"], "depends_on": ["evidence"]},
        {"id": "summary", "agent_id": "worker", "instruction": "summarize", "depends_on": ["analysis"]},
    ]

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 3, "evidence": 2, "thoroughness": 3},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "wrong model",
                            "request": "use a mixed model"}] if revise else []}

    def on_replan(task):
        return replan_plan([
            {"id": "analysis_mixed", "agent_id": "worker", "instruction": "mixed model on evidence",
             "outputs": ["outputs/mixed.md"], "depends_on": ["evidence"]},
            {"id": "summary_mixed", "agent_id": "worker", "instruction": "summarize the mixed model",
             "depends_on": ["analysis_mixed"]},
        ], drop=["analysis"])

    hub = replan_hub(original, lambda task: result(task, text=f"{task.meta['step_id']} done",
                                                   workdir_id=task.meta["step_id"], outputs=task.meta["outputs"]),
                     on_replan, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["evidence", "analysis", "summary", "analysis_mixed", "summary_mixed"]
    assert [step["id"] for step in req["plan"]["steps"]] == ["evidence", "analysis_mixed", "summary_mixed"]
    entry = req["replan_history"][0]
    assert entry["retired"] == ["analysis", "summary"] and entry["trigger"] == "review_revise"
    assert entry["prior_results"]["analysis"] == {"ok": True, "error": None, "error_kind": None,
                                                  "outputs": ["outputs/analysis.md"], "workdir_id": "analysis"}
    assert set(req["results"]) == {"evidence", "analysis_mixed", "summary_mixed"}


@pytest.mark.asyncio
async def test_review_replan_declined_falls_back_to_targeted_revision():
    original = [{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 3, "thoroughness": 4},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "typo",
                            "request": "fix the table"}] if revise else []}

    hub = replan_hub(original, lambda task: result(task, text="analysis done"),
                     lambda task: replan_plan([], notes="revise in place"), on_review=on_review)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert step_ids(hub) == ["analysis", "analysis"]
    assert "fix the table" in kinds(hub, "step")[1].prompt
    assert req["replan_history"][0]["status"] == "declined"
    assert [step["id"] for step in req["plan"]["steps"]] == ["analysis"]


@pytest.mark.asyncio
async def test_invalid_replan_keeps_success_failure_and_reports_replan_reason():
    original = [
        {"id": "kept", "agent_id": "worker", "instruction": "keep", "depends_on": []},
        {"id": "broken", "agent_id": "worker", "instruction": "break", "depends_on": ["kept"]},
    ]
    hub = replan_hub(original, lambda task: (result(task, text="kept evidence") if task.meta["step_id"] == "kept"
                                             else result(task, ok=False, error="tool unavailable")),
                     lambda task: replan_plan([{"id": "bad", "agent_id": "worker", "instruction": "fallback",
                                                "depends_on": ["missing"]}]))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert req["results"]["kept"]["text"] == "kept evidence"
    assert req["results"]["broken"]["error"] == "tool unavailable"
    assert [step["id"] for step in req["plan"]["steps"]] == ["kept", "broken"]
    assert "tool unavailable" in req["report_appendix"]
    assert "#1 step_failure: failed; reason: step bad: invalid dependencies ['missing']" in req["report_appendix"]


@pytest.mark.parametrize("candidate, reason", [
    (replan_plan([{"id": "kept", "agent_id": "worker", "instruction": "again", "depends_on": []}]),
     "re-plan reuses step ids already in this request: ['kept']"),
    (replan_plan([{"id": "broken", "agent_id": "worker", "instruction": "retry", "depends_on": []}]),
     "re-plan reuses step ids already in this request: ['broken']"),
    (replan_plan([{"id": "new", "agent_id": "worker", "instruction": "x", "depends_on": []}], drop=["kept"]),
     "re-plan may drop only reviewer-flagged completed steps []; got ['kept']"),
    (replan_plan([{"id": "new", "agent_id": "cso", "instruction": "x", "depends_on": []}]),
     "re-plan uses unavailable or orchestration agents: ['cso']"),
    (replan_plan([{"id": f"n{i}", "agent_id": "worker", "instruction": "x", "depends_on": []} for i in range(3)]),
     "re-plan has 4 steps with the kept ones; maximum is 3"),
    (replan_plan([], notes="impossible safely"), "impossible safely"),
])
@pytest.mark.asyncio
async def test_replan_candidate_is_checked_before_anything_changes(candidate, reason):
    original = [
        {"id": "kept", "agent_id": "worker", "instruction": "keep", "depends_on": []},
        {"id": "broken", "agent_id": "worker", "instruction": "break", "depends_on": ["kept"]},
    ]
    hub = replan_hub(original, lambda task: (result(task, text="kept evidence") if task.meta["step_id"] == "kept"
                                             else result(task, ok=False, error="tool unavailable")),
                     lambda task: candidate)
    hub.s.orchestrator.max_steps = 3
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed" and step_ids(hub) == ["kept", "broken"]
    assert [step["id"] for step in req["plan"]["steps"]] == ["kept", "broken"]
    assert reason in req["replan_history"][0]["reason"]


@pytest.mark.asyncio
async def test_replan_cap_stops_repeated_failures():
    seen = []

    def on_replan(task):
        seen.append(task.meta["revision"])
        return replan_plan([{"id": f"retry{len(seen)}", "agent_id": "worker", "instruction": "retry",
                             "depends_on": []}])

    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, ok=False, error=f"{task.meta['step_id']} failed"), on_replan,
                     max_replans=2)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed" and seen == [1, 2]
    assert step_ids(hub) == ["A", "retry1", "retry2"]
    assert req["replan_history"][-1] == {"attempt": None, "trigger": "step_failure", "status": "limit",
                                         "reason": "re-plan limit reached (2/2)"}
    assert "retry2 failed" in req["report_appendix"]


@pytest.mark.asyncio
async def test_replan_does_not_route_around_a_rejected_pi_decision():
    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, ok=False, error_kind="ask_rejected", error="PI rejected: no DUA data"),
                     lambda task: pytest.fail("a PI rejection must not be re-planned around"))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed" and not kinds(hub, "replan")
    assert req["replan_history"][0]["status"] == "blocked"
    assert "A: a PI decision rejected this step" in req["report_appendix"]
    assert req["replan_progress"]["attempts"] == 0


@pytest.mark.asyncio
async def test_replan_does_not_route_around_live_jobs_at_the_wake_limit():
    """run_step clears pending_jobs when it stops at the wake limit; those jobs can still be running (#271)."""
    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, text="submitted", pending_jobs=["j1"]),
                     lambda task: pytest.fail("a step stopped with live jobs must not be re-planned around"))
    hub.s.orchestrator.max_wake_cycles = 0
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["results"]["A"]["error_kind"] == "wake_limit" and req["results"]["A"]["pending_jobs"] == []
    assert req["status"] == "failed" and not kinds(hub, "replan")
    assert req["replan_history"][0]["status"] == "blocked"
    assert "pending jobs: ['j1']" in req["replan_history"][0]["reason"]
    assert req["replan_progress"]["attempts"] == 0


@pytest.mark.asyncio
async def test_replan_question_goes_through_clarify_gate_and_reports_pending_decision():
    question = "May the fallback use the controlled cohort?"
    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: result(task, ok=False, error="public data too small"),
                     lambda task: replan_plan([], questions=[question]))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert hub.approvals[-1]["kind"] == "clarify"
    assert req["pending_questions"] == [question]
    assert "Pending PI decisions/questions:\n- May the fallback use the controlled cohort?" in req["report_appendix"]
    assert "re-plan needs PI clarification that was denied or unanswered" in req["report_appendix"]


@pytest.mark.asyncio
async def test_replan_answered_clarification_continues_the_same_attempt():
    calls = []

    def on_replan(task):
        calls.append((task.meta["revision"], task.meta["parse_attempt"]))
        if len(calls) == 1:
            return replan_plan([], questions=["Use the smaller panel?"])
        assert "PI answer: yes, smaller panel" in task.prompt
        return replan_plan([{"id": "panel", "agent_id": "worker", "instruction": "smaller panel",
                             "depends_on": []}])

    hub = replan_hub([{"id": "A", "agent_id": "worker", "instruction": "a", "depends_on": []}],
                     lambda task: (result(task, ok=False, error="panel too big") if task.meta["step_id"] == "A"
                                   else result(task, text="panel done")), on_replan)

    async def answer(**kwargs):
        hub.approvals.append(kwargs)
        return {"approved": True, "note": "yes, smaller panel"}

    hub.request_approval = answer
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert calls == [(1, 1), (1, 2)] and req["replan_progress"]["attempts"] == 1
    assert req["clarifications"][-1]["answer"] == "yes, smaller panel"
    assert "yes, smaller panel" in kinds(hub, "step")[-1].prompt


@pytest.mark.asyncio
async def test_resume_during_replan_reuses_the_interrupted_attempt():
    """A restart while the CSO re-plans asks the same attempt again (so ledger recovery can adopt it)."""
    def on_replan(task):
        assert task.meta["revision"] == 1
        return replan_plan([{"id": "B", "agent_id": "worker", "instruction": "b", "depends_on": []}])

    hub = replan_hub([], lambda task: result(task, text="b done"), on_replan)
    hub.requests["r"].update(
        plan={"steps": [{"id": "A", "agent_id": "worker", "instruction": "a", "outputs": [], "depends_on": []}]},
        results={"A": result(Task(agent_id="worker", prompt="a"), ok=False, error="a failed").model_dump(mode="json")},
        replan_progress={"attempts": 1, "max": 1, "in_flight": True})
    hub.result_map = lambda rid: {k: TaskResult.model_validate(v) for k, v in hub.requests[rid]["results"].items()}
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert len(kinds(hub, "replan")) == 1 and step_ids(hub) == ["B"]
    assert req["replan_progress"] == {"attempts": 1, "max": 1, "in_flight": False}


REVISE = {"verdict": "revise", "scores": {"addresses_question": 4, "evidence": 3, "thoroughness": 4},
          "issues": [{"step_id": "analysis", "priority": "P1", "problem": "robustness",
                      "request": "add a sensitivity check"}]}


@pytest.mark.parametrize("phase, plan_ids, reviews, replans", [
    # Restarted during the CSO call: review again (the ledger recovers it), then finish the same attempt.
    ("replan", ["analysis"], [0, 1], 1),
    # Restarted after the new plan was saved: run its new step, then review the next revision.
    ("replanned", ["analysis", "sensitivity"], [1], 0),
])
@pytest.mark.asyncio
async def test_resume_after_review_replan_continues_from_its_phase(phase, plan_ids, reviews, replans):
    def on_review(task):
        return REVISE if task.meta["revision"] == 0 else {**REVISE, "verdict": "accept", "issues": []}

    hub = replan_hub([], lambda task: result(task, text=f"{task.meta['step_id']} done"),
                     lambda task: replan_plan([{"id": "sensitivity", "agent_id": "worker", "instruction": "check",
                                                "depends_on": ["analysis"]}]), on_review=on_review)
    hub.requests["r"].update(
        plan={"steps": [{"id": sid, "agent_id": "worker", "instruction": sid, "outputs": [],
                         "depends_on": [] if sid == "analysis" else ["analysis"]} for sid in plan_ids]},
        results={"analysis": result(Task(agent_id="worker", prompt="a"), text="analysis done").model_dump(mode="json")},
        review_progress={"phase": phase, "next_revision": 1, "review": REVISE, "last_completed_review": 0,
                         "last_completed_revision": 0},
        replan_progress={"attempts": 1, "max": 1, "in_flight": phase == "replan"})
    hub.result_map = lambda rid: {k: TaskResult.model_validate(v) for k, v in hub.requests[rid]["results"].items()}
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert [task.meta["revision"] for task in kinds(hub, "review")] == reviews
    assert len(kinds(hub, "replan")) == replans and step_ids(hub) == ["sensitivity"]
    assert [step["id"] for step in req["plan"]["steps"]] == ["analysis", "sensitivity"]
    assert req["review_progress"]["last_completed_revision"] == 1


@pytest.mark.asyncio
async def test_replan_keeps_completed_step_dependencies_when_its_instruction_names_a_new_id():
    """A completed step already ran; inferring a dependency on a new step from its old instruction must not
    fail the re-plan, and the plan keeps the dependencies it really ran with (#271)."""
    original = [
        {"id": "fetch", "agent_id": "worker", "instruction": "download data", "depends_on": []},
        {"id": "broken", "agent_id": "worker", "instruction": "break", "depends_on": ["fetch"]},
    ]
    hub = replan_hub(original,
                     lambda task: (result(task, ok=False, error="tool unavailable")
                                   if task.meta["step_id"] == "broken" else result(task, text="ok")),
                     lambda task: replan_plan([{"id": "data", "agent_id": "worker", "instruction": "rebuild",
                                                "depends_on": []}]))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    assert req["replan_history"][0]["status"] == "applied"
    assert {step["id"]: step["depends_on"] for step in req["plan"]["steps"]} == {"fetch": [], "data": []}
    assert not any(warning.startswith("step fetch:") for warning in req["plan"]["warnings"])


@pytest.mark.asyncio
async def test_resume_does_not_finish_an_in_flight_replan_over_a_reduced_cap():
    """Lowering orchestrator.max_replans before a restart is honored, as #282 does for max_steps."""
    hub = replan_hub([], lambda task: result(task, text="b done"),
                     lambda task: pytest.fail("the in-flight attempt is over the new cap"))
    hub.requests["r"].update(
        plan={"steps": [{"id": "A", "agent_id": "worker", "instruction": "a", "outputs": [], "depends_on": []}]},
        results={"A": result(Task(agent_id="worker", prompt="a"), ok=False, error="a failed").model_dump(mode="json")},
        replan_progress={"attempts": 2, "max": 2, "in_flight": True})
    hub.result_map = lambda rid: {k: TaskResult.model_validate(v) for k, v in hub.requests[rid]["results"].items()}
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert req["status"] == "failed" and not kinds(hub, "replan")
    assert req["replan_history"][-1]["status"] == "limit"
    assert req["replan_history"][-1]["reason"] == "re-plan limit reached (2/1)"
    assert req["replan_progress"]["in_flight"] is False


@pytest.mark.asyncio
async def test_synthesis_sees_which_failed_step_a_replan_replaced():
    """The CSO's final report must not present the fallback as the method that was planned (#271)."""
    hub = replan_hub([{"id": "primary", "agent_id": "worker", "instruction": "primary analysis", "depends_on": []}],
                     lambda task: (result(task, ok=False, error="primary analysis failed")
                                   if task.meta["step_id"] == "primary" else result(task, text="fallback done")),
                     lambda task: replan_plan([{"id": "fallback", "agent_id": "worker", "instruction": "fallback",
                                                "depends_on": []}]))
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("report")
    prompt = kinds(hub, "synthesis")[0].prompt
    assert "re-plan history" in prompt and "retired: primary (primary analysis failed)" in prompt


@pytest.mark.asyncio
async def test_review_replan_prompt_says_undropped_flagged_steps_are_not_revised():
    """An applied review re-plan skips in-place revision, so the CSO must know a flagged step it keeps stays
    as it is this round (#271)."""
    original = [{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]
    prompts = []

    def on_replan(task):
        prompts.append(task.prompt)
        return replan_plan([], notes="revise in place")

    def on_review(task):
        return REVISE if task.meta["revision"] == 0 else {**REVISE, "verdict": "accept", "issues": []}

    hub = replan_hub(original, lambda task: result(task, text="analysis done"), on_replan, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("report")
    assert ("If you return steps, a flagged step you do not drop keeps its result and is not revised in this "
            "round") in prompts[0]


@pytest.mark.asyncio
async def test_finish_keeps_bench_result_block_last_after_labhq_metadata():
    async def dispatch(task):
        return result(task, text="unused")

    hub = FakeHub(dispatch)
    hub.requests["r"].update(
        plan={"steps": [{"id": "A"}]}, pending_questions=["Approve follow-up?"],
        cost_known=False, cost_usd=1.25,
    )
    orch = Orchestrator(hub)
    orch.budget_outcomes["r"] = [{"spent_usd": 1.25, "limit_usd": 1.0, "approved": True}]
    block = ("<!-- LABHQ_BENCH_RESULT -->\n```json\n{\"answer\": true}\n```\n"
             "<!-- /LABHQ_BENCH_RESULT -->")
    orch._finish("r", "Narrative\n\n" + block,
                 {"A": {"status": "done", "workdir_id": "w", "outputs": ["outputs/answer.md"]}}, ok=True)
    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["report_appendix"]
    assert report.rstrip().endswith("<!-- /LABHQ_BENCH_RESULT -->")
    assert "Step status and output paths" not in report and "비용 미집계" not in report
    assert appendix.index("Step status and output paths") < appendix.index("비용 미집계")
    assert appendix.index("비용 미집계") < appendix.index("Budget: $1.25 > $1.00; approved.")


@pytest.mark.asyncio
async def test_finish_groups_status_and_warnings_under_one_execution_appendix():
    async def dispatch(task):
        return result(task, text="unused")

    hub = FakeHub(dispatch)
    hub.requests["r"].update(plan={"steps": [{"id": "A"}]})
    Orchestrator(hub)._finish(
        "r", "## 결론과 권고\n결론 본문", {
            "A": {"status": "done", "workdir_id": "w", "outputs": ["outputs/answer.md"],
                  "tool_errors": ["lookup timed out"]},
        }, ok=True)

    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["report_appendix"]
    assert report.startswith("## 결론과 권고") and "실행 기록 참고" in report
    assert "## 부록: 실행 기록" not in report
    assert "## 보고서 경고" in appendix
    assert "Step status and output paths" in appendix


def test_finish_preserves_a_long_appendix_and_marks_the_terminal_copy_as_truncated():
    async def dispatch(task):
        return result(task, text="unused")

    hub = FakeHub(dispatch)
    terminal = []
    hub.commit_terminal = lambda rid, typ, data: terminal.append((rid, typ, data))
    full = "기록" * 11_000
    Orchestrator(hub)._finish("r", "PI body\n\n## 부록: 실행 기록\n\n" + full, {}, ok=True)

    stored = hub.requests["r"]["report_appendix"]
    data = terminal[0][2]
    assert full in stored and len(stored) > 20_000
    assert len(data["report_appendix"]) <= 20_000 and data["report_appendix"] != stored
    assert data["report_appendix_truncated"] is True
    assert data["report_appendix_chars"] == len(stored)
    assert data["report_appendix_api"] == "/api/requests/r"


def test_cso_plan_prompt_states_the_outputs_rule():
    from labhq.orchestrator.cso import PLAN_PROMPT, REPLAN_PROMPT, RESEARCH_PLAN_PROMPT

    assert "outputs/<name>" in PLAN_PROMPT and "outputs/answer.md" in PLAN_PROMPT
    assert "workspace root" in PLAN_PROMPT and "absolute" in PLAN_PROMPT
    rule = "same filename"
    assert all(rule in prompt for prompt in (PLAN_PROMPT, REPLAN_PROMPT, RESEARCH_PLAN_PROMPT))


def test_legacy_max_replans_still_sets_both_caps_when_loading_config():
    from labhq.settings import OrchestratorSettings

    assert OrchestratorSettings().max_replans == 0
    assert OrchestratorSettings().max_failure_replans == 1
    assert OrchestratorSettings.model_validate({"max_replans": 2}).max_failure_replans == 2
    explicit = OrchestratorSettings.model_validate({"max_replans": 2, "max_failure_replans": 3})
    assert explicit.max_replans == 2 and explicit.max_failure_replans == 3


@pytest.mark.asyncio
async def test_failed_twelve_step_report_names_each_failure_without_dumping_instructions():
    # #331: a 12-step failure produced a 147,770-char report (full instructions and long outputs per step).
    steps = [{"id": f"S{i}", "agent_id": "worker", "instruction": f"S{i} instruction " + "x" * 4000,
              "depends_on": [f"S{i - 1}"] if i > 6 else []} for i in range(1, 13)]

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": steps})
        sid = task.meta["step_id"]
        return result(task, ok=False, text="log line\n" * 2000, error=f"{sid} broke: " + "trace " * 2000)

    hub = FakeHub(dispatch)
    await Orchestrator(hub).run_request("r")
    report = hub.requests["r"]["report_appendix"]
    assert hub.requests["r"]["status"] == "failed"
    assert len(report) < 20000, len(report)
    assert "x" * 300 not in report, "the full instruction stays in the round record"
    for i in range(1, 7):
        assert f"S{i} broke" in report
    for i in range(7, 13):
        assert f"### S{i}" in report and "S6 broke" in report
    assert report.count("Next:") == 12


def test_review_revision_reruns_every_step_downstream_of_a_flagged_one():
    """A revised step changes what its dependents read: unflagged dependents re-run with a note, so neither a later
    revision nor the report reads a bridge built on the old result (PR #337 review, mock trial 2026-10-03)."""
    from labhq.orchestrator.cso import with_downstream_revisions

    def step(sid, *deps):
        return {"id": sid, "agent_id": "worker", "instruction": sid, "depends_on": list(deps)}

    steps = [step("s2"), step("s3"), step("s4", "s3"), step("s5", "s4"), step("s6", "s5"), step("s7", "s5"),
             step("s8", "s6", "s7"), step("s9", "s8"), step("s10", "s9", "s2")]
    feedback = {"s5": "- use a paired model\n", "s9": "- drop the circular score\n"}

    extended = with_downstream_revisions(steps, feedback)

    assert set(extended) == {"s5", "s6", "s7", "s8", "s9", "s10"}
    assert extended["s5"] == feedback["s5"]
    # 12th mock trial: a flagged step below a revised one must hear about it too, after its own notes.
    assert extended["s9"].startswith(feedback["s9"]) and "Upstream step(s) s5 were revised" in extended["s9"]
    assert "Upstream step(s) s5 were revised" in extended["s6"] and "s5" in extended["s8"]
    assert "Upstream step(s) s9 were revised" in extended["s10"]
    assert with_downstream_revisions(steps, {"s10": "- fix wording\n"}) == {"s10": "- fix wording\n"}



UNRESOLVED_REVIEW = {"verdict": "revise", "scores": {"addresses_question": 4, "evidence": 3, "thoroughness": 4},
                     "issues": [{"step_id": "A", "priority": "P1", "problem": "no sensitivity check",
                                 "request": "add one"}]}
LEGACY_UNRESOLVED_REVIEW = {
    **UNRESOLVED_REVIEW,
    "issues": [{key: value for key, value in UNRESOLVED_REVIEW["issues"][0].items() if key != "priority"}],
}
STEP_A = {"id": "A", "agent_id": "worker", "instruction": "analyze", "depends_on": []}


def unresolved_hub(on_synthesis, *, accept=False, review=None):
    """A FakeHub whose reviewer asks for revision every round (or accepts with `accept`); one step A."""
    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured={"steps": [STEP_A]})
        if kind == "step":
            return result(task, text=f"A revision {task.meta.get('revision', 0)}")
        if kind == "review":
            selected = review or UNRESOLVED_REVIEW
            return result(task, structured={**selected, "verdict": "accept", "issues": []} if accept else selected)
        assert kind == "synthesis", kind
        return on_synthesis(task)

    hub = FakeHub(dispatch)
    hub.s.orchestrator.max_revisions = 1
    return hub


@pytest.mark.asyncio
async def test_p2_only_review_is_done_without_revision_and_report_keeps_the_issue():
    p2_review = {
        "verdict": "revise",
        "scores": {"addresses_question": 4, "evidence": 3, "thoroughness": 4},
        "issues": [{"step_id": "A", "priority": "P2", "problem": "evidence is thin",
                    "request": "state the limitation"}],
    }
    hub = unresolved_hub(
        lambda task: result(task, text="CSO report body\n\n## 리뷰 참고\nevidence is thin → state the limitation"),
        review=p2_review)
    hub.s.orchestrator.max_revisions = 0

    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "done" and req["review"]["verdict"] == "accept"
    assert [task.meta["revision"] for task in kinds(hub, "review")] == [0]
    assert step_ids(hub) == ["A"]
    assert "리뷰 참고" in kinds(hub, "synthesis")[0].prompt
    assert "P2 · A: evidence is thin" in req["report"]
    assert "state the limitation" not in req["report"]
    assert "P2 · A: evidence is thin → state the limitation" in req["report_appendix"]


@pytest.mark.asyncio
async def test_review_replan_and_revision_feedback_use_only_p1_issues():
    reviews = [{
        "verdict": "revise",
        "scores": {"addresses_question": 4, "evidence": 3, "thoroughness": 4},
        "issues": [
            {"step_id": "analysis", "priority": "P1", "problem": "wrong model",
             "request": "use the paired model"},
            {"step_id": "analysis", "priority": "P2", "problem": "weak wording",
             "request": "soften the claim"},
        ],
    }, {
        "verdict": "accept",
        "scores": {"addresses_question": 5, "evidence": 4, "thoroughness": 4},
        "issues": [],
    }]
    revision_prompts = []

    def on_step(task):
        if task.meta["revision"]:
            revision_prompts.append(task.prompt)
        return result(task, text="analysis done")

    def on_replan(task):
        assert "wrong model" in task.prompt and "weak wording" not in task.prompt
        return replan_plan([], notes="revise in place")

    def on_review(task):
        return reviews[task.meta["revision"]]

    hub = replan_hub([{"id": "analysis", "agent_id": "worker", "instruction": "analyze",
                       "depends_on": []}], on_step, on_replan, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done"
    assert step_ids(hub) == ["analysis", "analysis"]
    assert len(revision_prompts) == 1
    assert "wrong model" in revision_prompts[0] and "weak wording" not in revision_prompts[0]


def test_legacy_review_without_priority_is_treated_as_p1():
    from labhq.orchestrator.cso import with_p1_verdict

    assert with_p1_verdict(LEGACY_UNRESOLVED_REVIEW)["verdict"] == "revise"


@pytest.mark.asyncio
async def test_unresolved_review_still_gets_a_cso_report():
    """2nd mock trial F5: revise after the revision cap ended with a 17k-char step dump and no conclusion."""
    hub = unresolved_hub(lambda task: result(task, text="CSO report body"))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert [task.meta["revision"] for task in kinds(hub, "review")] == [0, 1]
    assert len(kinds(hub, "synthesis")) == 1
    assert req["status"] == "failed" and req["outcome"] == "review_unresolved"
    assert req["report"].startswith("CSO report body")
    # labhq appends every open issue verbatim: the CSO saw the review clipped to 3,000 characters (PR #338 review)
    assert "Review: revisions unresolved." not in req["report"]
    assert "Review: revisions unresolved. The reviewer's open issues, verbatim:\n- A: no sensitivity check → add one" \
        in req["report_appendix"]
    assert req["error"] == "리뷰 지적이 수정 상한 뒤에도 남아 있습니다"
    assert req["review"]["verdict"] == "revise"


@pytest.mark.asyncio
async def test_unresolved_review_original_is_in_the_execution_appendix():
    hub = unresolved_hub(lambda task: result(task, text="## 결론과 권고\nCSO report body"))
    await Orchestrator(hub).run_request("r")

    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["report_appendix"]
    assert report.startswith("## 결론과 권고")
    assert "## 부록: 실행 기록" not in report and "Review: revisions unresolved." not in report
    assert appendix.startswith("## 부록: 실행 기록")
    assert "Review: revisions unresolved." in appendix


@pytest.mark.asyncio
async def test_unresolved_report_lists_every_open_issue_even_past_the_prompt_clip():
    """Five long issues overflow the 3,000-character review in the synthesis prompt; the report still lists all."""
    issues = [{"step_id": "A", "priority": "P1", "problem": f"issue {i} " + "x" * 700,
               "request": f"fix {i}"} for i in range(5)]
    hub = unresolved_hub(lambda task: result(task, text="CSO report body"))
    UNRESOLVED_REVIEW["issues"], saved = issues, UNRESOLVED_REVIEW["issues"]
    try:
        await Orchestrator(hub).run_request("r")
    finally:
        UNRESOLVED_REVIEW["issues"] = saved
    appendix = hub.requests["r"]["report_appendix"]
    assert all(f"→ fix {i}" in appendix for i in range(5))
    synthesis = kinds(hub, "synthesis")[0]
    assert "fix 4" not in synthesis.prompt  # the clip the appended list makes up for


@pytest.mark.asyncio
async def test_unresolved_synthesis_prompt_lists_open_issues_and_accept_prompt_is_unchanged():
    from labhq.orchestrator.cso import SYNTH_PROMPT, UNRESOLVED_REVIEW_NOTE, replan_history_note
    from labhq.util import short

    hub = unresolved_hub(lambda task: result(task, text="report"))
    await Orchestrator(hub).run_request("r")
    prompt = kinds(hub, "synthesis")[0].prompt
    assert prompt.endswith(UNRESOLVED_REVIEW_NOTE)
    assert "separate execution record" in UNRESOLVED_REVIEW_NOTE and "verbatim" in UNRESOLVED_REVIEW_NOTE
    assert "no sensitivity check" in prompt  # the reviewer's open issue is in the prompt it lists from

    accepted = unresolved_hub(lambda task: result(task, text="report"), accept=True)
    orch = Orchestrator(accepted)
    await orch.run_request("r")
    req = accepted.requests["r"]
    assert req["status"] == "done" and "outcome" not in req
    results = {k: TaskResult.model_validate(v) for k, v in req["results"].items()}
    expected = SYNTH_PROMPT.format(
        request="question", results=orch.format_results(req["plan"]["steps"], results, orch.cfg.context_chars_per_step),
        review=short(req["review"], 3000), warnings="(none)", assumptions="(none recorded)") + replan_history_note(req)
    assert kinds(accepted, "synthesis")[0].prompt == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["engine", "budget"])
async def test_unresolved_review_falls_back_to_step_results_when_synthesis_fails(failure):
    hub = unresolved_hub(lambda task: result(task, ok=False, error="engine crashed"))
    resume = failure == "budget"
    if resume:  # restarted after the loop with the budget spent: the PI denies the synthesis budget card
        hub.requests["r"].update(
            plan={"steps": [{**STEP_A, "outputs": []}]}, cost_usd=50.0,
            results={"A": result(Task(agent_id="worker", prompt="a"), text="A revision 1").model_dump(mode="json")},
            review_progress={"phase": "unresolved", "next_revision": 2, "review": LEGACY_UNRESOLVED_REVIEW,
                             "last_completed_review": 1, "last_completed_revision": 1})
        hub.result_map = lambda rid: {k: TaskResult.model_validate(v) for k, v in hub.requests[rid]["results"].items()}
    await Orchestrator(hub).run_request("r", resume=resume)

    req = hub.requests["r"]
    assert req["status"] == "failed" and req["outcome"] == "review_unresolved"
    assert "Review: revisions unresolved." in req["report_appendix"]
    assert "### A · worker (ok)" in req["report_appendix"] and "A revision 1" in req["report_appendix"]
    if resume:
        assert not kinds(hub, "synthesis") and len(hub.approvals) == 1
        assert "Synthesis failed: budget exceeded" in req["report_appendix"]
    else:
        assert len(kinds(hub, "synthesis")) == 1 and "Synthesis failed: engine crashed" in req["report_appendix"]


@pytest.mark.asyncio
async def test_wrap_up_drops_the_first_runs_hash_of_a_file_it_rewrote(continuations):
    """A wrap-up that rewrites a saved output must not leave the first run's hash as the record: `labhq verify`
    would call the final file a mismatch (#58, PR #339 review). A file it did not touch keeps its hash."""
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            return result(task, text="saved", workdir="runs/A", outputs=["outputs/PARTIAL_STATUS.md"],
                          output_sha256={"outputs/PARTIAL_STATUS.md": "c" * 64},
                          unreported_outputs=["outputs/table.tsv"])
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns", session_id="session-1",
                      workdir="runs/A", outputs=["outputs/table.tsv", "outputs/keep.tsv"],
                      output_sha256={"outputs/table.tsv": "a" * 64, "outputs/keep.tsv": "b" * 64})

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                                meta={"kind": "step", "step_id": "A"}))
    assert res.output_sha256 == {"outputs/keep.tsv": "b" * 64, "outputs/PARTIAL_STATUS.md": "c" * 64}


@pytest.mark.asyncio
@pytest.mark.parametrize("outputs, hashes", [
    (["outputs/table.tsv", "outputs/report.md"], {"outputs/table.tsv": "a" * 64, "outputs/report.md": "c" * 64}),
    (["outputs/report.md"], {"outputs/report.md": "c" * 64}),  # the finish turn removed table.tsv
    (["outputs/table.tsv", "outputs/report.md"], {"outputs/report.md": "c" * 64}),  # grew past the hash limit
])
async def test_a_step_with_finish_turns_finishes_in_its_session_under_half_the_limit(continuations, outputs, hashes):
    """A research step that ran out of turns had already done the work (7th mock trial: QC had reproduced every
    number). It finishes in the same session, and the outputs are as the runner saw them after that turn: a removed
    or unhashable file keeps no hash from the first turn (PR #355 review)."""
    async def dispatch(task):
        if task.resume_session_id:
            return result(task, text="done", session_id="session-1", workdir="runs/A", outputs=outputs,
                          output_sha256=hashes)
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns", session_id="session-1",
                      workdir="runs/A", outputs=["outputs/table.tsv"], output_sha256={"outputs/table.tsv": "0" * 64},
                      unreported_outputs=["outputs/scratch.tsv"])

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    hub.agents["worker"]["max_turns"] = 40
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                                meta={"kind": "step", "step_id": "A", "finish_turns": 1}))
    assert res.ok and [t.meta["kind"] for t in hub.calls] == ["step", "step"]
    finish = hub.calls[1]
    assert finish.resume_session_id == "session-1" and finish.meta["parent_task"] == hub.calls[0].id
    assert finish.meta["workdir"] == "runs/A" and finish.meta["agent_overrides"] == {"max_turns": 20}
    assert continuations[0]["updates"] == FINISH_PROMPT and finish.prompt == continuations[0]["prompt"]
    assert res.outputs == outputs and res.output_sha256 == hashes
    assert res.unreported_outputs == ["outputs/scratch.tsv"]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, finish_turns, wrap_turns", [(40, 20, 4), (8, 8, 4), (3, 3, 3)])
async def test_a_finish_turn_that_runs_out_too_is_wrapped_up_without_lifting_the_limit(limit, finish_turns,
                                                                                         wrap_turns):
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            return result(task, text="saved", workdir="runs/A", outputs=["outputs/PARTIAL_STATUS.md"])
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns",
                      session_id=f"session-{len(hub.calls)}", workdir="runs/A")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    hub.agents["worker"]["max_turns"] = limit
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                                meta={"kind": "step", "step_id": "A", "finish_turns": 1}))
    assert not res.ok and res.partial_results
    assert [t.meta["kind"] for t in hub.calls] == ["step", "step", "wrap_up"]
    assert hub.calls[1].meta["agent_overrides"]["max_turns"] == finish_turns
    assert hub.calls[2].resume_session_id == "session-2"  # the finish turn's session, not the first one
    assert hub.calls[2].meta["agent_overrides"]["max_turns"] == wrap_turns


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [{"pending_jobs": ["job-1"]}, {"pending_asks": ["ask-1"]}])
async def test_a_turn_still_waiting_on_jobs_or_questions_gets_no_finish_turn(pending):
    """The runner ties a job or question to the turn that made it, so a finish turn with a new task id would never
    wait for it and could pass the step on to CP2 before the job ends (PR #355 review)."""
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            return result(task, text="saved", workdir="runs/A", outputs=["outputs/PARTIAL_STATUS.md"])
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns", session_id="session-1",
                      workdir="runs/A", **pending)

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                          meta={"kind": "step", "step_id": "A", "finish_turns": 1}))
    assert [t.meta["kind"] for t in hub.calls] == ["step", "wrap_up"]


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"kind": "step"}, {"kind": "result_correction", "finish_turns": 1}])
async def test_only_a_step_asked_to_finish_gets_a_finish_turn(meta):
    async def dispatch(task):
        if task.meta["kind"] == "wrap_up":
            return result(task, text="saved", workdir="runs/A", outputs=["outputs/PARTIAL_STATUS.md"])
        return result(task, ok=False, error="turn limit", error_kind="error_max_turns", session_id="session-1",
                      workdir="runs/A")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                          meta={**meta, "step_id": "A"}))
    assert [t.meta["kind"] for t in hub.calls] == [meta["kind"], "wrap_up"]


def test_plan_prompts_ask_for_one_environment_step():
    """2nd mock trial: steps built their own venvs and could not add a package to another step's (2026-10-03)."""
    from labhq.orchestrator.cso import ENV_STEP_RULE, PLAN_PROMPT, RESEARCH_PLAN_PROMPT

    assert "plan one environment step first" in ENV_STEP_RULE
    assert ENV_STEP_RULE in PLAN_PROMPT and ENV_STEP_RULE in RESEARCH_PLAN_PROMPT


def test_question_object_is_found_beside_a_larger_unrelated_object():
    """A large object with a raw newline, read leniently, must not hide the escaped question (PR #343 review)."""
    from labhq.orchestrator.cso import blocking_question

    text = ('Notes: {"table": "row 1\nrow 2", "rows": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]}\n'
            '{"blocking_decision": "Install networkx?\n- a) yes\n- b) no"}')
    res = TaskResult(task_id="t", agent_id="worker", ok=True, text=text)
    assert blocking_question(res) == "Install networkx?\n- a) yes\n- b) no"
    assert blocking_question(TaskResult(task_id="t", agent_id="worker", ok=True, text='{"table": "a\nb"}')) is None


def test_structured_result_is_not_overridden_by_a_sample_question_in_its_text():
    """A CLI engine can send a structured result and log text apart; a sample JSON in that text is not a question
    (PR #344 review)."""
    from labhq.orchestrator.cso import REPLAN_PROMPT, ENV_STEP_RULE, blocking_question

    res = TaskResult(task_id="t", agent_id="worker", ok=True, structured={"summary": "done"},
                     text='log: example {"blocking_decision": "sample?"}')
    assert blocking_question(res) is None
    assert ENV_STEP_RULE in REPLAN_PROMPT


@pytest.mark.asyncio
async def test_resumed_revision_gets_the_revised_upstream_results_and_must_return_a_full_result(tmp_path):
    """12th mock trial: the resumed report step saw only its own notes, kept the s8 numbers s8 had withdrawn, and
    the biologist answered with a diff ("the rest is unchanged") that then replaced its whole result."""
    from labhq.orchestrator.cso import REVISION_RESULT_RULE, with_downstream_revisions

    async def dispatch(task):
        return result(task, text=f"{task.meta['step_id']} v2 result")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True

    async def wait_session_free(agent_id, session_id, held_workdir, **kwargs):
        return session_id, held_workdir

    hub.wait_session_free = wait_session_free
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Cluster", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Report", "depends_on": ["A"]}]
    outcomes = {sid: TaskResult(task_id=f"prior-{sid}", agent_id="worker", ok=True, text=f"{sid} v1 result",
                                session_id=f"{sid}-session", workdir=str(tmp_path / sid)) for sid in "AB"}
    feedback = with_downstream_revisions(steps, {"A": "- use the patient LMM\n", "B": "- soften the summary\n"})
    await Orchestrator(hub).run_dag("r", "Original request", steps, outcomes, only={"A", "B"}, feedback=feedback)

    a, b = sorted(hub.calls, key=lambda task: task.meta["step_id"])
    assert b.resume_session_id == "B-session"
    assert "- soften the summary" in b.prompt and "Upstream step(s) A were revised" in b.prompt
    assert "Current upstream results" in b.prompt and "A v2 result" in b.prompt
    assert "A v1 result" not in b.prompt
    assert REVISION_RESULT_RULE in a.prompt and REVISION_RESULT_RULE in b.prompt
    assert "Current upstream results" not in a.prompt  # A has no upstream step


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Recommendation: use the paired model; the unpaired one inflates the DE count.\n\n# Evidence\nbody",
    "Recommendation: use the paired model.\n\n---\n# Evidence\nbody",
])
@pytest.mark.parametrize("accept", [True, False])
async def test_general_report_keeps_everything_before_its_first_heading(text, accept):
    """PR #368 review: the general report may put its answer before any heading, with or without a "---" after it, so
    labhq drops nothing; the synthesis prompt alone asks for no preamble (12th mock trial lead-in)."""
    from labhq.orchestrator.cso import SYNTH_PROMPT

    hub = unresolved_hub(lambda task: result(task, text=text), accept=accept)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["report"].startswith(text)
    assert "Start with the report's first heading: no preamble." in SYNTH_PROMPT


@pytest.mark.asyncio
async def test_resumed_revision_after_a_restart_still_gets_the_current_upstream_results(tmp_path):
    """PR #368 review: after a restart mid-round, A's finished revision is no longer pending, so the resumed B is run
    with feedback naming only B; it must still read A's current result, not the one in its old session."""
    async def dispatch(task):
        return result(task, text="B v2 result")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent_id: True

    async def wait_session_free(agent_id, session_id, held_workdir, **kwargs):
        return session_id, held_workdir

    hub.wait_session_free = wait_session_free
    steps = [{"id": "A", "agent_id": "worker", "instruction": "Cluster", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "Report", "depends_on": ["A"]}]
    outcomes = {"A": TaskResult(task_id="revised-A", agent_id="worker", ok=True, text="A v2 result"),
                "B": TaskResult(task_id="prior-B", agent_id="worker", ok=True, text="B v1 result",
                                session_id="B-session", workdir=str(tmp_path / "B"))}
    feedback = {"B": "- soften the summary\n- Upstream step(s) A were revised after the scientific review.\n"}
    await Orchestrator(hub).run_dag("r", "Original request", steps, outcomes, only={"B"}, feedback=feedback)

    (b,) = hub.calls
    assert b.resume_session_id == "B-session"
    assert "Current upstream results" in b.prompt and "A v2 result" in b.prompt
