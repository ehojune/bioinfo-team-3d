"""Synthetic request regressions for model-free partial closeout (#1530)."""

import asyncio
import copy

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from tests.test_research_cp2 import _settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan
from tests.test_research_report import _hub as research_report_hub

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
