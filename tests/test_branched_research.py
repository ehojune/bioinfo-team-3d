"""Branched research plans: dynamic limits, failure isolation, and report rules (#686, #694)."""

import copy

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import validate_research_plan
from labhq.settings import Settings
from tests.test_research_protocol import MiniHub, valid_plan


def branched_plan(counts=(7, 7, 7, 7), synthesis=2):
    plan = valid_plan(topics=[])
    plan["branches"] = [
        {"id": chr(65 + index), "question": f"question {index + 1}",
         "evidence_type": kind, "data_boundary": "public data",
         "estimated_budget_usd": 10 + index}
        for index, kind in enumerate(("literature", "public omics", "domestic statistics", "overseas statistics",
                                      "clinical registry"))
    ][:len(counts)]
    steps = []
    terminals = []
    for index, count in enumerate(counts):
        branch = chr(65 + index)
        previous = None
        for number in range(1, count + 1):
            sid = f"{branch}{number}"
            steps.append({"id": sid, "agent_id": "worker", "instruction": f"work {sid}",
                          "phase": "analysis", "branch": branch, "claim_ids": ["c1"],
                          "input_refs": ["public-input"], "outputs": [f"outputs/{sid}.tsv"],
                          "checks": ["result QC"],
                          "evidence_slots": [{"id": "e1", "required": True,
                                              "description": "direct result"}],
                          "depends_on": [previous] if previous else []})
            previous = sid
        terminals.append(previous)
    previous = None
    for number in range(1, synthesis + 1):
        sid = f"S{number}"
        steps.append({"id": sid, "agent_id": "worker", "instruction": "integrate every branch",
                      "phase": "report", "branch": "synthesis", "claim_ids": ["c1"],
                      "input_refs": terminals, "outputs": [f"outputs/{sid}.md"],
                      "checks": ["failed branches named"],
                      "evidence_slots": [{"id": "e1", "required": True,
                                          "description": "integrated result"}],
                      "depends_on": terminals if previous is None else [previous]})
        previous = sid
    plan["steps"] = steps
    plan["checklist"] = {}
    return plan


def result_for(hub, task):
    sid = task.meta["step_id"]
    step = next(step for step in hub.requests["r"]["plan"]["steps"] if step["id"] == sid)
    path = step["outputs"][0]
    return {
        "schema_version": 2, "plan_sha256": hub.requests["r"]["research_contract"]["plan_sha256"],
        "step_id": sid,
        "claims": [{"id": "c1", "revision": 1, "statement": f"result from {sid}",
                    "scope": "frozen branch", "kind": "finding", "status": "supported",
                    "importance": "major", "status_reason": "planned analysis", "limitations": []}],
        "evidence": [{"id": "e1", "kind": "experimental", "observation": f"result from {sid}",
                      "status": "observed", "source": {"artifact_id": "a1", "locator": "row 1"},
                      "directness": "direct", "source_level": "primary",
                      "independence_group": sid, "assessment_reason": "planned branch analysis",
                      "slots": ["e1"]}],
        "links": [{"claim_id": "c1", "claim_revision": 1, "evidence_id": "e1",
                   "relation": "supports", "rationale": "planned result"}],
        "artifact_refs": [{"artifact_id": "a1", "path": path}],
        "not_established": [], "failures": [], "method_changes": [],
    }


def test_four_branch_thirty_step_plan_uses_branch_limits_not_legacy_twelve():
    plan = branched_plan()
    parsed = validate_research_plan(plan, max_steps=12, max_branches=5, max_branch_steps=8,
                                    active_packs={})
    assert len(parsed.steps) == 30

    unbranched = valid_plan(topics=[], steps=13)
    with pytest.raises(ValueError, match="maximum is 12"):
        validate_research_plan(unbranched, max_steps=12, max_branches=5, max_branch_steps=8,
                               active_packs={})


def test_four_branch_plan_rejects_synthesis_that_only_depends_on_branch_a():
    plan = branched_plan((1, 1, 1, 1), synthesis=1)
    plan["steps"][-1]["depends_on"] = ["A1"]

    with pytest.raises(ValueError, match=r"branches.*B.*C.*D"):
        validate_research_plan(plan, max_steps=12, max_branches=5, max_branch_steps=8,
                               active_packs={})


def test_split_synthesis_steps_that_each_read_one_branch_are_rejected():
    """S1 <- A and S2 <- B covered every branch only as a union; no step integrated them (PR #703 review)."""
    plan = branched_plan((1, 1), synthesis=2)
    plan["steps"][-2]["depends_on"] = ["A1"]
    plan["steps"][-1]["depends_on"] = ["B1"]
    with pytest.raises(ValueError, match=r"no single synthesis step.*missing branches: B"):
        validate_research_plan(plan, max_steps=12, max_branches=5, max_branch_steps=8, active_packs={})
    plan["steps"][-1]["depends_on"] = ["B1", "S1"]  # S2 now reads B and, through S1, A
    validate_research_plan(plan, max_steps=12, max_branches=5, max_branch_steps=8, active_packs={})


@pytest.mark.asyncio
async def test_mock_cso_four_branch_thirty_step_plan_is_frozen_whole():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=copy.deepcopy(branched_plan()))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="plan literature, public omics, domestic statistics, and overseas statistics")
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan"]
    assert len(hub.requests["r"]["plan"]["steps"]) == 30
    assert hub.requests["r"]["outcome"] == "plan_approved"


@pytest.mark.asyncio
async def test_nine_step_branch_is_replanned_without_silent_truncation():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    replies = [branched_plan((9,), synthesis=1), branched_plan((8,), synthesis=1)]

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=copy.deepcopy(replies.pop(0)))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="plan literature and public omics branches")
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"]
    assert "branch A has 9 steps; maximum is 8" in hub.calls[1].prompt
    assert len(hub.requests["r"]["plan"]["steps"]) == 9


@pytest.mark.asyncio
async def test_failed_branch_blocks_only_it_and_synthesis_gets_failure_memo_without_artifacts():
    plan = branched_plan((1, 2, 1, 1), synthesis=1)
    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=copy.deepcopy(plan))
        if task.meta["step_id"] == "B1":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="source refused",
                              text="PARTIAL-BRANCH-BODY", workdir="failed-b1-workdir",
                              workdir_id="wd-failed-b1", outputs=["outputs/B1.partial.tsv"])
        result = result_for(hub, task)
        path = next(step["outputs"][0] for step in hub.requests["r"]["plan"]["steps"]
                    if step["id"] == task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path], output_sha256={path: "a" * 64})

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                     text="compare literature and public omics")
    # This test is about the failed branch, not the budget: room for every parallel branch's hold (#1139).
    hub.requests["r"]["budget_usd"] = 100
    decisions = [{"approved": True, "note": "approved"},
                 {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    dispatched = [task.meta.get("step_id") for task in hub.calls if task.meta["kind"] == "step"]
    assert "A1" in dispatched and "B1" in dispatched and "B2" not in dispatched and "S1" in dispatched
    synthesis = next(task for task in hub.calls if task.meta.get("step_id") == "S1")
    assert "실패한 갈래: B" in synthesis.context and "source refused" in synthesis.context
    assert "마지막으로 끝난 단계: none" in synthesis.context
    assert "PARTIAL-BRANCH-BODY" not in synthesis.context
    assert "B1.partial.tsv" not in synthesis.context
    assert "failed-b1-workdir" not in synthesis.meta["upstream_dirs"]
    assert "B1" not in synthesis.meta["upstream_steps"]
    assert len(hub.approvals) == 2, {key: hub.requests["r"].get(key) for key in
                                     ("outcome", "error", "report", "execution_record", "results")}
    card = hub.approvals[1]["detail"]
    assert card["step_branches"] == {"A1": "A", "C1": "C", "D1": "D", "S1": "synthesis"}
    assert any("B1" in warning for warning in card["warnings"])


@pytest.mark.asyncio
async def test_four_branch_plan_with_no_successful_terminal_stops_before_cp2():
    plan = branched_plan((2, 1, 1, 1), synthesis=1)
    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=copy.deepcopy(plan))
        if task.meta["step_id"] in {"A2", "B1", "C1", "D1"}:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error=f"terminal failed: {task.meta['step_id']}")
        result = result_for(hub, task)
        path = next(step["outputs"][0] for step in hub.requests["r"]["plan"]["steps"]
                    if step["id"] == task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path], output_sha256={path: "a" * 64})

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                     text="compare four independent evidence branches")
    hub.requests["r"]["budget_usd"] = 100  # room for the four parallel branches' holds (#1139)

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        if kwargs["kind"] == "research_plan":
            return {"approved": True, "note": "approved", "approval_id": "a1", "decided_at": 1.0}
        return {"approved": False, "choice": "deny", "note": "stop", "approval_id": "a2", "decided_at": 2.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [approval["kind"] for approval in hub.approvals] == ["research_plan"]
    assert hub.requests["r"]["outcome"] == "research_failed"
    assert "갈래별 terminal 상태" in hub.requests["r"]["report"]
    for branch, terminal in (("A", "A2"), ("B", "B1"), ("C", "C1"), ("D", "D1")):
        assert branch in hub.requests["r"]["report"] and terminal in hub.requests["r"]["report"]


def test_forty_three_step_digest_keeps_every_step_branch_mapping():
    plan = branched_plan((8, 8, 8, 8, 8), synthesis=3)
    for step in plan["steps"]:
        step["instruction"] = f"{step['id']} " + ("long instruction " * 80)

    digest = cso._research_plan_digest(plan)

    assert len(plan["steps"]) == 43
    for step in plan["steps"]:
        assert f'"id": "{step["id"]}", "branch": "{step["branch"]}"' in digest


def test_prompts_cover_branch_reporting_and_hypothesis_aligned_reference():
    assert "A non-significant result is still a result" in cso.RESEARCH_SYNTH_PROMPT
    assert "what was\n  done and found" in cso.RESEARCH_SYNTH_PROMPT
    assert "representative contrast matches the hypothesis" in cso.RESEARCH_SYNTH_PROMPT
    assert "analysis-result table" in cso.RESEARCH_REVIEW_PROMPT
    assert "same exposure side" in cso.RESEARCH_REVIEW_PROMPT
    assert "that group versus all others" in cso.RESEARCH_PLAN_PROMPT
