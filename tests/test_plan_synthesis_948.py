"""#948: one missed synthesis edge, or one more model slip, must not end the whole research request.

12th run of the real study: the CSO's corrected plan still left the synthesis step unconnected to the branches, so
no CP1 card was made and $4.65 was gone. labhq now connects an existing synthesis step to every branch's last step
before validation (and says so), a branch the CSO also named "synthesis" no longer has to be read by the synthesis,
and the CSO gets up to three corrections with the problems quoted verbatim."""

from __future__ import annotations

import copy

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import PLAN_CORRECTIONS, Orchestrator
from labhq.research.contract import repair_synthesis_links, validate_research_plan
from labhq.settings import Settings
from tests.test_branched_research import branched_plan
from tests.test_research_protocol import MiniHub

LIMITS = dict(max_steps=12, max_branches=5, max_branch_steps=8, active_packs={})


def twelfth_run_shape():
    """Three branches, a synthesis lane that reads none of them, and a branch the CSO also called "synthesis"."""
    plan = branched_plan((3, 3, 2), synthesis=3)
    plan["branches"].append({"id": "synthesis", "question": "integrate", "evidence_type": "synthesis",
                             "data_boundary": "public data", "estimated_budget_usd": 5})
    plan["steps"][-3]["depends_on"] = []
    return plan


def test_the_twelfth_run_shape_is_connected_before_validation_and_says_so():
    plan = twelfth_run_shape()
    with pytest.raises(ValueError, match="no single synthesis step"):
        validate_research_plan(plan, **LIMITS)
    repaired = repair_synthesis_links(plan)
    validate_research_plan(repaired, **LIMITS)
    first = next(step for step in repaired["steps"] if step["id"] == "S1")
    assert first["depends_on"] == ["A3", "B3", "C2"]
    assert any("S1" in warning and "A3" in warning for warning in repaired["warnings"])
    assert plan["steps"][-3]["depends_on"] == []  # the CSO's own plan is not mutated


def test_existing_edges_are_kept_and_a_complete_plan_is_left_alone():
    plan = branched_plan((2, 2), synthesis=2)
    assert repair_synthesis_links(plan) is plan  # nothing missing, nothing added
    plan["steps"][-2]["depends_on"] = ["A2"]  # S1 reads only A; S2 reads S1
    repaired = repair_synthesis_links(plan)
    assert next(s for s in repaired["steps"] if s["id"] == "S1")["depends_on"] == ["A2", "B2"]
    validate_research_plan(repaired, **LIMITS)


def test_a_branch_named_synthesis_no_longer_makes_a_correct_plan_invalid():
    plan = branched_plan((2, 2), synthesis=2)
    plan["branches"].append({"id": "synthesis", "question": "integrate", "evidence_type": "synthesis",
                             "data_boundary": "public data", "estimated_budget_usd": 5})
    validate_research_plan(plan, **LIMITS)


def test_without_a_synthesis_step_nothing_is_repaired_and_the_problem_stays():
    plan = branched_plan((2, 2), synthesis=1)
    plan["steps"] = [step for step in plan["steps"] if step["branch"] != "synthesis"]
    assert repair_synthesis_links(plan) is plan
    with pytest.raises(ValueError, match="at least one synthesis step"):
        validate_research_plan(repair_synthesis_links(plan), **LIMITS)


def test_an_edge_that_would_close_a_cycle_is_not_added():
    plan = branched_plan((2, 2), synthesis=1)
    plan["steps"][-1]["depends_on"] = ["A2"]
    next(s for s in plan["steps"] if s["id"] == "B1")["depends_on"] = ["S1"]  # B already waits for the synthesis
    assert repair_synthesis_links(plan) is plan


async def _run(plans):
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    queue = list(plans)

    async def reply(task):
        assert task.meta["kind"] == "plan"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(queue.pop(0)))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare branches")
    await Orchestrator(hub).run_request("r")
    return hub


def ghost(plan):
    plan["steps"][0]["agent_id"] = "ghost"  # not on the roster: a problem the CSO has to correct
    return plan


@pytest.mark.asyncio
async def test_the_cso_gets_three_corrections_and_the_second_one_can_pass():
    hub = await _run([ghost(branched_plan((2, 2))), ghost(branched_plan((2, 2))), branched_plan((2, 2))])
    assert [task.meta["kind"] for task in hub.calls] == ["plan"] * 3
    request = hub.requests["r"]
    assert request["outcome"] == "plan_approved" and request["plan_validation"]["attempts"] == 3
    assert "ghost" in hub.calls[1].prompt and "ghost" in hub.calls[2].prompt  # each round quotes the problem verbatim


@pytest.mark.asyncio
async def test_after_the_last_correction_the_report_names_the_real_number():
    assert PLAN_CORRECTIONS == 3
    hub = await _run([ghost(branched_plan((2, 2))) for _ in range(PLAN_CORRECTIONS + 1)])
    assert len(hub.calls) == PLAN_CORRECTIONS + 1 and not hub.approvals
    request = hub.requests["r"]
    assert request["outcome"] == "plan_invalid" and request["plan_validation"]["attempts"] == PLAN_CORRECTIONS + 1
    assert f"CSO가 {PLAN_CORRECTIONS}번 고친" in request["report"] and "한 번" not in request["report"]


@pytest.mark.asyncio
async def test_a_missed_synthesis_edge_no_longer_costs_a_correction_round():
    plan = twelfth_run_shape()
    hub = await _run([plan])
    assert [task.meta["kind"] for task in hub.calls] == ["plan"]
    request = hub.requests["r"]
    assert request["outcome"] == "plan_approved"
    assert any("labhq가 synthesis" in warning for warning in request["plan"]["warnings"])
