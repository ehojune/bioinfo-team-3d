"""The reviewers answer each declared checklist item and the report names them (#586, item 3).

In the 10-13 PI trial both reviewers received the topic checklist and the plan's answers, but no finding cited a
check, and the report never named one: only assumption and not_applicable answers reached it."""

from __future__ import annotations

import copy

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import (CHECKLIST_REVIEW_RULE, REVIEW_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, Orchestrator,
                                    review_with_checklist, valid_review)
from labhq.vocab import topic_checklists
from tests.test_cso import FakeHub
from tests.test_research_cp2 import _interrupt
from tests.test_research_report import ACCEPT, REPORT, _hub, _kinds
from tests.test_topic_checklists_precedents import PRECEDENTS, bulk_answers, general_plan

SCORES = {"addresses_question": 4, "evidence": 4, "thoroughness": 4}
OLD_GENERIC = {"verdict": "accept", "scores": SCORES, "issues": []}
OLD_RESEARCH = {"verdict": "accept", "issues": []}


def row(item_id, status, evidence="s1 outputs/result1.tsv"):
    return {"id": item_id, "status": status, "evidence": evidence}


def generic(rows=None, issues=None):
    return {**OLD_GENERIC, "issues": issues or [], **({"checklist": rows} if rows is not None else {})}


def issue(priority, problem, step_id="A", request="fix it"):
    return {"step_id": step_id, "priority": priority, "problem": problem, "request": request}


CATALOG = topic_checklists.load()
PLAN = general_plan(checklist=bulk_answers())  # batch, pairing, gene_set_test: step:A; independent_validation: n/a
PRECEDENT_RECORD = cso.normalize_precedents(PRECEDENTS)


def added(review, plan=PLAN, precedents=None, **kwargs):
    """The issues review_with_checklist adds to `review`."""
    out = review_with_checklist(review, plan, CATALOG, precedents, **kwargs)
    return [i for i in out["issues"] if i not in review["issues"]]


# ---------- the prompt asks, the schema takes the answer ----------

@pytest.mark.parametrize("prompt", [cso.REVIEW_PROMPT, cso.RESEARCH_REVIEW_PROMPT])
def test_both_review_prompts_ask_for_a_status_and_evidence_per_checklist_item(prompt):
    assert CHECKLIST_REVIEW_RULE in prompt
    for text in ("`checklist`", "(done, partly or not_done)", "`evidence`", "step id or output path",
                 "every listed checklist item and required precedent item", "labhq adds a P2 issue"):
        assert text in prompt


@pytest.mark.parametrize("schema, old", [(REVIEW_SCHEMA, OLD_GENERIC), (RESEARCH_LANE_REVIEW_SCHEMA, OLD_RESEARCH)])
def test_review_schemas_take_an_optional_checklist_and_old_reviews_stay_valid(schema, old):
    assert "checklist" in schema["properties"] and "checklist" not in schema["required"]
    assert valid_review(old, schema)  # a review saved before #586
    assert valid_review({**old, "checklist": []}, schema)
    assert valid_review({**old, "checklist": [row("batch", "partly"), row("qc", "not_done", "")]}, schema)
    for bad in ([{"id": "batch", "status": "maybe", "evidence": ""}],
                [{"id": "batch", "status": "done"}],
                [{"id": "batch", "status": "done", "evidence": "x", "extra": 1}],
                "batch: done"):
        assert not valid_review({**old, "checklist": bad}, schema)


def test_an_unreadable_checklist_costs_the_rows_not_the_review():
    for schema, old in ((REVIEW_SCHEMA, OLD_GENERIC), (RESEARCH_LANE_REVIEW_SCHEMA, OLD_RESEARCH)):
        broken = {**old, "checklist": [{"id": "batch", "status": "maybe", "evidence": ""}]}
        assert cso.lenient_review(broken, schema) == old
        assert cso.lenient_review(old, schema) is old
        assert cso.lenient_review({**old, "verdict": "maybe", "checklist": []}, schema) is None
        assert cso.lenient_review(None, schema) is None


# ---------- a miss on a step-answered check becomes a P2 issue ----------

@pytest.mark.parametrize("research", [False, True])
def test_a_partly_or_not_done_step_answered_check_becomes_a_p2_issue(research):
    rows = [row("batch", "not_done", "no batch term in the model"), row("pairing", "partly", "S1 pairs 3 of 5"),
            row("gene_set_test", "done"), row("independent_validation", "not_done", "no cohort")]
    base = {"verdict": "accept", "issues": []} if research else generic()
    review = review_with_checklist({**base, "checklist": rows}, PLAN, CATALOG, None, research=research)
    assert [(i["step_id"], i["priority"]) for i in review["issues"]] == [("A", "P2"), ("A", "P2")]
    batch, pairing = review["issues"]
    assert "bulk_rna_seq/batch" in batch["problem"] and "하지 않았다" in batch["problem"]
    assert "no batch term in the model" in batch["problem"] and "batch" in batch["request"]
    assert "bulk_rna_seq/pairing" in pairing["problem"] and "일부만 했다" in pairing["problem"]
    assert review["checklist"] == rows and review["verdict"] == "accept"
    # done adds nothing, and not_applicable is no miss: the plan said why the check does not apply
    assert not any("gene_set_test" in i["problem"] or "independent_validation" in i["problem"]
                   for i in review["issues"])
    schema = RESEARCH_LANE_REVIEW_SCHEMA if research else REVIEW_SCHEMA
    assert valid_review(review, schema)
    if research:
        assert (batch["claim_id"], batch["category"], batch["evidence_quote"]) == ("", "other",
                                                                                "no batch term in the model")


def test_a_p2_miss_does_not_turn_an_accept_into_revise():
    review = cso.with_p1_verdict(review_with_checklist(
        generic([row("batch", "not_done", "")]), PLAN, CATALOG, None))
    assert review["verdict"] == "accept" and [i["priority"] for i in review["issues"]] == ["P2"]


def test_a_required_precedent_item_is_a_check_too():
    plan = general_plan(checklist={**bulk_answers(), "precedent.1": "step:A"})
    found = added(generic([row("precedent.1", "not_done", "no paired test in outputs/")]), plan, PRECEDENT_RECORD)
    assert [i["step_id"] for i in found] == ["A"] and "precedent.1" in found[0]["problem"]


def test_a_check_answered_assumption_or_not_applicable_adds_no_issue():
    plan = general_plan(checklist={**bulk_answers(), "batch": "assumption: 처리 날짜가 기록되지 않았다"})
    rows = [row("batch", "not_done", "no date"), row("independent_validation", "not_done", "none")]
    assert added(generic(rows), plan) == []


def test_an_existing_p1_or_p2_issue_that_names_the_check_stands_in_for_it():
    rows = [row("batch", "not_done")]
    for existing in (issue("P1", "batch is not modelled"), issue("P2", "the batch를 확인하지 않았다"),
                     issue("P2", "see `bulk_rna_seq/batch`", step_id="B")):
        assert added(generic(rows, [existing])) == []
    # P3 is wording only, and another check's text is no stand-in
    assert len(added(generic(rows, [issue("P3", "batch is not modelled")]))) == 1
    assert len(added(generic(rows, [issue("P2", "batch_effect plot is blurry")]))) == 1
    assert len(added(generic(rows, [issue("P2", "weak evidence overall")]))) == 1


def test_a_reviewer_row_may_spell_the_id_with_its_topic_and_a_second_row_cannot_hide_a_miss():
    rows = [row("bulk_rna_seq.batch", "not_done", "none"), row("bulk_rna_seq/batch", "done", "S1"),
            row("unlisted_check", "not_done", "not a required check")]
    found = added(generic(rows))
    assert len(found) == 1 and "bulk_rna_seq/batch" in found[0]["problem"]


def test_a_shared_id_names_every_owning_step_and_files_the_issue_on_the_first():
    answers = {key: value for key, value in bulk_answers().items() if key != "batch"}
    plan = general_plan(topics=["bulk_rna_seq", "single_cell_rna_seq"],
                        checklist={**answers, "bulk_rna_seq.batch": "step:S5", "single_cell_rna_seq.batch": "step:S4"})
    found = added(generic([row("batch", "partly", "only the bulk arm")]), plan)
    assert [i["step_id"] for i in found] == ["S5"] and "S5, S4" in found[0]["problem"]


def test_a_review_without_checklist_rows_is_unchanged():
    review = generic()
    assert review_with_checklist(review, PLAN, CATALOG, None) is review
    assert review_with_checklist(generic([]), PLAN, CATALOG, None)["issues"] == []


# ---------- the appendix table ----------

def test_the_table_names_each_check_with_its_owning_step_verdict_and_evidence():
    plan = general_plan(checklist={**bulk_answers(), "pairing": "assumption: 짝 정보가 없다",
                                   "precedent.1": "step:A"})
    required = cso.required_checklist_items(plan, CATALOG, PRECEDENT_RECORD)
    rows = [row("batch", "done", "A outputs/batch.png"), row("gene_set_test", "partly", "uses | and\nnewline"),
            row("independent_validation", "not_done", "no cohort")]
    table = topic_checklists.report_table(plan["checklist"], required, rows).splitlines()
    assert table[:2] == ["| 항목 | 담당 단계 | 리뷰 판정 | 근거 |", "| --- | --- | --- | --- |"]
    assert table[2:] == [
        "| bulk_rna_seq/batch | A | 했다 | A outputs/batch.png |",
        "| bulk_rna_seq/pairing | 가정 | - | 짝 정보가 없다 |",
        "| bulk_rna_seq/gene_set_test | A | 일부 | uses \\| and newline |",
        "| bulk_rna_seq/independent_validation | 해당 없음 | - | no cohort |",
        "| precedent.1 | A | - | - |",
    ]
    assert topic_checklists.report_table(plan["checklist"], [], rows) == ""


def test_the_table_lists_checks_even_when_no_reviewer_answered():
    plan = general_plan(checklist=bulk_answers())
    required = cso.required_checklist_items(plan, CATALOG, None)
    table = topic_checklists.report_table(plan["checklist"], required, None)
    assert "| bulk_rna_seq/batch | A | - | - |" in table
    section = cso.checklist_appendix(plan, {"status": "review_unparsed"}, CATALOG, None)
    assert section.startswith("점검표") and "| bulk_rna_seq/pairing | A | - | - |" in section
    assert cso.checklist_appendix(general_plan(topics=[]), None, CATALOG, None) == ""


# ---------- end to end ----------

CHECK_PLAN = general_plan(checklist=bulk_answers())


@pytest.mark.asyncio
async def test_general_report_appendix_carries_the_table_and_the_miss_is_a_p2_in_the_report():
    rows = [row("batch", "not_done", "no batch term in the model"), row("pairing", "done", "A outputs/pairs.tsv"),
            row("gene_set_test", "done", "A"), row("independent_validation", "not_done", "no cohort")]

    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(CHECK_PLAN))
        if kind == "step":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="A done")
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=generic(rows))
        assert kind == "synthesis", kind
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="## 결론과 권고\nbody")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.precedent_agent = None
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]

    assert req["status"] == "done", req.get("error")
    review_prompt = next(t.prompt for t in hub.calls if t.meta["kind"] == "review")
    assert CHECKLIST_REVIEW_RULE in review_prompt and "- batch:" in review_prompt
    assert [(i["step_id"], i["priority"]) for i in req["review"]["issues"]] == [("A", "P2")]
    assert req["review"]["verdict"] == "accept" and req["review"]["checklist"] == rows
    assert "P2 · A: 점검 bulk_rna_seq/batch" in req["report"]
    appendix = req["report_appendix"]
    assert "| bulk_rna_seq/batch | A | 안 했다 | no batch term in the model |" in appendix
    assert "| bulk_rna_seq/pairing | A | 했다 | A outputs/pairs.tsv |" in appendix
    assert "| bulk_rna_seq/independent_validation | 해당 없음 | - | no cohort |" in appendix
    assert "점검표" not in req["report"]  # the table is part of the execution record, not the PI body


@pytest.mark.asyncio
async def test_research_review_checklist_reaches_the_issues_the_record_and_survives_a_restart():
    rows = [row("pseudobulk", "done", "s1 result1.tsv"), row("qc", "not_done", "no doublet step in s1"),
            row("batch", "partly", "s1 adjusts one batch variable")]
    hub = _hub(review={**ACCEPT, "checklist": rows})
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]

    assert _kinds(hub) == ["plan", "step", "review", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    review = req["research_contract"]["review"]
    assert review["verdict"] == "accept" and review["checklist"] == rows
    assert CHECKLIST_REVIEW_RULE in hub.calls[2].prompt and "- qc:" in hub.calls[2].prompt
    miss = [i for i in review["issues"] if "점검 " in i["problem"]]
    assert [(i["step_id"], i["priority"], i["claim_id"], i["category"]) for i in miss] == [
        ("s1", "P2", "", "other")] * 2
    assert any("single_cell_rna_seq/qc" in i["problem"] for i in miss)
    assert valid_review({k: review[k] for k in ("verdict", "issues", "checklist")}, RESEARCH_LANE_REVIEW_SCHEMA)
    assert "P2 · s1: 점검 single_cell_rna_seq/qc" in req["report"]
    assert "| single_cell_rna_seq/qc | s1 | 안 했다 | no doublet step in s1 |" in req["report_appendix"]
    assert "| single_cell_rna_seq/pseudobulk | s1 | 했다 | s1 result1.tsv |" in req["report_appendix"]
    saved = copy.deepcopy(review)

    _interrupt(hub)  # restarted after the review was saved: it is reused, with its rows
    calls = len(hub.calls)
    await Orchestrator(hub).run_request("r", resume=True)
    assert _kinds(hub, calls) == ["synthesis"]
    assert hub.requests["r"]["research_contract"]["review"] == saved
    assert "| single_cell_rna_seq/batch | s1 | 일부 | s1 adjusts one batch variable |" in \
        hub.requests["r"]["report_appendix"]


@pytest.mark.asyncio
async def test_research_review_without_checklist_rows_still_lists_the_checks_in_the_record():
    hub = _hub()  # the reviewer answered no row, as before #586
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "research_reported" and req["report"].startswith(REPORT)
    assert "| single_cell_rna_seq/qc | s1 | - | - |" in req["report_appendix"]
    assert not [i for i in req["research_contract"]["review"]["issues"] if "점검 " in i["problem"]]
