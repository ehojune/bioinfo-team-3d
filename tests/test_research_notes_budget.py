"""Research lane card notes reach the prompts that act on them (PI 점검 R11), and the CP1 plan's numeric budget is
the request's enforced spending cap (R17).

req_7ccde78be0 (2026-10-08 trial): the six-item CP2 note was in none of the 24 task prompts, and the plan's
"USD 5 이내" ran to $18.28 without a budget card because the only enforced cap was policy per_request_usd ($30)."""

import copy

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.research import contract as rc
from tests import test_research_continue as cont
from tests import test_research_report as rep
from tests.test_research_cp2 import CP1, _interrupt, _research_hub, _settings
from tests.test_research_protocol import valid_plan

CP1_NOTE = "총괄 CP1 승인: 설치 없음(기존 numpy/scipy), 독립 검증 GSE32863, 양성 대조 SEMA5A."
CP2_NOTE = "승인. 보고서에 반드시 남길 것: (1) GSE19804는 histology 메타데이터가 없다. (2) CAMERA는 0개다."
CONTINUE_NOTE = "이어 가되 s2 효과 열은 donor 단위로만 계산할 것."
# valid_plan() before R17: a plan frozen without budget_usd keeps this hash.
VALID_PLAN_SHA = "6b454f6784e1e68d0dc14b0a78b82cb87eafa60327834f79e94d71298f2d74a4"


def _kinds(hub, kind):
    return [task for task in hub.calls if task.meta["kind"] == kind]


def _answer(hub, decisions, *, budget=None):
    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        if kwargs["kind"] == "budget":
            return {"approved": budget is not None and budget, "note": "", "approval_id": "budget",
                    "decided_at": 1.0}
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval


def _with_plan(hub, **extra):
    """The CSO's plan gains top-level fields (budget_usd) on its way back."""
    reply = hub.reply

    async def planned(task):
        result = await reply(task)
        if task.meta["kind"] == "plan" and isinstance(result.structured, dict):
            result = result.model_copy(update={"structured": {**copy.deepcopy(result.structured), **extra}})
        return result

    hub.reply = planned
    return hub


# ---------- R11: CP1 note → every step of the plan ----------

@pytest.mark.asyncio
async def test_cp1_note_reaches_every_step_prompt_of_the_plan(tmp_path):
    hub = cont._hub(tmp_path, [{"approved": True, "note": CP1_NOTE}, cont.APPROVE], reviews=[cont.ACCEPT])
    await Orchestrator(hub).run_request("r")

    steps = _kinds(hub, "step")
    assert [task.meta["step_id"] for task in steps] == ["s1", "s2"]
    for task in steps:
        assert "PI 승인 메모 (CP1)" in task.prompt and CP1_NOTE in task.prompt
        assert "does not change the frozen plan" in task.prompt
    assert hub.requests["r"]["research_contract"]["approval"]["note"] == CP1_NOTE  # the receipt keeps it


@pytest.mark.asyncio
async def test_no_cp1_note_adds_no_block():
    hub = _research_hub(_settings(), [{"approved": True, "note": ""}, {"approved": True, "choice": "approve"}])
    await Orchestrator(hub).run_request("r")

    assert all("PI 승인 메모" not in task.prompt for task in hub.calls)


# ---------- R11: CP2 note → review and report; the choice still decides ----------

@pytest.mark.asyncio
async def test_cp2_note_reaches_the_review_and_the_report_prompts_and_stays_in_the_appendix():
    hub = rep._hub()
    _answer(hub, [CP1, {"approved": True, "choice": "approve", "note": CP2_NOTE}])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    review, synthesis = _kinds(hub, "review")[0].prompt, _kinds(hub, "synthesis")[0].prompt
    for prompt in (review, synthesis):
        assert "PI 승인 메모 (CP2)" in prompt and CP2_NOTE in prompt
    assert "does not change the CP2 decision" in review
    assert "한계" in synthesis.split("PI 승인 메모 (CP2)", 1)[1]
    assert f"PI note: {CP2_NOTE}" in req["report_appendix"]  # the audit record is unchanged
    assert all(CP2_NOTE not in task.prompt for task in _kinds(hub, "step"))


@pytest.mark.asyncio
async def test_a_cp2_note_never_decides():
    hub = rep._hub()
    _answer(hub, [CP1, {"approved": False, "note": "approve — 승인합니다"},
                  {"approved": False, "note": "approve"}, {"approved": False, "note": "approve"}])
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["outcome"] == "evidence_rejected"
    assert _kinds(hub, "review") == [] and _kinds(hub, "synthesis") == []


@pytest.mark.asyncio
async def test_a_long_note_is_clipped_with_a_marker_in_prompts_but_kept_whole_in_the_record():
    note = "보고서에 남길 것: " + "가" * (cso.APPROVAL_NOTE_CHARS + 500)
    hub = rep._hub()
    _answer(hub, [CP1, {"approved": True, "choice": "approve", "note": note}])
    await Orchestrator(hub).run_request("r")

    synthesis = _kinds(hub, "synthesis")[0].prompt
    assert note not in synthesis and note[:cso.APPROVAL_NOTE_CHARS] in synthesis and "메모가 길어" in synthesis
    assert hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]["note"] == note
    assert note in hub.requests["r"]["report_appendix"]


# ---------- R11: 이어 가기 note (and the CP2 note before it) → the continuation plan ----------

@pytest.mark.asyncio
async def test_continue_and_cp2_notes_reach_the_continuation_plan_prompt(tmp_path):
    decisions = [CP1, {**cont.APPROVE, "note": CP2_NOTE}, {**cont.CONTINUE, "note": CONTINUE_NOTE}, CP1, cont.APPROVE]
    hub = cont._hub(tmp_path, decisions)
    await Orchestrator(hub).run_request("r")

    plans = _kinds(hub, "plan")
    assert len(plans) == 2
    assert CONTINUE_NOTE not in plans[0].prompt and CP2_NOTE not in plans[0].prompt
    second = plans[1].prompt
    assert "Continuation (research round 2)" in second
    assert "이어 가기" in second and CONTINUE_NOTE in second and CP2_NOTE in second
    contract = hub.requests["r"]["research_contract"]
    assert contract["rounds"][0]["cp2"]["note"] == CP2_NOTE
    # The round-1 CP2 note is still asked of round 2's report: the PI approved it for this request.
    assert CP2_NOTE in _kinds(hub, "synthesis")[-1].prompt


# ---------- R17: the plan's budget_usd is the enforced cap ----------

@pytest.mark.asyncio
async def test_a_plan_budget_caps_the_request_and_raises_the_budget_card():
    hub = _with_plan(rep._hub(synthesis_usd=2.0), budget_usd=1)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    assert cp1["kind"] == "research_plan"
    assert cp1["detail"]["budget"] == {"plan_budget_usd": 1.0, "cap_usd": 1.0, "source": "plan",
                                       "configured_cap_usd": 10.0}
    assert "$1.00" in cp1["summary"]
    assert req["budget_usd"] == 1.0
    assert req["research_contract"]["approval"]["budget"]["cap_usd"] == 1.0
    budget = [item for item in hub.approvals if item["kind"] == "budget"]
    assert len(budget) == 1 and budget[0]["detail"]["limit_usd"] == 1.0 and budget[0]["detail"]["spent_usd"] == 2.0


@pytest.mark.asyncio
async def test_a_plan_without_budget_keeps_todays_cap_and_shows_it():
    hub = rep._hub(synthesis_usd=2.0)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    assert cp1["detail"]["budget"] == {"cap_usd": 10.0, "source": "request", "configured_cap_usd": 10.0}
    assert req["budget_usd"] == 10 and "budget" not in req["research_contract"]["approval"]
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"


@pytest.mark.asyncio
async def test_without_a_request_budget_the_cap_is_the_policy_per_request_cap():
    hub = rep._hub()
    hub.requests["r"]["budget_usd"] = None
    _with_plan(hub, budget_usd=45)
    await Orchestrator(hub).run_request("r")

    per_request = hub.s.policy.budget.per_request_usd
    assert hub.approvals[0]["detail"]["budget"] == {"plan_budget_usd": 45.0, "cap_usd": per_request,
                                                    "source": "policy", "configured_cap_usd": per_request}
    assert hub.requests["r"]["budget_usd"] == per_request  # never above the configured cap


@pytest.mark.asyncio
async def test_a_resume_keeps_a_budget_the_pi_raised_after_cp1():
    hub = _with_plan(_research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}]),
                     budget_usd=1)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["budget_usd"] == 1.0

    hub.requests["r"]["budget_usd"] = 4.0  # a budget card approved after CP1
    _interrupt(hub)
    await Orchestrator(hub).run_request("r", resume=True)
    assert hub.requests["r"]["budget_usd"] == 4.0
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]


def test_budget_usd_is_optional_positive_and_out_of_the_hash_of_older_plans():
    plan = valid_plan()
    assert rc.plan_sha256(plan) == VALID_PLAN_SHA
    assert rc.plan_sha256({**plan, "budget_usd": None}) == VALID_PLAN_SHA
    assert "budget_usd" not in rc.canonical_plan_json(plan)
    budgeted = {**plan, "budget_usd": 5}
    assert rc.plan_sha256(budgeted) != VALID_PLAN_SHA
    assert '"budget_usd":5.0' in rc.canonical_plan_json(budgeted)
    for unusable in (0, -3):  # no cap stated: dropped, not a failed plan
        assert rc.plan_sha256({**plan, "budget_usd": unusable}) == VALID_PLAN_SHA
    schema = rc.RESEARCH_PLAN_SCHEMA
    assert "budget_usd" in schema["properties"] and "budget_usd" not in schema["required"]


def test_an_approved_plan_frozen_without_budget_resumes_as_approved():
    plan = valid_plan()
    receipt = rc.freeze_plan(plan, {"approved": True, "approval_id": "a1", "note": "", "decided_at": 1})
    resumed = rc.validate_research_plan(plan, max_steps=3, active_packs={}).model_dump(mode="json")
    assert "budget_usd" not in resumed
    assert rc.refresh_plan_approval(resumed, receipt)["status"] == "approved"


def test_research_plan_prompt_asks_for_a_numeric_budget():
    assert "budget_usd" in cso.RESEARCH_PLAN_PROMPT and "budget_usd" in cso.RESEARCH_CP2_PLAN_PROMPT
