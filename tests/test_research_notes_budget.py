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


def _lab_cap(hub, usd=10):
    """The lab's policy cap with no budget on the request: the plan's sum (spent + plan budget) is the cap up to it. A
    budget the PI wrote on the request is the cap itself (#944)."""
    hub.requests["r"]["budget_usd"] = None
    hub.s.policy.budget.per_request_usd = usd
    return hub


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


def _costed(hub, **usd_by_kind):
    """A task kind's cost in dollars, so the request has spent before CP1 as on the 2026-10-12 trial."""
    reply = hub.reply

    async def costed(task):
        result = await reply(task)
        usd = usd_by_kind.get(task.meta["kind"])
        return result if usd is None else result.model_copy(update={"cost_usd": usd, "cost_known": True})

    hub.reply = costed
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
    assert f"PI note: {CP2_NOTE}" in req["execution_record"]  # the audit record is unchanged
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
    assert note in hub.requests["r"]["execution_record"]


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
async def test_a_plan_budget_overrun_on_the_finished_report_is_recorded_without_a_card():
    hub = _lab_cap(_with_plan(rep._hub(synthesis_usd=2.0), budget_usd=1))
    orch = Orchestrator(hub)
    await orch.run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    assert cp1["kind"] == "research_plan"
    assert cp1["detail"]["budget"] == {"plan_budget_usd": 1.0, "spent_before_plan_usd": 0.0, "cap_usd": 1.0,
                                       "source": "plan", "configured_cap_usd": 10.0}
    assert "$1.00" in cp1["summary"]
    assert req["budget_usd"] == 1.0
    assert req["research_contract"]["approval"]["budget"]["cap_usd"] == 1.0
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    assert orch.budget_denials == {}
    assert orch.budget_outcomes["r"] == [{"spent_usd": 2.0, "limit_usd": 1.0,
                                           "approved": None, "final_turn": True}]
    assert "final turn; no further work" in req["execution_record"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reserve", [False, True])
async def test_a_report_turn_that_waits_for_an_answer_is_not_the_final_turn_until_its_wake_ends(reserve):
    """PR #527 review P2: the first report turn returned a question and ran over the cap; the record said "final
    turn; no further work" and then a budget card opened before the wake turn. The card is the one decision for
    that overrun, and the exemption is judged on the turn that really ends the step. With the pre-dispatch hold on
    (#1139) the raise also leaves room for a whole next turn, so the wake turn does not pass the raised cap and no
    final-turn record is written."""
    hub = _lab_cap(_with_plan(rep._hub(), budget_usd=1))
    hub.s.policy.budget.reserve_before_dispatch = reserve
    reply = hub.reply
    synthesis = []

    async def asking_report(task):
        if task.meta["kind"] != "synthesis":
            return await reply(task)
        synthesis.append(task)
        if len(synthesis) == 1:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, pending_asks=["ask_scope"],
                              cost_usd=2.0)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=rep.REPORT, cost_usd=1.0)

    async def wait_asks(ids):
        assert ids == ["ask_scope"]
        return [{"answer": "donor level", "from": "pi", "status": "answered"}]

    hub.reply, hub.wait_asks = asking_report, wait_asks
    _answer(hub, [CP1, {"approved": True, "choice": "approve", "note": ""}], budget=True)
    orch = Orchestrator(hub)
    await orch.run_request("r")

    req = hub.requests["r"]
    assert len(synthesis) == 2 and req["outcome"] == "research_reported"
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence", "budget"]
    appendix = req["execution_record"]
    if reserve:  # the card's raise left room for the wake turn: it ends inside the new cap
        assert orch.budget_outcomes["r"] == [{"spent_usd": 2.0, "limit_usd": 1.0, "approved": True}]
        assert appendix.count("final turn; no further work") == 0 and "$2.00 > $1.00; approved" in appendix
        return
    assert orch.budget_outcomes["r"] == [{"spent_usd": 2.0, "limit_usd": 1.0, "approved": True},
                                         {"spent_usd": 3.0, "limit_usd": 2.0, "approved": None, "final_turn": True}]
    assert appendix.count("final turn; no further work") == 1
    assert appendix.index("$2.00 > $1.00; approved") < appendix.index("$3.00 > $2.00; final turn")


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
    assert hub.approvals[0]["detail"]["budget"] == {"plan_budget_usd": 45.0, "spent_before_plan_usd": 0.0,
                                                    "cap_usd": per_request, "source": "policy",
                                                    "configured_cap_usd": per_request}
    assert hub.requests["r"]["budget_usd"] == per_request  # never above the configured cap


@pytest.mark.asyncio
async def test_a_resume_keeps_a_budget_the_pi_raised_after_cp1():
    hub = _lab_cap(_with_plan(_research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}]),
                              budget_usd=1))
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["budget_usd"] == 1.0

    hub.requests["r"]["budget_usd"] = 4.0  # a budget card approved after CP1
    _interrupt(hub)
    await Orchestrator(hub).run_request("r", resume=True)
    assert hub.requests["r"]["budget_usd"] == 4.0
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]


# ---------- trial 2026-10-12: the plan's budget_usd is what execution may spend, on top of the spend so far ----------

APPROVE_CP2 = {"approved": True, "choice": "approve", "note": ""}


@pytest.mark.asyncio
async def test_the_cap_is_the_spend_before_cp1_plus_the_plan_budget_and_no_card_follows_the_approval():
    """req_6e21a09296: $3.16 was spent before CP1, the plan said $3.00, the cap became $3.00 and the budget card
    "예산 초과: $3.16 / $3.00" opened 4 minutes after the PI approved the plan (PI asked for --budget 10)."""
    hub = _lab_cap(_costed(_with_plan(rep._hub(), budget_usd=3), plan=3.16))
    _answer(hub, [CP1, APPROVE_CP2])
    orch = Orchestrator(hub)
    await orch.run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    expected = {"plan_budget_usd": 3.0, "spent_before_plan_usd": 3.16, "cap_usd": 6.16, "source": "plan",
                "configured_cap_usd": 10.0}
    assert cp1["detail"]["budget"] == expected
    assert req["research_contract"]["approval"]["budget"] == expected
    assert req["budget_usd"] == 6.16
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]  # no budget card
    assert orch.budget_denials == {} and "r" not in orch.budget_outcomes
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "review", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"


@pytest.mark.asyncio
async def test_execution_past_the_plan_budget_on_top_of_the_spend_still_raises_the_card():
    hub = _lab_cap(_costed(_with_plan(rep._hub(), budget_usd=3), plan=3.16, step=2.9))
    _answer(hub, [CP1, APPROVE_CP2], budget=True)
    await Orchestrator(hub).run_request("r")
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]  # $6.06 of $6.16

    hub = _lab_cap(_costed(_with_plan(rep._hub(), budget_usd=3), plan=3.16, step=3.1))
    _answer(hub, [CP1, APPROVE_CP2], budget=True)
    await Orchestrator(hub).run_request("r")
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "budget", "research_evidence"]
    card = hub.approvals[1]
    assert card["detail"]["limit_usd"] == 6.16 and card["detail"]["spent_usd"] == pytest.approx(6.26)
    assert card["summary"].startswith("예산 초과: $6.26 / $6.16")


@pytest.mark.asyncio
async def test_the_configured_cap_stays_the_ceiling_when_the_spend_and_the_plan_budget_pass_it():
    hub = _costed(_with_plan(rep._hub(), budget_usd=3), plan=3.16)
    hub.requests["r"]["budget_usd"] = 4  # the PI's --budget
    _answer(hub, [CP1, APPROVE_CP2])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    assert cp1["detail"]["budget"] == {"plan_budget_usd": 3.0, "spent_before_plan_usd": 3.16, "cap_usd": 4.0,
                                       "source": "request", "configured_cap_usd": 4.0}
    assert "집행 상한 $4.00(요청 예산 $4.00에 맞춰 줄임: 계획 예산 $3.00 + 계획까지 쓴 $3.16 = $6.16)" in cp1["summary"]
    assert req["budget_usd"] == 4.0 and req["research_contract"]["approval"]["budget"]["cap_usd"] == 4.0
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]


def test_the_cp1_card_line_names_the_plan_budget_and_the_spend_before_it():
    plain = {"plan_budget_usd": 3.0, "spent_before_plan_usd": 2.96, "cap_usd": 5.96, "source": "plan",
             "configured_cap_usd": 10.0}
    assert cso._budget_line(plain) == ("집행 상한 $5.96(계획 예산 $3.00 + 계획까지 쓴 $2.96). "
                                      "넘으면 예산 카드가 뜹니다.")
    cut = {"plan_budget_usd": 3.0, "spent_before_plan_usd": 8.5, "cap_usd": 10.0, "source": "request",
           "configured_cap_usd": 10.0}
    assert cso._budget_line(cut) == ("집행 상한 $10.00(요청 예산 $10.00에 맞춰 줄임: "
                                    "계획 예산 $3.00 + 계획까지 쓴 $8.50 = $11.50).")
    assert cso._budget_line({**cut, "source": "policy"}).startswith(
        "집행 상한 $10.00(정책 per_request_usd $10.00에 맞춰 줄임: ")
    assert cso._budget_line({"cap_usd": 10.0, "source": "request", "configured_cap_usd": 10.0}) == (
        "집행 상한 $10.00(요청 예산, 계획에 budget_usd 없음).")


@pytest.mark.asyncio
async def test_step_and_review_prompts_say_how_the_enforced_cap_was_made():
    hub = _lab_cap(_costed(_with_plan(rep._hub(), budget_usd=3), plan=2.96))
    await Orchestrator(hub).run_request("r")

    for kind in ("step", "review"):
        assert ("Enforced spending cap for this request: $5.96 (계획 예산 $3.00 + 계획까지 쓴 $2.96, CP1 승인)"
                in _kinds(hub, kind)[0].prompt)

    # An approval frozen before the spend was counted keeps its old wording.
    hub.requests["r"]["budget_usd"] = 5.96
    hub.requests["r"]["research_contract"]["approval"]["budget"] = {
        "plan_budget_usd": 5.96, "cap_usd": 5.96, "source": "plan", "configured_cap_usd": 10.0}
    assert "(the plan's budget_usd approved at CP1)" in Orchestrator(hub)._spending_cap_line("r")


@pytest.mark.asyncio
async def test_a_continuation_round_counts_its_own_spend_at_its_cp1(tmp_path):
    """Round 1: a $2 plan turn, plan budget $1.20, cap $3.20, then two $0.50 steps: $3 spent. Round 2 plans for free: its
    cap is that $3 + its plan's $2 = $5, not the plan's $2, which the $3 already spent would have passed the moment CP1
    was approved. The plan budget leaves a little room: with the pre-dispatch hold on (#1139) a request that has used its
    cap to the cent asks before its next turn, which is not what this test is about."""
    decisions = [CP1, cont.APPROVE, cont.CONTINUE, CP1, cont.APPROVE]
    hub = _lab_cap(cont._hub(tmp_path, decisions))
    reply = hub.reply

    async def costed(task):
        result = await reply(task)
        second = "Continuation (research round 2)" in task.prompt
        if task.meta["kind"] == "plan" and isinstance(result.structured, dict):
            result = result.model_copy(update={"structured": {**result.structured, "budget_usd": 2 if second else 1.2}})
        usd = {"plan": 0.0 if second else 2.0, "step": 0.5}.get(task.meta["kind"], 0.0)
        return result.model_copy(update={"cost_usd": usd, "cost_known": True})

    hub.reply = costed
    orch = Orchestrator(hub)
    await orch.run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence", "research_continue",
                                                       "research_plan", "research_evidence"]
    assert first["budget_usd"] == 3.2  # round 1's cap: the $2 spent on its plan + the plan budget
    assert contract["approval"]["budget"] == {"plan_budget_usd": 2.0, "spent_before_plan_usd": 3.0, "cap_usd": 5.0,
                                              "source": "plan", "configured_cap_usd": 10.0}
    assert orch.budget_denials == {} and "r" not in orch.budget_outcomes
    assert contract["budget_cap"] == {"configured_cap_usd": 10.0, "source": "policy"}  # round 1's, never re-fixed


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
    for prompt in (cso.RESEARCH_PLAN_PROMPT, cso.RESEARCH_CP2_PLAN_PROMPT):
        assert "additional execution budget" in prompt
        assert "all earlier-round spending already incurred" in prompt
        assert "the most this whole request may spend" not in prompt


# ---------- PR #499 review ----------

@pytest.mark.asyncio
@pytest.mark.parametrize(("card", "outcome", "status"), [(False, "research_failed", "failed"),
                                                          (True, "plan_approved", "done")])
async def test_a_cap_already_passed_at_cp1_raises_the_card_before_a_request_ends_without_steps(card, outcome, status):
    """P1: the plan cost $2, the cap fixed for this request is $1.50 (the plan's $1 on top of the $2 spent would be $3,
    so it is cut to it; the request's own budget is $10 here, as after a budget card raised it), and with the evidence
    checkpoint off the request ends at CP1; it must not end "done" over its approved budget without a card."""
    hub = _research_hub(_settings(evidence_checkpoint=False), [])
    hub.requests["r"]["research_contract"] = {"budget_cap": {"configured_cap_usd": 1.5, "source": "request"}}
    reply = hub.reply

    async def costly_plan(task):
        result = await reply(task)
        return (result.model_copy(update={"cost_usd": 2.0, "cost_known": True}) if task.meta["kind"] == "plan"
                else result)

    hub.reply = costly_plan
    _with_plan(hub, budget_usd=1)
    _answer(hub, [{"approved": True, "note": ""}], budget=card)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "budget"]
    assert hub.approvals[1]["detail"]["limit_usd"] == 1.5 and hub.approvals[1]["detail"]["spent_usd"] == 2.0
    assert _kinds(hub, "step") == []
    assert req["outcome"] == outcome and req["status"] == status


def test_a_boolean_budget_fails_plan_validation_instead_of_vanishing():
    for value in (True, False):
        with pytest.raises(ValueError, match="budget_usd"):
            rc.validate_research_plan({**valid_plan(), "budget_usd": value}, max_steps=3, active_packs={})
        assert any("budget_usd" in problem for problem in
                   rc.research_plan_errors({**valid_plan(), "budget_usd": value}, max_steps=3, active_packs={}))


@pytest.mark.asyncio
async def test_earlier_round_cp2_notes_stay_in_the_audit_appendix_with_their_round(tmp_path):
    long_note = CP2_NOTE + " " + "나" * (cso.APPROVAL_NOTE_CHARS + 100)
    decisions = [CP1, {**cont.APPROVE, "note": long_note}, cont.CONTINUE, CP1, {**cont.APPROVE, "note": "2차 확인"}]
    hub = cont._hub(tmp_path, decisions)
    await Orchestrator(hub).run_request("r")

    appendix = hub.requests["r"]["execution_record"]
    assert f"PI note (round 1 CP2): {long_note}" in appendix  # whole, though prompts clip it
    assert "PI note: 2차 확인" in appendix


@pytest.mark.asyncio
async def test_step_and_review_prompts_carry_the_enforced_cap_not_only_resource_limits():
    hub = _lab_cap(_with_plan(rep._hub(), budget_usd=1))
    await Orchestrator(hub).run_request("r")

    for kind in ("step", "review"):
        prompt = _kinds(hub, kind)[0].prompt
        assert "Enforced spending cap for this request: $1.00" in prompt
        assert "protocol.resource_limits" in prompt.split("Enforced spending cap", 1)[1]


# ---------- #944: a budget the PI wrote on the request is the cap, not the plan's estimate ----------

@pytest.mark.asyncio
async def test_a_request_budget_of_40_stays_the_cap_after_cp1_when_the_plan_estimates_7():
    """10th attempt (req_fbaa5e34cf): $40 asked, $2.70 spent before CP1, plan $7 → the cap fell to $9.70 and two
    $10 per-task reservations opened two budget cards although the run cost $2.98."""
    hub = _costed(_with_plan(rep._hub(), budget_usd=7), plan=2.7, step=1.3)
    hub.requests["r"]["budget_usd"] = 40
    hub.s.policy.budget.per_task_usd = 10
    hub.s.policy.budget.reserve_before_dispatch = True
    _answer(hub, [CP1, APPROVE_CP2], budget=True)
    orch = Orchestrator(hub)
    await orch.run_request("r")

    req = hub.requests["r"]
    cp1 = hub.approvals[0]
    assert cp1["detail"]["budget"] == {"plan_budget_usd": 7.0, "spent_before_plan_usd": 2.7, "cap_usd": 40.0,
                                       "source": "request", "configured_cap_usd": 40.0}
    assert "집행 상한 $40.00(요청 예산 그대로). 계획 추정은 참고만 합니다: 계획 예산 $7.00 + 계획까지 쓴 $2.70 = $9.70" in cp1["summary"]
    assert req["budget_usd"] == 40.0 and req["research_contract"]["approval"]["budget"]["cap_usd"] == 40.0
    assert [item["kind"] for item in hub.approvals if item["kind"] == "budget"] == []  # 0 cards
    assert req["outcome"] == "research_reported" and orch.budget_denials == {}
    assert "$40.00 (the request's own budget" in orch._spending_cap_line("r")


@pytest.mark.asyncio
async def test_a_task_whose_full_cap_would_pass_the_request_budget_is_held_at_what_is_left():
    """35 spent of $40 and a $10 per-turn cap: the turn holds the $5 left (#1139) and goes without a card, so the request
    still cannot pass $40. Before the hold was cut to what is left, this asked."""
    hub = _costed(_with_plan(rep._hub(), budget_usd=7), plan=35.0, step=1.0)
    hub.requests["r"]["budget_usd"] = 40
    hub.s.policy.budget.per_task_usd = 10
    hub.s.policy.budget.reserve_before_dispatch = True
    _answer(hub, [CP1, APPROVE_CP2], budget=False)
    orch = Orchestrator(hub)
    await orch.run_request("r")

    assert [item for item in hub.approvals if item["kind"] == "budget"] == []
    step = _kinds(hub, "step")
    assert step and all(task.budget_usd <= 5.0 for task in step)  # capped at what the request had left
    assert hub.requests["r"]["cost_usd"] <= 40.0


@pytest.mark.asyncio
async def test_a_task_that_would_pass_the_request_budget_by_more_than_what_is_left_is_still_held_for_a_card():
    """Under a cent left cannot be held (the CLI renders cents): such a request asks, and nothing is sent past the cap."""
    hub = _costed(_with_plan(rep._hub(), budget_usd=7), plan=39.995, step=1.0)
    hub.requests["r"]["budget_usd"] = 40
    hub.s.policy.budget.per_task_usd = 10
    hub.s.policy.budget.reserve_before_dispatch = True
    _answer(hub, [CP1, APPROVE_CP2], budget=False)
    orch = Orchestrator(hub)
    await orch.run_request("r")

    cards = [item for item in hub.approvals if item["kind"] == "budget"]
    assert len(cards) == 1 and cards[0]["detail"]["limit_usd"] == 40.0
    assert not _kinds(hub, "step")  # nothing is sent past the cap


@pytest.mark.asyncio
async def test_a_limit_a_budget_card_raised_before_cp1_is_not_a_request_budget():
    """PR #945 review: no --budget was given; a card raised the policy limit to $20 before CP1. The plan's sum is still
    the cap, up to that limit: only a budget the PI wrote on the request is the cap itself."""
    hub = _costed(_with_plan(rep._hub(), budget_usd=7), plan=2.7)
    hub.requests["r"].update(budget_usd=20, requested_budget_usd=None)
    _answer(hub, [CP1, APPROVE_CP2])
    await Orchestrator(hub).run_request("r")

    budget = hub.approvals[0]["detail"]["budget"]
    assert budget == {"plan_budget_usd": 7.0, "spent_before_plan_usd": 2.7, "cap_usd": 9.7, "source": "plan",
                      "configured_cap_usd": 20.0}
    assert hub.requests["r"]["research_contract"]["budget_cap"]["source"] == "policy"
