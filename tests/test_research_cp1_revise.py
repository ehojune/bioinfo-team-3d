"""CP1 수정 요청: the PI's note re-plans in a new request instead of 거절 and retyping the request.

Web trial 2026-10-08: CP1 had only 승인 and 거절, so a plan with one wrong detail had to be approved with a memo or
rejected and sent again by hand. Now 수정 요청 with a note ends the request without running a step and opens a new
research request with the same text, references, answers and budget plus the note; that one plans again and asks a
new CP1. A continuation round's CP1 does not offer it: its plan rests on the previous round's results."""

from __future__ import annotations

import asyncio
import time

import pytest

from labhq.gateway.server import Hub
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings
from tests import test_research_continue as cont
from tests.test_research_cp2 import CP1

NOTE = "s2 전에 샘플 QC 단계를 넣고, DE는 donor 단위로 계산해 주세요."
REVISE = {"approved": False, "choice": "revise", "note": NOTE}
BARE_REVISE = {"approved": False, "choice": "revise", "note": " "}


def _with_revise(hub):
    hub.revised = []

    def create_revised_request(rid, note):
        hub.revised.append((rid, note))
        return "req_revised"

    hub.create_revised_request = create_revised_request
    return hub


def _kinds(hub):
    return [approval["kind"] for approval in hub.approvals]


@pytest.mark.asyncio
async def test_a_cp1_revise_with_a_note_opens_a_new_request_and_runs_no_step(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [REVISE]))
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["research_plan"] and cont._steps(hub) == ["plan"]
    card = hub.approvals[0]
    assert card["detail"]["revise_allowed"] is True and "수정 요청" in card["summary"]
    assert hub.revised == [("r", NOTE)]
    # Not a failure: the PI asked for another plan, and the report names where it continues.
    assert req["outcome"] == "plan_revision_requested" and req["status"] == "done"
    assert "req_revised" in req["report"] and NOTE in req["report"]
    receipt = req["research_contract"]["approval"]
    assert receipt["approved"] is False and receipt["choice"] == "revise" and receipt["note"] == NOTE


@pytest.mark.asyncio
async def test_a_cp1_revise_without_a_note_is_asked_again_and_a_plain_deny_still_rejects(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [BARE_REVISE, REVISE]))
    await Orchestrator(hub).run_request("r")
    assert _kinds(hub) == ["research_plan", "research_plan"]
    assert hub.approvals[1]["summary"].startswith("수정 요청에 메모가 없어 다시 묻습니다.")
    assert hub.revised == [("r", NOTE)] and hub.requests["r"]["outcome"] == "plan_revision_requested"

    # Three empty notes end it the way 거절 does; no request is opened from nothing.
    hub = _with_revise(cont._hub(tmp_path / "bare", [BARE_REVISE, BARE_REVISE, BARE_REVISE]))
    await Orchestrator(hub).run_request("r")
    assert len(hub.approvals) == 3 and hub.revised == [] and hub.requests["r"]["outcome"] == "plan_rejected"

    hub = _with_revise(cont._hub(tmp_path / "deny", [{"approved": False, "choice": "deny", "note": "아님"}]))
    await Orchestrator(hub).run_request("r")
    assert hub.revised == [] and hub.requests["r"]["outcome"] == "plan_rejected"


@pytest.mark.asyncio
async def test_a_gateway_without_the_new_request_path_offers_no_revise_on_cp1(tmp_path):
    hub = cont._hub(tmp_path, [REVISE])  # no create_revised_request: 수정 요청 reads as 거절, as before
    await Orchestrator(hub).run_request("r")
    assert hub.approvals[0]["detail"]["revise_allowed"] is False and "수정 요청" not in hub.approvals[0]["summary"]
    assert hub.requests["r"]["outcome"] == "plan_rejected"


@pytest.mark.asyncio
async def test_a_continuation_rounds_cp1_offers_no_revise(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [CP1, cont.APPROVE, cont.CONTINUE, REVISE]))
    await Orchestrator(hub).run_request("r")
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_continue", "research_plan"]
    assert hub.approvals[0]["detail"]["revise_allowed"] is True
    second = hub.approvals[3]["detail"]
    assert second["revise_allowed"] is False and second["continuation"]["round"] == 2
    assert hub.revised == []  # the round-2 CP1 수정 요청 ends the continuation like 거절


@pytest.mark.asyncio
async def test_a_restart_after_the_new_request_was_opened_ends_without_asking_again(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [REVISE]))
    orch = Orchestrator(hub)
    await orch.run_request("r")
    req = hub.requests["r"]
    # As if the process stopped after Hub.create_revised_request saved the link and before the request ended.
    req["revised_to"] = "req_revised"
    req["status"] = "running"
    req.pop("outcome", None)
    asked = len(hub.approvals)
    await Orchestrator(hub).run_request("r")
    assert len(hub.approvals) == asked and hub.revised == [("r", NOTE)]
    assert req["outcome"] == "plan_revision_requested" and "req_revised" in req["report"]


def _gateway(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.started = []

    async def no_start(rid):
        hub.started.append(rid)

    monkeypatch.setattr(hub, "_start_request", no_start)
    return hub


OLD_PLAN = {"brief": {"question": "Does smoking change expression?"}, "steps": [{"id": "s1"}, {"id": "s2"}]}


def _old_request(hub, **extra):
    hub.requests["old"] = {
        "id": "old", "text": "GSE10072 흡연자 대 비흡연자 DE", "mode": "orchestrate", "status": "waiting_pi",
        "created_at": time.time(), "work_kind": "auto", "scope_status": "in_scope", "project_dirs": [],
        "references": [{"kind": "pmid", "value": "18297132", "note": "원 논문", "source": "request"},
                       {"kind": "url", "value": "https://example.org/data.tsv", "note": None, "source": "request",
                        "query_removed": True},
                       {"kind": "doi", "value": "10.1000/lab-default", "note": None, "source": "pi_profile"}],
        "default_references": True, "budget_usd": 80.0, "requested_budget_usd": 40.0, "project_id": None,
        "cso_model": None, "route": "auto", "meta": {"case": "trial"}, "plan": OLD_PLAN,
        "research_contract": {"plan_sha256": "a" * 64},
        "clarifications": [{"questions": ["외부 검증?"], "answer": "b) 없음"}],
        "pi_notes": [{"text": "TCGA는 쓰지 마세요", "at": 1.0}], **extra}
    hub.save_request("old")
    hub.store.put("reference_original", "old",
                  {"references": [{"value": "https://example.org/data.tsv", "original": "https://example.org/data.tsv?sig=x"}]})


@pytest.mark.asyncio
async def test_the_gateway_opens_the_revised_request_with_the_plan_note_answers_and_pointers(tmp_path, monkeypatch):
    hub = _gateway(tmp_path, monkeypatch)
    _old_request(hub)

    new = hub.create_revised_request("old", NOTE)
    await asyncio.sleep(0)
    req = hub.requests[new]
    assert req["text"].startswith("GSE10072 흡연자 대 비흡연자 DE") and NOTE in req["text"] and "old" in req["text"]
    assert req["work_kind"] == "research" and req["mode"] == "orchestrate"
    # The budget the PI asked for, not the one a budget card later doubled.
    assert req["budget_usd"] == 40.0 == req["requested_budget_usd"]
    # The plan the note talks about goes with it (Claude review P1: "s2 전에" had no s2 to refer to).
    assert req["revision_of"] == {"request_id": "old", "plan_sha256": "a" * 64, "plan": OLD_PLAN, "note": NOTE}
    # The request's own pointers are carried, the cleaned URL flagged again; the PI default is not duplicated.
    own = [ref for ref in req["references"] if ref["source"] == "request"]
    assert [(ref["kind"], ref["value"]) for ref in own] == [("pmid", "18297132"), ("url", "https://example.org/data.tsv")]
    assert own[1].get("query_removed") is True and req["default_references"] is True
    # Answers and notes given to the old request; the answers do not use this request's clarify cards.
    assert req["clarifications"] == [{"questions": ["외부 검증?"], "answer": "b) 없음", "inherited": True}]
    assert req["pi_notes"] == hub.requests["old"]["pi_notes"]
    assert req["revised_from"] == "old" and req["meta"]["revised_from"] == "old" and req["meta"]["case"] == "trial"
    assert hub.requests["old"]["revised_to"] == new and hub.started == [new]
    # All of it is in the new request's first save: a restart right after it finds the same request again.
    saved = hub.store.get("request", new)
    assert saved["revision_of"]["note"] == NOTE and saved["clarifications"][0]["inherited"] is True
    hub.requests["old"].pop("revised_to")
    assert hub.revised_request_of("old") == new
    assert hub.create_revised_request("old", NOTE) == new and len(hub.started) == 1
    assert hub.requests["old"]["revised_to"] == new


def test_the_gateway_raises_when_the_old_request_can_no_longer_be_created(tmp_path, monkeypatch):
    hub = _gateway(tmp_path, monkeypatch)
    _old_request(hub, project_id="removed-project")
    with pytest.raises(KeyError):
        hub.create_revised_request("old", NOTE)
    assert hub.revised_request_of("old") is None and "revised_to" not in hub.requests["old"]


def test_the_cli_takes_a_cp1_revise_with_a_note_only_where_the_card_offers_it(monkeypatch):
    from labhq import cli

    pending = [{"id": "a_cp1", "kind": "research_plan", "summary": "CP1", "detail": {"revise_allowed": True}},
               {"id": "a_round2", "kind": "research_plan", "summary": "CP1", "detail": {"revise_allowed": False}}]
    posts = []

    def api(_settings, method, path, **kwargs):
        if method == "GET":
            return pending
        posts.append((path, kwargs["json"]))
        return {"ok": True}

    monkeypatch.setattr("labhq.cli._api", api)
    for argv in (["approve", "a_cp1", "--choice", "revise"],               # the note is what it re-plans from
                 ["approve", "a_round2", "--choice", "revise", "--note", NOTE]):  # a continuation CP1 has none
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert posts == []
    cli.main(["approve", "a_cp1", "--choice", "revise", "--note", NOTE])
    cli.main(["approve", "a_round2"])  # 승인 needs no choice, as before
    assert posts == [("/api/approvals/a_cp1", {"approved": False, "note": NOTE, "choice": "revise"}),
                     ("/api/approvals/a_round2", {"approved": True, "note": ""})]


@pytest.mark.asyncio
async def test_a_revised_request_plans_from_the_previous_plan_and_the_note(tmp_path):
    hub = cont._hub(tmp_path, [CP1, cont.APPROVE], reviews=[cont.ACCEPT])
    hub.requests["r"]["revision_of"] = {"request_id": "req_old", "plan_sha256": "b" * 64, "note": NOTE,
                                        "plan": {"brief": {"question": "old question"}, "steps": [{"id": "s2"}]}}
    await Orchestrator(hub).run_request("r")
    plan_prompt = next(task for task in hub.calls if task.meta["kind"] == "plan").prompt
    assert "CP1 revision request" in plan_prompt and "req_old" in plan_prompt and NOTE in plan_prompt
    assert "b" * 64 in plan_prompt and "old question" in plan_prompt


@pytest.mark.asyncio
async def test_a_revise_that_cannot_open_its_new_request_keeps_the_note_in_the_report(tmp_path):
    hub = cont._hub(tmp_path, [REVISE])

    def gone(rid, note):
        raise KeyError("unknown project 'removed-project'")

    hub.create_revised_request = gone
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "plan_revision_failed" and req["status"] == "failed"
    assert NOTE in req["report"] and "removed-project" in req["report"] and cont._steps(hub) == ["plan"]


@pytest.mark.asyncio
async def test_a_restart_finds_the_new_request_by_its_link_even_before_revised_to_was_saved(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [REVISE]))
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    req.pop("revised_to", None)
    req["status"] = "running"
    hub.revised_request_of = lambda rid: "req_revised" if rid == "r" else None
    asked = len(hub.approvals)
    await Orchestrator(hub).run_request("r")
    assert len(hub.approvals) == asked and hub.revised == [("r", NOTE)]
    assert req["outcome"] == "plan_revision_requested"


@pytest.mark.asyncio
async def test_a_re_approval_of_a_plan_whose_steps_ran_offers_no_revise(tmp_path):
    hub = _with_revise(cont._hub(tmp_path, [CP1]))
    hub.requests["r"]["results"] = {"s1": {"ok": True}}  # a resumed request whose plan changed after steps ran
    hub.requests["r"]["research_contract"] = {"approval": {"approved": True, "target_sha256": "c" * 64}}
    orch = Orchestrator(hub)
    try:
        await orch.run_request("r")
    except Exception:
        pass
    plan_cards = [approval for approval in hub.approvals if approval["kind"] == "research_plan"]
    assert plan_cards and all(card["detail"]["revise_allowed"] is False for card in plan_cards)
