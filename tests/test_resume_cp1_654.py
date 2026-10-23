"""#654: one resume card for a plan that was never approved, instead of a resume card and then CP1.

PI trial 2026-10-15: after a restart the PI approved "다시 이어 갈까요?" and CP1 asked again at once, because the resume
card never showed the plan. Now a resume card of a first research plan that has no CP1 receipt shows the plan and,
approved, records CP1 for that plan hash. A changed plan, a continuation or a plan after steps ran keep their CP1."""

from __future__ import annotations

import json
import time

import pytest

from labhq.gateway.server import Hub
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import plan_sha256
from labhq.settings import Settings
from tests import test_research_continue as cont
from tests.test_research_cp2 import CP1
from tests.test_research_cp1_revise import _gateway


class _Restart(BaseException):
    """The gateway process dies on the CP1 card: the plan is saved, no CP1 receipt is."""


async def _stopped_at_cp1(tmp_path, decisions):
    hub = cont._hub(tmp_path, decisions, reviews=[cont.ACCEPT])

    def die():
        raise _Restart()

    hub.hooks[1] = die
    with pytest.raises(_Restart):
        await Orchestrator(hub).run_request("r")
    hub.hooks.clear()
    hub.approvals.clear()
    hub.requests["r"]["status"] = "interrupted"
    return hub


def _resume_receipt(hub, target=None):
    plan = hub.requests["r"]["plan"]
    hub.requests["r"]["resume_cp1"] = {"approved": True, "target_sha256": target or plan_sha256(plan),
                                       "approval_id": "appr_resume", "decided_at": 5.0}


@pytest.mark.asyncio
async def test_an_approved_resume_card_that_showed_the_plan_stands_for_cp1(tmp_path):
    hub = await _stopped_at_cp1(tmp_path, [cont.APPROVE])
    _resume_receipt(hub)
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert "research_plan" not in [card["kind"] for card in hub.approvals]
    receipt = req["research_contract"]["approval"]
    assert receipt["approved"] is True and receipt["via"] == "resume_card" and receipt["approval_id"] == "appr_resume"
    assert receipt["target_sha256"] == req["research_contract"]["plan_sha256"] == plan_sha256(req["plan"])
    assert "resume_cp1" not in req  # read once
    assert req["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_a_stop_before_the_receipt_is_saved_keeps_the_resume_answer(tmp_path, monkeypatch):
    hub = await _stopped_at_cp1(tmp_path, [cont.APPROVE])
    _resume_receipt(hub)

    async def dies(self, rid, stage):
        raise _Restart()

    monkeypatch.setattr(Orchestrator, "_semantic_devices_check", dies)
    with pytest.raises(_Restart):
        await Orchestrator(hub).run_request("r", resume=True)
    assert hub.requests["r"]["resume_cp1"]["approval_id"] == "appr_resume"  # the PI's answer survives the stop

    monkeypatch.undo()
    hub.requests["r"]["status"] = "interrupted"
    await Orchestrator(hub).run_request("r", resume=True)
    assert "research_plan" not in [card["kind"] for card in hub.approvals]
    assert hub.requests["r"]["research_contract"]["approval"]["via"] == "resume_card"
    assert "resume_cp1" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_a_plan_that_changed_since_the_resume_card_asks_cp1_again(tmp_path):
    hub = await _stopped_at_cp1(tmp_path, [CP1, cont.APPROVE])
    _resume_receipt(hub, target="d" * 64)
    await Orchestrator(hub).run_request("r", resume=True)

    cards = [card for card in hub.approvals if card["kind"] == "research_plan"]
    assert len(cards) == 1
    assert "via" not in hub.requests["r"]["research_contract"]["approval"]
    assert "resume_cp1" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_a_resume_without_the_receipt_asks_cp1_as_before(tmp_path):
    hub = await _stopped_at_cp1(tmp_path, [CP1, cont.APPROVE])
    await Orchestrator(hub).run_request("r", resume=True)
    assert [card["kind"] for card in hub.approvals].count("research_plan") == 1


@pytest.mark.asyncio
async def test_the_card_text_lists_the_plan_and_only_a_first_unapproved_plan_gets_it(tmp_path):
    hub = await _stopped_at_cp1(tmp_path, [])
    orch = Orchestrator(hub)
    req = hub.requests["r"]
    card = orch.resume_plan_card("r", req)
    assert card["target_sha256"] == plan_sha256(req["plan"])
    assert f"연구 계획 {len(req['plan']['steps'])}단계" in card["text"] and "s1" in card["text"]
    assert "CP1" in card["text"] and "집행 상한" in card["text"]
    assert len(card["text"]) < 700

    approved = {**req, "research_contract": {"approval": {"approved": True, "target_sha256": card["target_sha256"]}}}
    assert orch.resume_plan_card("r", approved) is None  # already approved at this hash
    assert orch.resume_plan_card("r", {**req, "results": {"s1": {"ok": True}}}) is None
    assert orch.resume_plan_card("r", {**req, "research_contract": {"continuation": {"round": 2}}}) is None
    assert orch.resume_plan_card("r", {**req, "plan": {"steps": [{"id": "s1"}]}}) is None  # not a research plan
    # A re-hire note is shown only on the CP1 card, so that plan keeps CP1 as its own question.
    rehired = {**req, "plan": {**req["plan"], "warnings": ["직원 재배정: s2"]}}
    assert orch.resume_plan_card("r", rehired) is None


@pytest.mark.asyncio
async def test_the_gateway_resume_card_shows_the_plan_and_its_approval_records_the_receipt(tmp_path, monkeypatch):
    hub = _gateway(tmp_path, monkeypatch)
    plan = cont._plan(1)
    hub.requests["r"] = {"id": "r", "text": "compare conditions", "mode": "orchestrate", "status": "running",
                         "created_at": time.time(), "plan": plan}
    hub.interrupt_request("r")
    card = next(a["approval"] for a in hub.approvals.values() if a["approval"]["kind"] == "resume")
    assert card["detail"]["cp1_target_sha256"] == plan_sha256(plan)
    assert "연구 계획" in card["summary"] and "s1" in card["summary"]
    assert json.loads(card["detail"]["plan_canonical"])["steps"][0]["id"] == "s1"  # the whole frozen plan, as CP1
    assert card["detail"]["scope_status"] and card["detail"]["protocol_revision"]

    await hub.resolve_approval(card["id"], True)
    assert hub.requests["r"]["resume_cp1"] == {
        "approved": True, "target_sha256": plan_sha256(plan), "approval_id": card["id"],
        "decided_at": hub.requests["r"]["resume_cp1"]["decided_at"]}


@pytest.mark.asyncio
async def test_the_gateway_records_nothing_when_the_resume_is_declined_or_has_no_plan(tmp_path, monkeypatch):
    hub = _gateway(tmp_path, monkeypatch)
    hub.requests["r"] = {"id": "r", "text": "compare conditions", "mode": "orchestrate", "status": "running",
                         "created_at": time.time(), "plan": cont._plan(1)}
    hub.requests["bare"] = {"id": "bare", "text": "no plan yet", "mode": "orchestrate", "status": "running",
                            "created_at": time.time()}
    hub.interrupt_request("r")
    hub.interrupt_request("bare")
    cards = {a["approval"]["request_id"]: a["approval"] for a in hub.approvals.values()
             if a["approval"]["kind"] == "resume"}
    assert "cp1_target_sha256" not in cards["bare"]["detail"] and "CP1" not in cards["bare"]["summary"]

    await hub.resolve_approval(cards["r"]["id"], False)
    await hub.resolve_approval(cards["bare"]["id"], True)
    assert "resume_cp1" not in hub.requests["r"] and "resume_cp1" not in hub.requests["bare"]
