"""Rules the plan and step prompts carry from code, so they do not depend on the CSO passing them on (#84)."""

import pytest

from labhq.intake import QUESTION_RULE
from labhq.orchestrator import cso
from labhq.orchestrator.cso import STEP_PROMPT


def _render() -> str:
    return " ".join(STEP_PROMPT.format(request="REQ", step_id="s1", instruction="INSTR").split())


def test_step_prompt_forbids_a_silent_fallback_and_asks_why_when_giving_up():
    text = _render()
    assert "Do not quietly switch to a weaker method when one fails" in text
    assert "keep debugging" in text and "say what you tried and why you stopped" in text


def test_blocking_decision_is_written_for_the_phone_card():
    text = _render()
    assert '"blocking_decision"' in text and "Do not proceed with the blocked work." in text
    assert "at most 700 characters" in text
    assert "the question itself in the first sentence" in text and "each choice on its own line" in text


def test_plan_questions_fit_the_phone_card_and_the_research_plan_keeps_its_own_rule():
    """Plan questions are structured (options are a/b/c/d buttons), so only the length and the order apply."""
    rule = QUESTION_RULE + " Each question must fit the PI's phone card: at most 700 characters, the question itself first."
    args = dict(request="REQ", roster="ROSTER", capabilities="CAPS", briefing="BRIEF", max_steps=3,
                question_rule=QUESTION_RULE, output_types_rule="")
    assert rule in cso.PLAN_PROMPT.format(**args)
    assert "phone card" not in cso.RESEARCH_PLAN_PROMPT.format(**args, intake="INTAKE", packs="PACKS")


def test_step_prompt_asks_for_escaped_line_breaks_inside_the_json_string():
    assert r"Inside the JSON string write each line break as \n." in _render()


# The phone-card layout puts choices on their own lines, and a step without an output schema may write those
# lines as real newlines inside the JSON string. Strict JSON refuses that, which dropped the question (#342 review).
CARD = 'Which group should I compare?\n- cases\n- controls'
RAW = '{"blocking_decision": "' + CARD + '"}'


def test_blocking_question_reads_real_newlines_inside_the_json_string():
    from labhq.models import TaskResult
    from labhq.util import extract_json

    assert extract_json(RAW) is None  # other callers keep strict JSON
    for text in (RAW, f"Stopped here.\n```json\n{RAW}\n```", f"Stopped here. {RAW}", RAW.replace("\n", "\t")):
        res = TaskResult(task_id="t", agent_id="worker", ok=True, text=text)
        assert cso.blocking_question(res) == (CARD if "\t" not in text else CARD.replace("\n", "\t"))


@pytest.mark.asyncio
async def test_a_card_question_with_real_newlines_reaches_the_pi_and_the_step_reruns():
    from tests.test_cso import STEPS, FakeHub, result

    steps = [{**STEPS[0], "outputs": ["outputs/a.tsv"]}, STEPS[1]]
    calls = []

    async def dispatch(task):
        calls.append(task)
        if task.meta["kind"] == "plan":
            return result(task, structured={"steps": steps})
        if task.meta.get("step_id") == "A" and "Your earlier blocking question and the PI's answer:" not in task.prompt:
            return result(task, text=RAW)
        return result(task, text="done", outputs=["outputs/a.tsv"] if task.meta.get("step_id") == "A" else [])

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None

    async def answer(**kwargs):
        hub.approvals.append(kwargs)
        return {"approved": True, "note": "cases"}
    hub.request_approval = answer
    await cso.Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done", hub.requests["r"]
    assert hub.approvals[0]["kind"] == "clarify" and CARD in hub.approvals[0]["summary"]
    assert [t.meta.get("step_id") for t in calls if t.meta["kind"] == "step"] == ["A", "A", "B"]
    assert hub.requests["r"]["step_decisions"]["A"]["question"] == CARD


def test_replan_questions_follow_the_same_phone_card_rule_as_the_plan():
    args = dict(roster="ROSTER", capabilities="CAPS", trigger="WHY", retired="none", drop_rule="DROP", used="A",
                max_steps=3, output_types_rule="", empty_rule="EMPTY", request="REQ", plan="[]", results="")
    text = cso.REPLAN_PROMPT.format(**args)
    assert ("ask in clarifying_questions and do not plan the blocked work. " + cso.PI_CARD_QUESTION_RULE) in text
    assert cso.PI_CARD_QUESTION_RULE in cso.PLAN_PROMPT
    assert "at most 700 characters, the question itself first" in cso.PI_CARD_QUESTION_RULE


def test_research_plan_questions_carry_the_length_their_validation_enforces():
    """A research plan question over ClarifyingQuestion's limit fails plan validation, so the prompt states it."""
    from labhq.intake import ClarifyingQuestion

    limit = next(m.max_length for m in ClarifyingQuestion.model_fields["question"].metadata
                 if hasattr(m, "max_length"))
    rule = (f"Each question is at most {limit} characters (a longer one fails plan validation), "
            "the question itself first.")
    args = dict(request="REQ", roster="ROSTER", capabilities="CAPS", briefing="BRIEF", max_steps=3,
                question_rule=QUESTION_RULE, output_types_rule="", intake="INTAKE", packs="PACKS")
    for template in (cso.RESEARCH_PLAN_PROMPT, cso.RESEARCH_CP2_PLAN_PROMPT):
        assert rule in " ".join(template.format(**args).split())
    assert ClarifyingQuestion(question="q" * limit, options=["a", "b"], allow_free_text=False)
