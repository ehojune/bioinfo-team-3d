"""Rules the plan and step prompts carry from code, so they do not depend on the CSO passing them on (#84)."""

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
