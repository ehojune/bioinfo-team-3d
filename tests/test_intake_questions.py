"""#36 PR A: structured clarifying questions reach the PI as buttons and return through the #34 answer path."""

import asyncio

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import PLAN_SCHEMA, Orchestrator, qa_text
from labhq.settings import Settings

STEP = {"id": "s1", "agent_id": "worker", "instruction": "run", "depends_on": []}
COHORT = {"question": "Which cohort?", "options": ["cases", "controls"], "allow_free_text": True, "depth": 60}
GENOME = {"question": "Which genome build?", "options": ["GRCh38", "GRCh37", "T2T"], "allow_free_text": False}


class Hub:
    def __init__(self, dispatch, note="Q1. b) controls\nQ2. a) GRCh38"):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.reviewer_agent = None
        self.requests = {"r": {"text": "question", "mode": "orchestrate"}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker")}
        self.events, self.calls, self.approvals = [], [], []
        self.note, self.reply = note, dispatch

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task, self)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        return {"approved": True, "note": self.note}

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


def ok(task, **kwargs):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, **kwargs)


def test_plan_schema_asks_for_options_free_text_and_depth():
    item = PLAN_SCHEMA["properties"]["clarifying_questions"]["items"]
    assert item["type"] == "object" and item["additionalProperties"] is False
    assert set(item["required"]) == {"question", "options", "allow_free_text"}
    assert item["properties"]["options"]["minItems"] == 2 and item["properties"]["options"]["maxItems"] == 4
    assert item["properties"]["depth"]["enum"] == [30, 60, 90]
    assert item["properties"]["allow_free_text"]["type"] == "boolean"


def test_normalize_questions_keeps_legacy_strings_and_repairs_bad_options():
    from labhq.intake import normalize_questions

    raw = ["  Legacy question?  ", "", 7, {"question": 3, "options": ["a", "b"]}, COHORT, GENOME,
           {"question": "One option only", "options": ["x"], "allow_free_text": False},
           {"question": "Too many", "options": ["1", "2", "3", "4", "5"], "allow_free_text": False},
           {"question": "Duplicate options", "options": ["same", " same ", "other"], "allow_free_text": False,
            "depth": 45}]
    assert normalize_questions(raw) == [
        {"question": "Legacy question?", "options": [], "allow_free_text": True},
        COHORT,
        GENOME,
        {"question": "One option only", "options": [], "allow_free_text": True},
        {"question": "Too many", "options": ["1", "2", "3", "4"], "allow_free_text": True},
        {"question": "Duplicate options", "options": ["same", "other"], "allow_free_text": False},
    ]
    assert normalize_questions(None) == [] and normalize_questions(7) == [] and normalize_questions("  ") == []


def test_malformed_question_shapes_still_wait_for_the_pi():
    from labhq.intake import normalize_questions

    # options that are not a list: a number used to raise TypeError and fail the whole request, a string
    # split into one button per letter, a dict into its keys. Each becomes a free-text question instead.
    raw = [{"question": "Which cohort?", "options": 5, "allow_free_text": False},
           {"question": "Which genome?", "options": "hg38", "allow_free_text": False},
           {"question": "Which test?", "options": {"wilcoxon": 1, "t": 2}, "allow_free_text": False}]
    assert normalize_questions(raw) == [{"question": q, "options": [], "allow_free_text": True}
                                        for q in ("Which cohort?", "Which genome?", "Which test?")]
    # A lone question outside a list is still asked; dropping it would run the plan without waiting.
    assert normalize_questions("Which cohort?") == [{"question": "Which cohort?", "options": [],
                                                    "allow_free_text": True}]
    assert normalize_questions(COHORT) == [COHORT]


@pytest.mark.asyncio
async def test_structured_questions_become_buttons_and_the_answer_reaches_the_replan():
    async def dispatch(task, hub):
        if task.meta["kind"] == "plan":
            replanned = "PI clarification (questions and answer):" in task.prompt
            return ok(task, structured={"steps": [STEP], "recruit": [], "notes": "",
                                        "clarifying_questions": [] if replanned else [COHORT, GENOME]})
        return ok(task, text="done")

    hub = Hub(dispatch)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req
    approval = hub.approvals[0]
    assert approval["kind"] == "clarify"
    assert approval["detail"]["questions"] == [COHORT, GENOME]  # the phone renders these as buttons
    assert "1. Which cohort? — a) cases / b) controls" in approval["summary"]
    entry = req["clarifications"][0]
    assert entry["questions"] == ["Which cohort?", "Which genome build?"]
    assert entry["question_details"] == [COHORT, GENOME]
    assert entry["answer"] == hub.note
    replan = [t for t in hub.calls if t.meta["kind"] == "plan"][1].prompt
    assert "Q1. Which cohort?\n    options: a) cases; b) controls\n    depth: about 60 minutes" in replan
    assert "Q2. Which genome build?\n    options: a) GRCh38; b) GRCh37; c) T2T\n    free text: no" in replan
    assert "PI answer: Q1. b) controls\nQ2. a) GRCh38" in replan
    questions = next(e for e in hub.events if e["type"] == "request.questions")["data"]
    assert questions["questions"] == ["Which cohort?", "Which genome build?"]
    assert questions["details"] == [COHORT, GENOME]


@pytest.mark.asyncio
async def test_legacy_questions_keep_the_old_record_and_still_get_a_free_text_card():
    async def dispatch(task, hub):
        if task.meta["kind"] == "plan":
            replanned = "PI clarification (questions and answer):" in task.prompt
            return ok(task, structured={"steps": [STEP], "clarifying_questions": [] if replanned else ["Which cohort?"]})
        return ok(task, text="done")

    hub = Hub(dispatch, note="cases")
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["clarifications"][0] == {"questions": ["Which cohort?"], "answer": "cases"}
    assert hub.approvals[0]["detail"]["questions"] == [
        {"question": "Which cohort?", "options": [], "allow_free_text": True}]


@pytest.mark.asyncio
async def test_replan_that_still_asks_records_question_text_not_objects():
    async def dispatch(task, hub):
        return ok(task, structured={"steps": [STEP], "clarifying_questions": [COHORT]})

    hub = Hub(dispatch, note="Q1. a) cases")
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert req["pending_questions"] == ["Which cohort?"]
    assert "- Which cohort?" not in req["report"]
    assert "- Which cohort?" in req["report_appendix"] and "{'question'" not in req["report_appendix"]


def test_qa_text_for_structured_and_legacy_entries():
    assert qa_text({"questions": ["Which cohort?"], "answer": "cases"}) == "Q1. Which cohort?\nPI answer: cases"
    text = qa_text({"questions": ["Which cohort?"], "question_details": [COHORT], "answer": "Q1. a) cases"})
    assert text == "Q1. Which cohort?\n    options: a) cases; b) controls\n    depth: about 60 minutes\nPI answer: Q1. a) cases"


def minimal_plan(questions):
    return {
        "schema_version": 2,
        "topics": [],
        "intake": {"work_kind": "research", "reason": "PI specified work_kind=research", "source": "explicit"},
        "brief": {"question": "What is in the sample?", "purpose": "inventory", "subject": "public counts",
                  "scope": "one table", "deliverables": ["table"], "completion_conditions": ["table written"],
                  "study_type": "exploratory"},
        "protocol": {"revision": 1, "analysis_unit": "sample", "selection_criteria": [], "exclusion_criteria": [],
                     "comparators": [], "primary_metrics": ["count"], "validation_methods": ["recount"],
                     "resource_limits": ["local"], "stop_conditions": ["missing input"],
                     "approval_conditions": ["CP1"], "data_boundaries": ["public only"],
                     "statistics": {"applicable": False, "reason": "descriptive"}},
        "pack_values": {}, "clarifying_questions": questions,
        "steps": [{"id": "s1", "agent_id": "worker", "instruction": "count", "phase": "analysis",
                   "claim_ids": [], "input_refs": [], "outputs": [], "checks": ["recount"],
                   "evidence_slots": [], "depends_on": []}],
        "recruit": [], "notes": "",
    }


def test_research_plan_questions_use_the_same_structure_without_changing_legacy_hashes():
    from labhq.research.contract import RESEARCH_PLAN_SCHEMA, ResearchPlan, canonical_plan_json

    structured = ResearchPlan.model_validate(minimal_plan([COHORT]))
    assert structured.clarifying_questions[0].options == ["cases", "controls"]
    with pytest.raises(ValueError):
        ResearchPlan.model_validate(minimal_plan([{"question": "One option", "options": ["x"],
                                                   "allow_free_text": True}]))
    legacy = minimal_plan(["Which cohort?"])
    assert '"clarifying_questions":["Which cohort?"]' in canonical_plan_json(legacy)
    schema = RESEARCH_PLAN_SCHEMA["properties"]["clarifying_questions"]["items"]
    assert any(option.get("type") == "string" for option in schema["anyOf"])
    assert any("$ref" in option for option in schema["anyOf"])


def test_approval_summary_is_bounded_for_many_long_questions():
    from labhq.intake import questions_summary

    many = [{"question": "Q" * 500, "options": ["o" * 200] * 4, "allow_free_text": True}] * 6
    assert len(questions_summary(many)) <= 2000


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(test_structured_questions_become_buttons_and_the_answer_reaches_the_replan())


def test_option_text_that_already_carries_a_letter_is_not_labelled_twice():
    # #331: "a) 승인" showed as "a) a) 승인" on the decision card and in the CLI summary.
    from labhq.intake import ClarifyingQuestion, normalize_questions, questions_summary

    raw = {"question": "설치할까요?", "options": ["a) 승인", "B) 보류", "(c) 거절", "A. thaliana"],
           "allow_free_text": False}
    [question] = normalize_questions([raw])
    assert question["options"] == ["승인", "보류", "거절", "A. thaliana"]
    summary = questions_summary([question])
    assert "a) 승인 / b) 보류 / c) 거절 / d) A. thaliana" in summary and "a) a)" not in summary
    assert ClarifyingQuestion.model_validate(raw).options == question["options"]
