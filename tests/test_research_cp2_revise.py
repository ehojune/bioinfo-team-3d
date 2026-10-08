"""CP2 수정 요청 re-plans through a new CP1 instead of ending the request (PI 점검 R10).

The PI's CP2 note is the revision request: the CSO writes a new plan from it, the PI approves a new CP1, and only the
steps the new plan changes (and their dependents) run again. research.revise_continuations caps CP2 revises and review
revises together; past it, or when the new round ends before any step ran, the request ends as
``evidence_revision_requested`` the way every CP2 revise did before."""

from __future__ import annotations

import pytest

from labhq.evidence.audit import ledger_binding_problems
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import validate_research_result
from tests import test_research_continue as cont
from tests.test_research_cp2 import CP1

NOTE = "s2 효과 열을 donor 단위로 다시 계산하고 민감도 분석을 붙여 주세요."
REVISE = {"approved": False, "choice": "revise", "note": NOTE}
BARE_REVISE = {"approved": False, "choice": "revise", "note": "  "}


def _kinds(hub):
    return [approval["kind"] for approval in hub.approvals]


def _plans(hub):
    return [task for task in hub.calls if task.meta["kind"] == "plan"]


@pytest.mark.asyncio
async def test_a_cp2_revise_with_a_note_replans_through_a_new_cp1_and_reruns_only_the_changed_step(tmp_path):
    hub = cont._hub(tmp_path, [CP1, REVISE, CP1, cont.APPROVE], reviews=[cont.ACCEPT])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    # No extra 이어 가기 card: the PI chose to revise at CP2; the new CP1 is where the new plan is approved.
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_plan", "research_evidence"]
    assert cont._steps(hub) == ["plan", "s1", "s2", "plan", "s2", "review", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"

    first, = contract["rounds"]
    assert first["cp2"]["decision"] == "revision_requested" and first["cp2"]["note"] == NOTE
    assert first["review"] is None  # the first round never reached the review
    carried = contract["continuation"]
    assert carried["trigger"] == "cp2_revise" and carried["pi_request"] == NOTE and carried["p1_issues"] == []
    assert carried["approval_id"] == first["cp2"]["approval_id"] == "a2"
    assert carried["reuse"] == ["s1"] and carried["rerun"] == {"s2": "spec changed"}
    assert req["results"]["s1"]["task_id"] == first["results"]["s1"]["task_id"]
    for result in req["results"].values():
        validate_research_result(result["structured"], plan=req["plan"])
    assert ledger_binding_problems(req) == []

    # The PI note is the new plan's revision request, once (not repeated as a CP2 approval note).
    second = _plans(hub)[1].prompt
    assert "Continuation (research round 2)" in second and "PI revision request (CP2):\n" + NOTE in second
    assert second.count(NOTE) == 1 and "P1 issues:" not in second
    assert first["plan_sha256"] in second
    cp1 = hub.approvals[2]
    assert cp1["summary"].startswith("이어 가기 2차 계획(CP2 수정 요청 반영). 재사용: s1 · 다시 실행: s2. ")
    assert cp1["detail"]["continuation"]["trigger"] == "cp2_revise"
    assert cp1["detail"]["continuation"]["pi_request"] == NOTE
    # The round-2 reviewer checks the revision the PI asked for, and the record keeps the request whole.
    review = next(task for task in hub.calls if task.meta["kind"] == "review")
    assert "asked at CP2 for this revision" in review.prompt and NOTE in review.prompt
    assert f"PI 수정 요청 (round 1 CP2): {NOTE}" in req["report_appendix"]
    continued = [event for event in hub.events if event.get("type") == "request.continued"]
    assert [event["data"]["trigger"] for event in continued] == ["cp2_revise"]


@pytest.mark.asyncio
async def test_a_cp2_revise_without_a_note_is_asked_again_for_one(tmp_path):
    hub = cont._hub(tmp_path, [CP1, BARE_REVISE, REVISE, CP1, cont.APPROVE], reviews=[cont.ACCEPT])
    await Orchestrator(hub).run_request("r")

    assert _kinds(hub) == ["research_plan", "research_evidence", "research_evidence", "research_plan",
                           "research_evidence"]
    assert hub.approvals[2]["summary"].startswith("수정 요청에 메모가 없어 다시 묻습니다.")
    receipt = hub.requests["r"]["research_contract"]["rounds"][0]["cp2"]
    assert receipt["asks"] == 2 and receipt["note"] == NOTE
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_a_cp2_revise_that_never_gets_a_note_ends_without_a_new_plan(tmp_path):
    hub = cont._hub(tmp_path, [CP1, BARE_REVISE, dict(BARE_REVISE), dict(BARE_REVISE)])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_evidence", "research_evidence"]
    assert len(_plans(hub)) == 1 and "rounds" not in req["research_contract"]
    assert req["outcome"] == "evidence_revision_requested" and req["status"] == "failed"
    assert "수정 요청에 메모가 없어 새 계획을 세우지 않았습니다." in req["report"] + req.get("report_appendix", "")


@pytest.mark.asyncio
async def test_past_the_cap_a_cp2_revise_ends_the_request_as_before(tmp_path):
    hub = cont._hub(tmp_path, [CP1, {**REVISE}], limit=0)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    card = hub.approvals[1]
    assert card["detail"]["revise_continues"] is False and "상한" in card["summary"]
    assert _kinds(hub) == ["research_plan", "research_evidence"] and len(_plans(hub)) == 1
    assert req["outcome"] == "evidence_revision_requested" and req["status"] == "failed"
    text = req["report"] + req.get("report_appendix", "")
    assert "research.revise_continuations=0" in text and f"PI note: {NOTE}" in text


@pytest.mark.asyncio
async def test_past_the_cap_an_empty_revise_is_not_asked_again(tmp_path):
    hub = cont._hub(tmp_path, [CP1, dict(BARE_REVISE)], limit=0)
    await Orchestrator(hub).run_request("r")

    assert _kinds(hub) == ["research_plan", "research_evidence"]
    assert hub.requests["r"]["outcome"] == "evidence_revision_requested"


@pytest.mark.asyncio
async def test_cp2_revises_and_review_revises_share_one_cap(tmp_path):
    # Round 1 CP2 revise uses the only continuation; round 2's review "revise" then gets no 이어 가기 card.
    hub = cont._hub(tmp_path, [CP1, REVISE, CP1, cont.APPROVE], reviews=[cont.REVISE], limit=1)
    await Orchestrator(hub).run_request("r")
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_plan", "research_evidence"]
    assert hub.requests["r"]["outcome"] == "research_review_revise"
    assert "상한" in hub.requests["r"]["report"] + hub.requests["r"].get("report_appendix", "")

    # A review revise uses it first; the next CP2 revise ends the request as before, and its card says so.
    hub = cont._hub(tmp_path / "second", [CP1, cont.APPROVE, cont.CONTINUE, CP1, REVISE],
                    reviews=[cont.REVISE], limit=1)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_continue", "research_plan",
                           "research_evidence"]
    assert hub.approvals[1]["detail"]["revise_continues"] is True
    assert hub.approvals[4]["detail"]["revise_continues"] is False
    assert req["outcome"] == "evidence_revision_requested" and len(req["research_contract"]["rounds"]) == 1
    assert "research.revise_continuations=1" in req["report"] + req.get("report_appendix", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("new_cp1", [{"approved": False, "note": "no"},
                                     {"approved": False, "note": "", "state": "timed_out"}])
async def test_a_cp2_revise_round_that_ends_before_dispatch_ends_as_the_revision_request(tmp_path, new_cp1):
    hub = cont._hub(tmp_path, [CP1, REVISE, new_cp1])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert req["outcome"] == "evidence_revision_requested" and req["status"] == "failed"
    assert cont._steps(hub) == ["plan", "s1", "s2", "plan"]
    assert "이어 가기 2차가 단계 실행 전에 끝났습니다" in req["report"] and f"PI 수정 요청: {NOTE}" in req["report"]
    assert "Research review" not in req["report"]
    # Round 1 comes back whole: its plan, CP1 approval, revise receipt and results.
    assert req["plan"] == first["plan"] and contract["plan_sha256"] == first["plan_sha256"]
    assert contract["checkpoints"]["cp2"] == first["cp2"] and contract["approval"] == first["approval"]
    assert {sid: result["task_id"] for sid, result in req["results"].items()} == {
        sid: result["task_id"] for sid, result in first["results"].items()}
    assert ledger_binding_problems(req) == []
    assert contract["continuation"]["declined_plan"]["plan"]["steps"][1]["instruction"] == cont.FIXED


class _Restart(BaseException):
    """The gateway process dies here: nothing after it runs, and saved state is what a restart reads."""


@pytest.mark.asyncio
@pytest.mark.parametrize(("die", "after"), [
    ("plan", ["plan", "s2", "review", "synthesis"]),  # while the CSO writes the new plan
    ("cp1", ["s2", "review", "synthesis"]),  # on the new CP1 card, after the new plan was adopted
])
async def test_restart_in_the_middle_of_a_cp2_revise_continuation(tmp_path, die, after):
    hub = cont._hub(tmp_path, [CP1, REVISE, CP1, cont.APPROVE], reviews=[cont.ACCEPT])
    reply = hub.reply
    dead = {"plan": die == "plan"}

    async def dying(task):
        if dead["plan"] and task.meta["kind"] == "plan" and "Continuation (research round 2)" in task.prompt:
            dead["plan"] = False
            raise _Restart()
        return await reply(task)

    def die_on_card():
        raise _Restart()

    hub.reply = dying
    if die == "cp1":
        hub.hooks[3] = die_on_card
    with pytest.raises(_Restart):
        await Orchestrator(hub).run_request("r")
    hub.hooks.clear()
    if die == "cp1":
        hub.approvals.pop()  # the card that was up when the process died is asked again
    calls = len(hub.calls)
    hub.requests["r"]["status"] = "interrupted"
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert cont._steps(hub, calls) == after  # s1 never runs again and CP2 of round 1 is not asked again
    assert _kinds(hub).count("research_evidence") == 2
    assert req["outcome"] == "research_reported"
    assert len(req["research_contract"]["rounds"]) == 1
    assert req["research_contract"]["continuation"]["reuse"] == ["s1"]
    assert ledger_binding_problems(req) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "kinds", "outcome"), [
    ([CP1, REVISE, CP1], ["research_plan", "research_evidence", "research_plan", "budget"],
     "evidence_revision_requested"),
    ([CP1, cont.APPROVE, cont.CONTINUE, CP1],
     ["research_plan", "research_evidence", "research_continue", "research_plan", "budget"], "research_review_revise"),
])
async def test_a_rolled_back_round_restores_the_previous_spending_cap(tmp_path, decisions, kinds, outcome):
    """PR #499 review: round 2's CP1 lowered the cap to $1, the budget card for the $4 already spent was denied, and
    the request went back to round 1. Its $5 cap must come back with its plan and CP1 receipt."""
    hub = cont._hub(tmp_path, [*decisions, {"approved": False, "note": ""}], reviews=[cont.REVISE])
    reply = hub.reply

    async def costed(task):
        result = await reply(task)
        if task.meta["kind"] == "plan" and isinstance(result.structured, dict):
            budget = 1 if "Continuation (research round 2)" in task.prompt else 5
            result = result.model_copy(update={"structured": {**result.structured, "budget_usd": budget}})
        return result.model_copy(update={"cost_usd": 2.0 if task.meta["kind"] == "plan" else 0.0,
                                         "cost_known": True})

    hub.reply = costed
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert _kinds(hub) == kinds
    assert hub.approvals[-1]["detail"]["limit_usd"] == 1.0
    assert req["outcome"] == outcome
    assert contract["continuation"]["ended_before_dispatch"]["outcome"] == "research_failed"
    assert first["budget_usd"] == 5.0
    assert contract["approval"]["budget"]["cap_usd"] == 5.0 and req["budget_usd"] == 5.0


@pytest.mark.asyncio
async def test_the_cp1_card_summary_is_korean_and_keeps_the_hash_in_detail(tmp_path):
    hub = cont._hub(tmp_path, [CP1, cont.APPROVE], reviews=[cont.ACCEPT])
    await Orchestrator(hub).run_request("r")

    card = hub.approvals[0]
    plan_hash = hub.requests["r"]["research_contract"]["plan_sha256"]
    assert card["summary"].startswith("CP1 연구 계획 승인: 고정할 질문·방법·완료/중단 조건·데이터 범위·적용 pack을 ")
    assert plan_hash not in card["summary"] and "plan_sha256" not in card["summary"]
    assert card["detail"]["target_sha256"] == plan_hash


def test_the_cli_refuses_a_revise_without_a_note(monkeypatch):
    from labhq import cli

    pending = [{"id": "a_cp2", "kind": "research_evidence", "summary": "CP2",
                "detail": {"revise_continues": True}},
               {"id": "a_end", "kind": "research_evidence", "summary": "CP2",
                "detail": {"revise_continues": False}}]
    posts = []

    def api(_settings, method, path, **kwargs):
        if method == "GET":
            return pending
        posts.append((path, kwargs["json"]))
        return {"ok": True}

    monkeypatch.setattr("labhq.cli._api", api)
    for argv in (["approve", "a_cp2", "--choice", "revise"],
                 ["approve", "a_cp2", "--choice", "revise", "--note", "   "]):
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert posts == []
    cli.main(["approve", "a_cp2", "--choice", "revise", "--note", NOTE])
    cli.main(["approve", "a_end", "--choice", "revise"])  # past the cap a revise ends anyway, so no note is needed
    assert posts == [("/api/approvals/a_cp2", {"approved": False, "note": NOTE, "choice": "revise"}),
                     ("/api/approvals/a_end", {"approved": False, "note": "", "choice": "revise"})]
