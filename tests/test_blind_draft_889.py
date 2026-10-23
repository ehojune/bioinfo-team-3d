"""Blind reviewer draft: sealed after CP1 and before the first step, compared at the research review (#889)."""

from __future__ import annotations

import hashlib

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator, lenient_review
from labhq.research import blind_draft
from tests import test_research_continue as cont

DRAFT = "Independent draft: the effect may come from donor mix; alternative: batch effect. UNVERIFIED: cohort size."
COMPARISON = {"missed_alternatives": ["batch effect"], "differing_conclusions": [], "evidence_gaps": ["cohort size"]}


def _hub(tmp_path, *, on=True, draft_ok=True, reviews=None):
    hub = cont._hub(tmp_path, [cont.APPROVE, cont.APPROVE], reviews=reviews or [cont.ACCEPT])
    hub.s.research.blind_draft = on
    reply = hub.reply

    async def with_draft(task):
        if task.meta["kind"] == "blind_draft":
            if not draft_ok:
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="draft crashed")
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=DRAFT)
        return await reply(task)

    hub.reply = with_draft
    return hub


def _kinds(hub):
    return [task.meta["kind"] for task in hub.calls]


@pytest.mark.asyncio
async def test_draft_is_sealed_before_the_first_step_and_sees_only_the_request(tmp_path):
    hub = _hub(tmp_path)
    await Orchestrator(hub).run_request("r")

    kinds = _kinds(hub)
    assert kinds.index("blind_draft") < kinds.index("step")
    draft_task = hub.calls[kinds.index("blind_draft")]
    plan = hub.requests["r"]["plan"]
    assert draft_task.agent_id == "sci_reviewer"
    assert "compare conditions" in draft_task.prompt
    assert "must not look for" in draft_task.prompt
    for step in plan["steps"]:
        assert step["instruction"] not in draft_task.prompt  # no team plan detail
    record = hub.requests["r"]["research_contract"]["blind_draft"]
    assert record["status"] == "sealed" and record["text"] == DRAFT
    assert record["sha256"] == hashlib.sha256(DRAFT.encode()).hexdigest()
    assert blind_draft.sealed_text(record) == DRAFT


@pytest.mark.asyncio
async def test_review_gets_the_sealed_draft_and_keeps_the_comparison(tmp_path):
    hub = _hub(tmp_path, reviews=[{**cont.ACCEPT, "blind_comparison": COMPARISON}])
    await Orchestrator(hub).run_request("r")

    review_task = next(task for task in hub.calls if task.meta["kind"] == "review")
    record = hub.requests["r"]["research_contract"]["blind_draft"]
    assert DRAFT in review_task.prompt and record["sha256"] in review_task.prompt
    assert "is not evidence that a claim is true" in review_task.prompt
    assert hub.requests["r"]["research_contract"]["review"]["blind_comparison"] == COMPARISON
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_off_by_default_changes_nothing(tmp_path):
    hub = _hub(tmp_path, on=False)
    await Orchestrator(hub).run_request("r")

    assert "blind_draft" not in _kinds(hub)
    assert "blind_draft" not in hub.requests["r"]["research_contract"]
    review_task = next(task for task in hub.calls if task.meta["kind"] == "review")
    assert "Blind draft" not in review_task.prompt
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_a_failed_draft_is_recorded_and_never_stops_the_request(tmp_path):
    hub = _hub(tmp_path, draft_ok=False)
    await Orchestrator(hub).run_request("r")

    record = hub.requests["r"]["research_contract"]["blind_draft"]
    assert record["status"] == "unavailable" and "draft crashed" in record["reason"]
    review_task = next(task for task in hub.calls if task.meta["kind"] == "review")
    assert "Blind draft" not in review_task.prompt
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_no_draft_after_a_team_result_exists_and_a_settled_record_is_kept(tmp_path):
    hub = _hub(tmp_path)
    orch = Orchestrator(hub)
    req = hub.requests["r"]
    req["research_contract"] = {"plan_sha256": "a" * 64}
    req["results"] = {"s1": {"ok": True}}
    assert await orch._blind_draft("r", req, "question", {}) is True
    assert req["research_contract"]["blind_draft"] == {**req["research_contract"]["blind_draft"],
                                                      "status": "unavailable", "reason": "results_exist"}
    assert hub.calls == []

    sealed = blind_draft.seal(DRAFT, results_exist=False)
    req["results"] = {}
    req["research_contract"]["blind_draft"] = sealed
    assert await orch._blind_draft("r", req, "question", {}) is True  # a restart reuses it
    assert req["research_contract"]["blind_draft"] is sealed and hub.calls == []


def test_a_late_or_edited_draft_is_not_used_by_the_review():
    late = blind_draft.seal(DRAFT, results_exist=True)
    assert late["status"] == "late" and blind_draft.review_block(late) == ""
    sealed = blind_draft.seal(DRAFT, results_exist=False)
    assert blind_draft.review_block(sealed) != ""
    assert blind_draft.sealed_text({**sealed, "text": DRAFT + " edited"}) is None
    assert blind_draft.seal("   ", results_exist=False)["status"] == "unavailable"


def test_an_unreadable_blind_comparison_costs_only_itself():
    schema = cso.RESEARCH_LANE_REVIEW_SCHEMA
    good = {**cont.ACCEPT, "blind_comparison": COMPARISON}
    assert lenient_review(good, schema) == good
    broken = {**cont.ACCEPT, "blind_comparison": {"missed_alternatives": "not a list"}}
    assert lenient_review(broken, schema) == cont.ACCEPT
    both = {**cont.ACCEPT, "checklist": "bad", "blind_comparison": "bad"}
    assert lenient_review(both, schema) == cont.ACCEPT


@pytest.mark.asyncio
async def test_a_refused_reservation_ends_the_request_as_a_budget_denial_not_an_internal_error(tmp_path):
    hub = _hub(tmp_path)
    orch = Orchestrator(hub)
    real = orch.run_step

    async def refuse(task, *args, **kwargs):
        if task.meta["kind"] == "blind_draft":
            raise cso.BudgetExceeded("the draft's turn cap does not fit the remaining request budget")
        return await real(task, *args, **kwargs)

    orch.run_step = refuse
    await orch.run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_failed"
    failure = req["research_contract"]["failure"]
    assert failure["stage"] == "blind_draft" and "does not fit" in failure["budget"]
    assert "step" not in _kinds(hub)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["off", "failed"])
async def test_a_comparison_of_a_draft_the_reviewer_never_saw_is_not_recorded(tmp_path, scenario):
    hub = _hub(tmp_path, on=scenario != "off", draft_ok=scenario != "failed",
               reviews=[{**cont.ACCEPT, "blind_comparison": COMPARISON}])
    await Orchestrator(hub).run_request("r")

    assert "blind_comparison" not in hub.requests["r"]["research_contract"]["review"]
    assert hub.requests["r"]["outcome"] == "research_reported"
