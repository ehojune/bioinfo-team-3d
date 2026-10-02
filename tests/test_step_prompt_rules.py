"""Rules every step prompt carries from code, so they do not depend on the CSO passing them on (#84)."""

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
