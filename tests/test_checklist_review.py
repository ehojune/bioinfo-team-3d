"""The reviewers answer each declared checklist item and the report names them (#586, item 3).

In the 10-13 PI trial both reviewers received the topic checklist and the plan's answers, but no finding cited a
check, and the report never named one: only assumption and not_applicable answers reached it."""

from __future__ import annotations

import copy
import re

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import (CHECKLIST_REVIEW_RULE, REVIEW_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, Orchestrator,
                                    review_with_checklist, valid_review)
from labhq.vocab import topic_checklists
from tests.test_cso import FakeHub
from tests.test_research_cp2 import _interrupt
from tests.test_research_report import ACCEPT, REPORT, _hub, _kinds
from tests.test_solo_route import SoloHub, plan as solo_plan
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


# ---------- one check answered per topic (PR #600 review) ----------

def per_topic_plan(bulk, single_cell):
    """The 10-13 trial shape: `batch` is a check of two declared topics and the plan answers it once per topic."""
    others = {key: value for key, value in bulk_answers().items() if key != "batch"}
    return general_plan(topics=["bulk_rna_seq", "single_cell_rna_seq"], checklist={
        **others, "bulk_rna_seq.batch": bulk, "single_cell_rna_seq.batch": single_cell})


def test_each_topics_verdict_of_a_shared_id_stays_with_its_own_answer():
    plan = per_topic_plan("step:S5", "step:S4")
    done_bulk = [row("bulk_rna_seq.batch", "done", "S5 outputs/batch.png"),
                 row("single_cell_rna_seq.batch", "not_done", "no batch term in S4")]
    found = added(generic(done_bulk), plan)
    assert [(i["step_id"], i["priority"]) for i in found] == [("S4", "P2")]
    assert "single_cell_rna_seq/batch" in found[0]["problem"] and "no batch term in S4" in found[0]["problem"]
    assert "S5" not in found[0]["problem"] + found[0]["request"]
    # the other way round, and a miss in both topics: one issue per topic, each on its own step
    swapped = [row("bulk_rna_seq/batch", "partly", "S5 pairs one arm"), row("single_cell_rna_seq/batch", "done", "S4")]
    found = added(generic(swapped), plan)
    assert [i["step_id"] for i in found] == ["S5"] and "bulk_rna_seq/batch" in found[0]["problem"]
    both = [row("bulk_rna_seq.batch", "not_done", "none"), row("single_cell_rna_seq.batch", "partly", "one")]
    assert [i["step_id"] for i in added(generic(both), plan)] == ["S5", "S4"]


def test_a_miss_is_linked_to_the_step_answer_of_its_own_topic_only():
    # one topic answered step, the other assumption: the assumption topic's verdict never reaches the step answer
    plan = per_topic_plan("step:S5", "assumption: 처리 날짜가 기록되지 않았다")
    rows = [row("bulk_rna_seq.batch", "done", "S5"), row("single_cell_rna_seq.batch", "not_done", "no date")]
    assert added(generic(rows), plan) == []
    rows = [row("bulk_rna_seq.batch", "not_done", "no batch term"), row("single_cell_rna_seq.batch", "done", "-")]
    found = added(generic(rows), plan)
    assert [i["step_id"] for i in found] == ["S5"] and "bulk_rna_seq/batch" in found[0]["problem"]


def test_a_bare_id_row_covers_only_the_topics_without_a_row_of_their_own():
    plan = per_topic_plan("step:S5", "step:S4")
    rows = [row("batch", "not_done", "shared"), row("bulk_rna_seq.batch", "done", "S5")]
    found = added(generic(rows), plan)
    assert [i["step_id"] for i in found] == ["S4"] and "bulk_rna_seq" not in found[0]["problem"]


def test_a_bare_answer_for_a_shared_id_still_takes_the_worse_of_the_topic_rows():
    plan = general_plan(topics=["bulk_rna_seq", "single_cell_rna_seq"], checklist={"batch": "step:S1"})
    rows = [row("bulk_rna_seq.batch", "done", "S1"), row("single_cell_rna_seq.batch", "partly", "one arm")]
    found = added(generic(rows), plan)
    assert [i["step_id"] for i in found] == ["S1"] and "one arm" in found[0]["problem"]


def test_an_issue_about_one_topics_alias_does_not_stand_in_for_the_other_topics_miss():
    plan = per_topic_plan("step:S5", "step:S4")
    rows = [row("bulk_rna_seq.batch", "not_done", "none"), row("single_cell_rna_seq.batch", "not_done", "none")]
    existing = issue("P2", "single_cell_rna_seq/batch is not modelled", step_id="S4")
    found = added(generic(rows, [existing]), plan)
    assert [i["step_id"] for i in found] == ["S5"]
    # a qualified spelling with a dot counts too, and an issue naming both stands in for both
    assert [i["step_id"] for i in added(generic(rows, [issue("P1", "bulk_rna_seq.batch is missing")]), plan)] == ["S4"]
    both = issue("P2", "bulk_rna_seq/batch and single_cell_rna_seq/batch are missing")
    assert added(generic(rows, [both]), plan) == []


def test_the_table_has_one_row_per_topic_answer_of_a_shared_id():
    rows = [row("bulk_rna_seq.batch", "done", "S5 outputs/batch.png"),
            row("single_cell_rna_seq/batch", "not_done", "no batch term in S4")]
    plan = per_topic_plan("step:S5", "step:S4")
    required = cso.required_checklist_items(plan, CATALOG, None)
    lines = topic_checklists.report_table(plan["checklist"], required, rows).splitlines()
    assert "| bulk_rna_seq/batch | S5 | 했다 | S5 outputs/batch.png |" in lines
    assert "| single_cell_rna_seq/batch | S4 | 안 했다 | no batch term in S4 |" in lines
    assert not any("bulk_rna_seq,single_cell_rna_seq" in line for line in lines)
    mixed = per_topic_plan("step:S5", "assumption: 처리 날짜가 기록되지 않았다")
    lines = topic_checklists.report_table(mixed["checklist"], required, rows).splitlines()
    assert "| bulk_rna_seq/batch | S5 | 했다 | S5 outputs/batch.png |" in lines
    assert "| single_cell_rna_seq/batch | 가정 | 안 했다 | no batch term in S4 |" in lines
    section = cso.checklist_appendix(plan, generic(rows), CATALOG, None)
    assert "| single_cell_rna_seq/batch | S4 | 안 했다 | no batch term in S4 |" in section


# ---------- a solo review belongs to the solo result, not to plan step A (PR #600 review) ----------

def test_a_solo_review_miss_is_filed_on_the_solo_result_and_never_on_a_plan_step():
    rows = [row("batch", "not_done", "no batch term"), row("pairing", "partly", "3 of 5"), row("gene_set_test", "done")]
    out = review_with_checklist(generic(rows), PLAN, CATALOG, None, owner=cso.SOLO_OWNER)
    assert [(i["step_id"], i["priority"]) for i in out["issues"]] == [(cso.SOLO_OWNER, "P2")] * 2
    for found in out["issues"]:
        assert "A 단계" not in found["problem"] + found["request"] and cso.SOLO_OWNER in found["problem"]
    assert "bulk_rna_seq/batch" in out["issues"][0]["problem"] and "no batch term" in out["issues"][0]["problem"]
    assert valid_review(out, REVIEW_SCHEMA)
    required = cso.required_checklist_items(PLAN, CATALOG, None)
    lines = topic_checklists.report_table(PLAN["checklist"], required, rows, owner=cso.SOLO_OWNER).splitlines()
    assert f"| bulk_rna_seq/batch | {cso.SOLO_OWNER} | 안 했다 | no batch term |" in lines
    assert "| bulk_rna_seq/independent_validation | 해당 없음 | - | no independent public cohort |" in lines
    assert not any("| A |" in line for line in lines)


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


SOLO_ROWS = [row("batch", "not_done", "no batch term in the result"), row("pairing", "done", "answer.md"),
             row("gene_set_test", "partly", "one gene set only"), row("independent_validation", "not_done", "no cohort")]


@pytest.mark.asyncio
@pytest.mark.parametrize("solo_review", [True, False])
async def test_solo_report_attributes_the_checklist_to_the_solo_result_not_to_plan_step_a(solo_review):
    hub = SoloHub(planned={**solo_plan(), "topics": ["bulk_rna_seq"], "checklist": bulk_answers()})
    hub.s.orchestrator.precedent_agent = None
    hub.s.orchestrator.solo_review = solo_review
    if solo_review:
        hub.s.orchestrator.reviewer_agent = "reviewer"
        hub.agents["reviewer"] = {"id": "reviewer", "name": "reviewer", "role": "test", "engine": "mock"}
    original = hub.dispatch

    async def dispatch(task):
        if task.meta.get("kind") == "review":
            hub.calls.append(task)
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=generic(SOLO_ROWS))
        return await original(task)

    hub.dispatch = dispatch
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]

    assert [task.meta.get("kind") for task in hub.calls] == (
        ["plan", "direct", "review"] if solo_review else ["plan", "direct"])
    assert req["status"] == "done" and req["route_decision"]["mode"] == "solo"
    appendix = req["report_appendix"]
    assert "| bulk_rna_seq/batch | A |" not in appendix  # step A never ran: the solo turn did
    assert f"| bulk_rna_seq/batch | {cso.SOLO_OWNER} |" in appendix
    assert "| bulk_rna_seq/independent_validation | 해당 없음 |" in appendix
    if not solo_review:
        assert "| bulk_rna_seq/batch | 단독 처리 | - | - |" in appendix
        assert "점검 " not in req["report"]
        return
    issues = req["solo_review"]["issues"]
    assert not [found for found in issues if found["step_id"] == "A"]
    assert [(found["step_id"], found["priority"]) for found in issues] == [(cso.SOLO_OWNER, "P2")] * 2
    assert f"P2 · {cso.SOLO_OWNER}: 점검 bulk_rna_seq/batch" in req["report"]
    assert "| bulk_rna_seq/batch | 단독 처리 | 안 했다 | no batch term in the result |" in appendix
    assert "| bulk_rna_seq/pairing | 단독 처리 | 했다 | answer.md |" in appendix
    assert req["review"]["verdict"] == "accept"


# ---------- the reviewer is shown the ids it must answer (PR #602 review) ----------

LISTED_ID = re.compile(r"(?m)^- ([a-z][a-z0-9_.-]*): ")


def listed_ids(prompt):
    """The ids in the checklist block of a review prompt: a reviewer can only answer exactly what is listed there."""
    block = prompt.split("Declared topic checklist and plan answers:", 1)[1].split("\nAnswers: ", 1)[0]
    return LISTED_ID.findall(block)


def listed_rows(prompt, not_done=()):
    """A reviewer that follows CHECKLIST_REVIEW_RULE literally: one row per listed id, none for anything else."""
    return [row(item_id, "not_done" if item_id in not_done else "done", f"{item_id} evidence")
            for item_id in listed_ids(prompt)]


def check_of(topic, item_id):
    return next(item for item in CATALOG[topic] if item.id == item_id)


def test_the_review_context_lists_a_shared_id_once_per_topic_with_its_own_check_and_answer():
    context = cso.plan_review_context(per_topic_plan("step:S5", "step:S4"), CATALOG, None)
    ids = listed_ids(context)
    assert "bulk_rna_seq.batch" in ids and "single_cell_rna_seq.batch" in ids and "batch" not in ids
    bulk, single_cell = check_of("bulk_rna_seq", "batch"), check_of("single_cell_rna_seq", "batch")
    assert bulk.check != single_cell.check  # each topic keeps its own check, not the joined text
    assert f"- bulk_rna_seq.batch: {bulk.check} Why: {bulk.why} — 계획 답: step:S5\n" in context
    assert f"- single_cell_rna_seq.batch: {single_cell.check} Why: {single_cell.why} — 계획 답: step:S4\n" in context
    assert " / " not in context.split("\nAnswers: ", 1)[0]
    mixed = cso.plan_review_context(per_topic_plan("step:S5", "assumption: 처리 날짜가 없다"), CATALOG, None)
    assert "- single_cell_rna_seq.batch: " in mixed and "계획 답: assumption: 처리 날짜가 없다" in mixed


def test_the_review_context_keeps_the_bare_id_for_unshared_checks_and_for_a_bare_plan_answer():
    context = cso.plan_review_context(general_plan(checklist=bulk_answers()), CATALOG, None)
    assert listed_ids(context) == [item.id for item in CATALOG["bulk_rna_seq"]] and "계획 답" not in context
    both = general_plan(topics=["bulk_rna_seq", "single_cell_rna_seq"], checklist={"batch": "step:S1"})
    ids = listed_ids(cso.plan_review_context(both, CATALOG, None))
    assert ids.count("batch") == 1 and "bulk_rna_seq.batch" not in ids
    plan = general_plan(checklist={**bulk_answers(), "precedent.1": "step:A"})
    ids = listed_ids(cso.plan_review_context(plan, CATALOG, PRECEDENT_RECORD))
    assert "precedent.1" in ids and "batch" in ids


@pytest.mark.parametrize("prompt", [cso.REVIEW_PROMPT, cso.RESEARCH_REVIEW_PROMPT], ids=["review", "research"])
def test_both_review_prompts_ask_for_one_entry_per_listed_topic_id(prompt):
    assert "using its id exactly as listed" in prompt
    assert "listed once per topic as `topic.id` gets one entry per listed id" in prompt.replace("\n", " ")


def test_a_reviewer_that_answers_exactly_the_listed_ids_gets_per_topic_verdicts_and_no_false_p2():
    plan = per_topic_plan("step:S5", "step:S4")
    prompt = cso.REVIEW_PROMPT.format(request="q", results="S4, S5 done") + cso.plan_review_context(plan, CATALOG, None)
    rows = listed_rows(prompt, not_done={"single_cell_rna_seq.batch"})
    assert {"bulk_rna_seq.batch", "single_cell_rna_seq.batch"} <= {entry["id"] for entry in rows}
    parsed = cso.lenient_review({**OLD_GENERIC, "checklist": rows}, REVIEW_SCHEMA)
    review = review_with_checklist(parsed, plan, CATALOG, None)
    assert [(i["step_id"], i["priority"]) for i in review["issues"]] == [("S4", "P2")]
    assert "single_cell_rna_seq/batch" in review["issues"][0]["problem"]
    assert "batch" not in listed_ids(prompt)  # no bare line to answer once for both topics


TRIAL_ANSWERS = {"bulk_rna_seq.batch": "step:S5", "pairing": "step:S5", "gene_set_test": "step:S5",
                 "independent_validation": "not_applicable: no independent public cohort",
                 "single_cell_rna_seq.batch": "step:S4", "pseudobulk": "step:S4", "qc": "step:S4"}


def trial_plan():
    """The 10-13 trial: two declared topics, `batch` answered by S5 (bulk) and by S4 (single cell)."""
    plan = general_plan(topics=["bulk_rna_seq", "single_cell_rna_seq"], checklist=dict(TRIAL_ANSWERS))
    plan["steps"] = [{"id": step, "agent_id": "worker", "instruction": "analyze", "outputs": [], "depends_on": []}
                     for step in ("S4", "S5")]
    return plan


@pytest.mark.asyncio
async def test_the_real_review_path_files_a_shared_id_miss_on_its_own_topics_step_only():
    prompts = []

    async def dispatch(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=trial_plan())
        if kind == "step":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=f"{task.meta['step_id']} done")
        if kind == "review":
            prompts.append(task.prompt)
            rows = listed_rows(task.prompt, not_done={"single_cell_rna_seq.batch"})
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=generic(rows))
        assert kind == "synthesis", kind
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="## 결론과 권고\nbody")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.precedent_agent = None
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]

    assert req["status"] == "done", req.get("error")
    assert len(prompts) == 1 and CHECKLIST_REVIEW_RULE in prompts[0]
    ids = listed_ids(prompts[0])
    assert "bulk_rna_seq.batch" in ids and "single_cell_rna_seq.batch" in ids and "batch" not in ids
    assert {entry["id"] for entry in req["review"]["checklist"]} == set(ids)
    assert [(i["step_id"], i["priority"]) for i in req["review"]["issues"]] == [("S4", "P2")]
    assert "S5" not in req["review"]["issues"][0]["problem"] + req["review"]["issues"][0]["request"]
    appendix = req["report_appendix"]
    assert "| bulk_rna_seq/batch | S5 | 했다 | bulk_rna_seq.batch evidence |" in appendix
    assert "| single_cell_rna_seq/batch | S4 | 안 했다 | single_cell_rna_seq.batch evidence |" in appendix
    assert "P2 · S4: 점검 single_cell_rna_seq/batch" in req["report"] and "P2 · S5" not in req["report"]
