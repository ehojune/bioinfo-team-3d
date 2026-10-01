"""Request intake (#36): structured clarifying questions.

A question carries 2-4 short options so the phone shows buttons, whether a free-text answer is allowed,
and an optional depth (about 30/60/90 minutes of work). Older plans used plain strings; those still work.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUESTION_DEPTHS = (30, 60, 90)
OPTION_LETTERS = "abcd"
MAX_SUMMARY_CHARS = 2000

CLARIFYING_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "allow_free_text": {"type": "boolean"},
        "depth": {"type": "integer", "enum": list(QUESTION_DEPTHS)},
    },
    "required": ["question", "options", "allow_free_text"],
}

QUESTION_RULE = ("Ask clarifying_questions only if an answer would change the plan: at most 4, each an object with "
                 "the question, 2-4 short options (shown as a/b/c/d buttons), allow_free_text (true when none of "
                 "the options may fit), and depth (about 30, 60 or 90 minutes of work) only when the question is "
                 "how deep to go.")


class ClarifyingQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=2, max_length=4)
    allow_free_text: bool
    depth: Literal[30, 60, 90] | None = None

    @field_validator("question")
    @classmethod
    def question_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value.strip()

    @field_validator("options")
    @classmethod
    def distinct_options(cls, value: list[str]) -> list[str]:
        options = [option.strip() for option in value]
        if any(not option or len(option) > 200 for option in options):
            raise ValueError("options must be non-empty and at most 200 characters")
        if len(set(options)) != len(options):
            raise ValueError("options must be distinct")
        return options


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """Plain strings and malformed objects become questions the PI can still answer in free text."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            if item.strip():
                out.append({"question": item.strip(), "options": [], "allow_free_text": True})
            continue
        if not isinstance(item, dict) or not isinstance(item.get("question"), str) or not item["question"].strip():
            continue
        options = list(dict.fromkeys(option.strip() for option in item.get("options") or []
                                     if isinstance(option, str) and option.strip()))
        free = item.get("allow_free_text") is not False
        if len(options) < 2:
            options, free = [], True
        elif len(options) > 4:
            options, free = options[:4], True  # the dropped choice can still be typed
        entry: dict[str, Any] = {"question": item["question"].strip(), "options": options, "allow_free_text": free}
        if type(item.get("depth")) is int and item["depth"] in QUESTION_DEPTHS:
            entry["depth"] = item["depth"]
        out.append(entry)
    return out


def has_structure(questions: list[dict[str, Any]]) -> bool:
    return any(q.get("options") or q.get("depth") or not q.get("allow_free_text", True) for q in questions)


def question_line(index: int, question: dict[str, Any]) -> str:
    line = f"{index}. {question['question']}"
    if question.get("options"):
        line += " — " + " / ".join(f"{OPTION_LETTERS[i]}) {option}" for i, option in enumerate(question["options"]))
    if question.get("depth"):
        line += f" [depth about {question['depth']} min]"
    return line


def questions_summary(questions: list[dict[str, Any]], header: str = "Please answer before work begins:") -> str:
    text = "\n".join([header, *(question_line(i, q) for i, q in enumerate(questions, 1))])
    return text if len(text) <= MAX_SUMMARY_CHARS else text[:MAX_SUMMARY_CHARS - 1] + "…"


def question_detail_lines(question: dict[str, Any]) -> list[str]:
    """What a re-plan needs to read the PI's "b)" as a concrete choice."""
    lines = []
    if question.get("options"):
        lines.append("    options: " + "; ".join(f"{OPTION_LETTERS[i]}) {option}"
                                                for i, option in enumerate(question["options"])))
    if question.get("depth"):
        lines.append(f"    depth: about {question['depth']} minutes")
    if question.get("options") and question.get("allow_free_text") is False:
        lines.append("    free text: no")
    return lines
