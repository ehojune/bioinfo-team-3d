"""Synthetic request regressions for model-free partial closeout (#1530)."""

import asyncio
import copy
import json

import pytest

from labhq.gateway.server import Hub
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from tests.test_cso import FakeHub, replan_hub, replan_plan, result
from tests.test_research_cp2 import _settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan
from tests.test_research_report import _hub as research_report_hub
from tests.test_solo_route import SoloHub

pytestmark = pytest.mark.usefixtures("pre_v1_defaults")
DIGEST = "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "sent", "cards", "step_kind"), [
    ("budget_denied", ["s1"], ["budget"], "budget_denied"),
    ("budget_cap", ["s1"], [], "budget_cap"),
    ("error_max_turns", ["s1", "s2"], [], "error_max_turns"),
])
async def test_limit_finishes_research_without_dispatching_report(limit, sent, cards, step_kind):
    settings = _settings()
    settings.policy.budget.reserve_before_dispatch = True
    settings.policy.budget.per_task_usd = 10
    plan = valid_plan(steps=3)
    plan["steps"][1].update(agent_id="second", depends_on=["s1"])
    plan["steps"][2]["depends_on"] = ["s2"]

    async def reply(task):
        common = {"task_id": task.id, "agent_id": task.agent_id}
        if task.meta["kind"] == "plan":
            return TaskResult(**common, ok=True, structured=plan, cost_usd=0)
        if task.meta.get("step_id") == "s1":
            path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
            return TaskResult(**common, ok=True, structured=_cp2_result(hub, task), workdir_id="synthetic-run",
                              outputs=[path], output_sha256={path: DIGEST},
                              cost_usd=9.999 if limit == "budget_denied" else 0.1)
        if task.meta.get("step_id") == "s2" and limit == "error_max_turns":
            return TaskResult(**common, ok=False, error="synthetic turn exhaustion", error_kind=limit, cost_usd=0)
        pytest.fail(f"unexpected dispatch: {task.meta}")

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare synthetic conditions")
    hub.agents["second"] = {**hub.agents["worker"], "id": "second"}
    if limit == "budget_cap":
        hub.agents["second"]["max_budget_usd"] = 0.004
    original_approval = hub.request_approval

    async def approval(**kwargs):
        decision = await original_approval(**kwargs)
        return {**decision, "approved": False} if kwargs["kind"] == "budget" else decision

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    saved = hub.requests["r"]
    assert saved["status"] == "failed"
    assert saved["outcome"] == "research_failed"
    assert saved["partial_closeout"] == limit
    assert saved["report_package"]["kind"] == "failure"
    assert saved["report_contract_check"]["status"] == "PASS"
    assert "synthetic-run/outputs/result1.tsv" in saved["report"]
    assert DIGEST in saved["report"]
    assert "outputs/result2.tsv" in saved["report"] and "outputs/result3.tsv" in saved["report"]
    assert saved["results"]["s1"]["output_sha256"] == {"outputs/result1.tsv": DIGEST}
    assert saved["results"]["s2"]["error_kind"] == step_kind
    assert not saved["results"]["s3"]["ok"]
    assert [task.meta.get("step_id") for task in hub.calls if task.meta["kind"] == "step"] == sent
    assert [card["kind"] for card in hub.approvals if card["kind"] != "research_plan"] == cards
    assert {task.meta["kind"] for task in hub.calls} == {"plan", "step"}

    await asyncio.sleep(0)  # MiniHub publishes terminal events on the next loop tick.
    before = copy.deepcopy(saved)
    await Orchestrator(hub).run_request("r", resume=True)
    assert saved == before
    terminal = [event for event in hub.events if event["type"] == "request.completed"]
    assert len(terminal) == 1
    assert terminal[0]["data"]["ok"] is False
    assert terminal[0]["data"]["report"] == saved["report"]


@pytest.mark.asyncio
async def test_concurrent_closeout_commits_one_terminal_report():
    async def unexpected_dispatch(task):
        pytest.fail(f"unexpected dispatch: {task.meta}")

    hub = MiniHub(_settings(), unexpected_dispatch, mode="orchestrate", work_kind="research")
    hub.requests["r"]["status"] = "running"
    orchestrator = Orchestrator(hub)
    commits = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def schedule(rid, kind, data, *, request, before):
        commits.append((rid, kind, data))
        entered.set()
        await release.wait()
        hub.requests[rid] = request

    hub.aschedule_terminal = schedule
    results = {"s1": {"ok": True, "outputs": ["outputs/a.txt"]}}
    first = asyncio.create_task(orchestrator._finish("r", "", results, False, limit_error="budget_cap"))
    await entered.wait()
    await orchestrator._finish("r", "", results, False, limit_error="budget_cap")
    release.set()
    await first
    await Orchestrator(hub)._finish("r", "", results, False, limit_error="budget_cap")
    assert len(commits) == 1
    assert hub.requests["r"]["status"] == "failed"
    assert hub.requests["r"]["partial_closeout"] == "budget_cap"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["review", "synthesis"])
async def test_report_phase_turn_exhaustion_keeps_completed_steps(stage):
    hub = research_report_hub()
    original_reply = hub.reply

    async def reply(task):
        if task.meta["kind"] == stage:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error_kind="error_max_turns", error="synthetic report-stage failure")
        return await original_reply(task)

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["outcome"] == "research_failed"
    assert saved["partial_closeout"] == "error_max_turns"
    assert "outputs/result1.tsv" in saved["report"] and "synthetic report-stage failure" in saved["execution_record"]
    assert [task.meta["kind"] for task in hub.calls] == (
        ["plan", "step", "review"] + (["synthesis"] if stage == "synthesis" else []))


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_before_cp2", [False, True])
@pytest.mark.parametrize(("error_kind", "limit"), [
    ("error_max_turns", "error_max_turns"),
    ("error_max_budget_usd", "budget_cap"),
])
async def test_compact_draft_limit_is_preserved(tmp_path, audit_before_cp2, error_kind, limit):
    hub = research_report_hub()
    hub.requests["r"]["research_options"] = {"compact_workflow": True, "audit_before_cp2": audit_before_cp2}
    hub.supports_resume = lambda agent_id: agent_id == "worker"
    plan = valid_plan(steps=2)
    plan["steps"][1].update(phase="validation", depends_on=["s1"])

    async def reply(task):
        common = {"task_id": task.id, "agent_id": task.agent_id}
        if task.meta["kind"] == "plan":
            return TaskResult(**common, ok=True, structured=plan)
        if task.meta["kind"] == "analysis_draft":
            assert task.resume_session_id == "s1"
            return TaskResult(**common, ok=False, error="synthetic draft limit", error_kind=error_kind)
        assert task.meta["kind"] == "step"
        sid = task.meta["step_id"]
        [path] = task.meta["required_outputs"]
        ledger = _cp2_result(hub, task)
        if sid == "s2":
            ledger["claims"][0]["id"] = "c2"
            ledger["evidence"][0].update(id="e2", slots=["e2"])
            ledger["links"][0].update(claim_id="c2", evidence_id="e2")
        ledger["artifact_refs"][0]["path"] = path
        return TaskResult(**common, ok=True, structured=ledger, workdir=str(tmp_path / sid), workdir_id=sid,
                          outputs=[path], output_sha256={path: DIGEST}, session_id=sid)

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["outcome"] == "research_failed"
    assert saved["partial_closeout"] == limit
    assert saved["error"] == "synthetic draft limit"
    assert "s1/outputs/result1.tsv" in saved["report"] and "s2/outputs/result2.tsv" in saved["report"]
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "step", "analysis_draft"]



@pytest.mark.asyncio
async def test_generic_review_replan_turn_limit_preserves_earlier_outputs():
    plan = [
        {"id": "evidence", "agent_id": "worker", "instruction": "collect evidence",
         "outputs": ["outputs/evidence.tsv"], "depends_on": []},
        {"id": "analysis", "agent_id": "worker", "instruction": "analyze evidence", "depends_on": ["evidence"]},
    ]

    def on_step(task):
        sid = task.meta["step_id"]
        if sid == "replacement":
            return result(task, ok=False, error="synthetic replan turn limit", error_kind="error_max_turns")
        return result(task, text=f"{sid} done", workdir_id=sid, outputs=task.meta["outputs"])

    def on_review(task):
        return {"verdict": "revise", "scores": {"addresses_question": 3, "evidence": 2, "thoroughness": 3},
                "issues": [{"step_id": "analysis", "priority": "P1", "problem": "wrong model",
                            "request": "use a mixed model"}]}

    def on_replan(task):
        return replan_plan([
            {"id": "replacement", "agent_id": "worker", "instruction": "mixed model",
             "outputs": ["outputs/replacement.tsv"], "depends_on": ["evidence"]},
        ], drop=["analysis"])

    hub = replan_hub(plan, on_step, on_replan, max_failure_replans=2, on_review=on_review)
    await Orchestrator(hub).run_request("r")
    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["partial_closeout"] == "error_max_turns"
    assert "evidence/outputs/evidence.tsv" in saved["report"] and "outputs/replacement.tsv" in saved["report"]
    assert saved["results"]["replacement"]["error_kind"] == "error_max_turns"
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "step", "review", "replan", "step"]



@pytest.mark.asyncio
async def test_finished_team_report_keeps_budget_denial_reason():
    plan = [{"id": "evidence", "agent_id": "worker", "instruction": "collect evidence",
             "outputs": ["outputs/evidence.tsv"], "depends_on": []}]
    hub = replan_hub(
        plan, lambda task: result(task, text="evidence ready", outputs=task.meta["outputs"]),
        lambda task: pytest.fail("unexpected replan"),
    )
    orchestrator = Orchestrator(hub)
    dispatch = hub.dispatch
    denial = "budget exceeded ($1.02 > $1.00); approval denied"

    async def dispatch_with_denial(task):
        reply = await dispatch(task)
        if task.meta["kind"] == "synthesis":
            orchestrator.budget_denials["r"] = denial
        return reply

    hub.dispatch = dispatch_with_denial
    await orchestrator.run_request("r")
    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["error"] == denial
    assert saved["report"] == "final" and "partial_closeout" not in saved
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "synthesis"]


@pytest.mark.asyncio
async def test_finished_direct_answer_publishes_budget_denial_reason():
    async def dispatch(task):
        return result(task, text="direct answer", cost_usd=1.02)

    hub = FakeHub(dispatch)
    hub.requests["r"].update(mode="direct", agent_id="worker", budget_usd=1.0)
    await Orchestrator(hub).run_request("r")
    await asyncio.sleep(0)  # FakeHub publishes terminal events on the next loop tick.

    denial = "budget exceeded ($1.02 > $1.00); approval denied"
    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["error"] == denial
    assert saved["report"] == "direct answer" and "partial_closeout" not in saved
    assert [task.meta["kind"] for task in hub.calls] == ["direct"]
    assert [card["kind"] for card in hub.approvals] == ["budget"]
    [terminal] = [event["data"] for event in hub.events if event["type"] == "request.completed"]
    assert terminal["ok"] is False and terminal["error"] == denial


@pytest.mark.asyncio
@pytest.mark.parametrize(("error_kind", "limit", "reason"), [
    ("error_max_turns", "error_max_turns", "synthetic turn limit after a timeout"),
    ("error_max_budget_usd", "budget_cap", "synthetic budget cap"),
    (None, "budget_denied", "budget exceeded ($1.10 > $1.00); approval denied"),
    ("before_dispatch", "budget_cap", "호출 상한이 1센트보다 작아 보내지 않았습니다(one cent: $0.0000); nothing was dispatched"),
])
async def test_solo_review_limit_closes_from_saved_outputs_and_survives_restart(error_kind, limit, reason):
    hub = SoloHub()
    hub.s.orchestrator.solo_review = True
    hub.s.orchestrator.reviewer_agent = "reviewer"
    hub.agents["reviewer"] = {**hub.agents["solo"], "id": "reviewer"}
    hub.supports_resume = lambda agent_id: agent_id == "reviewer"
    hub.requests["r"]["budget_usd"] = 1.0
    expected_dispatches = ["plan", "direct", "review"]
    if error_kind == "before_dispatch":
        hub.s.policy.budget.reserve_before_dispatch = True
        hub.agents["reviewer"]["max_budget_usd"] = 0.004
        expected_dispatches.pop()
    artifact = "outputs/evidence.tsv"
    hub.solo = hub.solo.model_copy(update={"outputs": [artifact], "output_sha256": {artifact: DIGEST}})
    dispatch = hub.dispatch
    checkpoint = None

    async def reply(task):
        if task.meta["kind"] not in {"plan", "direct", "review"}:
            pytest.fail(f"unexpected dispatch: {task.meta['kind']}")
        response = await dispatch(task)
        if task.meta["kind"] != "review":
            return response
        if error_kind is None:
            return response.model_copy(update={"cost_usd": 0.7, "cost_known": True})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error_kind=error_kind,
                          error=reason, session_id="review-session", cost_usd=0)

    async def reject_budget(**kwargs):
        assert kwargs["kind"] == "budget"
        return {"approved": False}

    async def save_request(rid):
        nonlocal checkpoint
        saved = hub.requests[rid]
        if checkpoint is None and saved.get("solo_review") and saved["status"] == "running":
            checkpoint = json.dumps(saved)

    hub.dispatch = reply
    hub.request_approval = reject_budget
    hub.asave_request = save_request
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == expected_dispatches
    saved = hub.requests["r"]
    assert saved["status"] == "failed" and saved["partial_closeout"] == limit
    assert saved["error"] == reason and saved["review"]["status"] == limit
    assert f"task_solo/{artifact}" in saved["report"] and DIGEST in saved["report"]
    assert "outputs/team.tsv" in saved["report"]
    assert saved["results"]["direct"]["output_sha256"] == {artifact: DIGEST}
    assert saved["route_decision"]["mode"] == "solo"
    assert len(hub.terminal) == 1 and hub.terminal[0][2]["ok"] is False

    assert checkpoint is not None
    restarted = SoloHub()
    restarted.requests["r"] = json.loads(checkpoint)
    gateway = object.__new__(Hub)
    gateway.requests = restarted.requests
    gateway.s = restarted.s  # The review limit remains terminal even when solo review is now disabled.
    assert gateway.resume_agents("r") == set()
    await Orchestrator(restarted).run_request("r", resume=True)
    recovered = restarted.requests["r"]
    for field in ("status", "partial_closeout", "error", "review", "report", "results", "route_decision"):
        assert recovered[field] == saved[field]

    completed = copy.deepcopy(recovered)
    await Orchestrator(restarted).run_request("r", resume=True)
    assert restarted.requests["r"] == completed
    assert restarted.calls == []
    assert len(restarted.terminal) == 1 and restarted.terminal[0][2]["report"] == saved["report"]
