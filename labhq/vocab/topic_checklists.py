"""Topic-scoped analysis checks injected into plans, reviews and reports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..yaml_unique import UniqueKeyError, load_yaml_unique
from . import Vocab, current


CHECKLIST_FILE = Path(__file__).with_name("topic_checklists.yaml")
CHECK_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ANSWER = re.compile(r"^(step:([^\s]+)|assumption:\s*\S.+|not_applicable:\s*\S.+)$")
SKIP_PREFIXES = ("assumption:", "not_applicable:")
SKIP_WARNING = "점검 못 함"
# A skip must say why (PI 2026-10-07, #446). These stand in for a reason without giving one; compared after
# lowercasing and dropping everything but letters and digits, so "N/A", "n.a." and "- " all match.
PLACEHOLDER_REASONS = frozenset({
    "", "na", "none", "null", "nil", "tbd", "todo", "unknown", "skip", "skipped", "notapplicable", "assumption",
    "없음", "해당없음", "모름", "미정", "생략",
})


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    check: str
    why: str
    topic: str


def load(path: Path | None = None, vocab: Vocab | None = None) -> dict[str, list[ChecklistItem]]:
    """Load the small curated file and reject keys outside the approved topic vocabulary."""
    source = Path(path) if path is not None else CHECKLIST_FILE
    try:
        raw = load_yaml_unique(source.read_text(encoding="utf-8"), source.name)
    except UniqueKeyError as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source.name}: needs a topic mapping")
    approved = vocab or current()
    if approved is None:
        raise ValueError("topic vocabulary is unavailable")
    unknown = sorted(str(key) for key in raw if not approved.is_key("topic", key))
    if unknown:
        raise ValueError(f"unknown topic checklist key: {', '.join(unknown)}")
    loaded: dict[str, list[ChecklistItem]] = {}
    for topic, rows in raw.items():
        if not isinstance(rows, list):
            raise ValueError(f"topic checklist {topic}: items must be a list")
        items: list[ChecklistItem] = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"id", "check", "why"}:
                raise ValueError(f"topic checklist {topic}: each item needs id, check, why")
            item_id = row.get("id")
            check, why = row.get("check"), row.get("why")
            if not isinstance(item_id, str) or not CHECK_ID.fullmatch(item_id):
                raise ValueError(f"topic checklist {topic}: invalid id")
            if not isinstance(check, str) or not check.strip() or not isinstance(why, str) or not why.strip():
                raise ValueError(f"topic checklist {topic}/{item_id}: check and why are required")
            items.append(ChecklistItem(item_id, check.strip(), why.strip(), str(topic)))
        if len({item.id for item in items}) != len(items):
            raise ValueError(f"topic checklist {topic}: duplicate item id")
        loaded[str(topic)] = items
    return loaded


def requirements(topics: Any, checklists: Mapping[str, list[ChecklistItem]]) -> list[ChecklistItem]:
    """Return declared-topic requirements once per id, preserving file/topic order.

    Two declared topics can list one id with different checks (``library_qc`` for ATAC and ChIP). The plan answers
    an id once, so the checks are joined instead of the later topic's check being dropped."""
    selected = set(topics) if isinstance(topics, list) else set()
    found: dict[str, ChecklistItem] = {}
    for topic, items in checklists.items():
        if topic in selected:
            for item in items:
                prior = found.get(item.id)
                if prior is None:
                    found[item.id] = item
                elif item.check not in prior.check.split(" / "):
                    found[item.id] = ChecklistItem(item.id, f"{prior.check} / {item.check}",
                                                   f"{prior.why} / {item.why}", f"{prior.topic},{item.topic}")
    return list(found.values())


def prompt_rule(checklists: Mapping[str, list[ChecklistItem]]) -> str:
    """Show all currently curated topic lists because the CSO declares topics in the same response.

    Only the checks: every plan prompt carries the whole catalog, so the reasons go to the reviewer, who sees
    them for the declared topics only (plan_review_context)."""
    lines = ["\n\nTopic checklists:"]
    for topic, items in checklists.items():
        if items:
            lines.append(f"- {topic}:")
            lines.extend(f"  - {item.id}: {item.check}" for item in items)
    lines += [
        "For every item under each topic you declare, answer top-level `checklist` with exactly one of:",
        "`step:<step id>`, `assumption: <why it could not be done>`, or `not_applicable: <why it does not apply>`.",
        "Do every applicable check the data allow. When a check applies but cannot be done (e.g. no independent",
        "cohort exists), answer `assumption: <why it could not be done>`: a justified skip is acceptable, becomes a",
        "stated limitation and is shown to the PI as a warning. Never skip silently or with an empty reason.",
        "Use not_applicable only when the check does not apply to this request.",
        "A topic with no listed items adds no checklist requirement.",
    ]
    return "\n".join(lines)


def skip_reason(value: Any) -> str | None:
    """The reason of an assumption/not_applicable answer, "" when it gives none, None for any other answer."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    for prefix in SKIP_PREFIXES:
        if text.startswith(prefix):
            reason = text[len(prefix):].strip()
            return "" if _placeholder(reason) else reason
    return None


def _placeholder(reason: str) -> bool:
    return "".join(ch for ch in reason.lower() if ch.isalnum()) in PLACEHOLDER_REASONS


def answer_errors(answers: Any, required: list[ChecklistItem], step_ids: list[str]) -> list[str]:
    """Validate required answers and step references without rejecting unrelated extra keys."""
    if not required:
        return []
    body = answers if isinstance(answers, dict) else {}
    errors: list[str] = []
    known = set(step_ids)
    for item in required:
        value = body.get(item.id)
        if skip_reason(value) == "":
            errors.append(f"checklist.{item.id} must answer with a reason: assumption: <why it could not be done> "
                          "or not_applicable: <why it does not apply>")
            continue
        if not isinstance(value, str) or not ANSWER.fullmatch(value.strip()):
            errors.append(f"checklist.{item.id} must answer step:<step id>, assumption: <why it could not be "
                          "done>, or not_applicable: <why it does not apply>")
            continue
        match = ANSWER.fullmatch(value.strip())
        if match and match.group(2) and match.group(2) not in known:
            errors.append(f"checklist.{item.id} refers to unknown step {match.group(2)}")
    return errors


def label(item: ChecklistItem) -> str:
    """topic/id for the PI; a precedent item's id already names its source (precedent.1)."""
    return item.id if item.id.startswith(f"{item.topic}.") else f"{item.topic}/{item.id}"


def skipped(answers: Any, required: list[ChecklistItem]) -> list[tuple[str, str]]:
    """(topic/id, reason) for each required check the plan will not do: an assumption answer with a real reason.

    One per item, in requirement order. not_applicable is not here: it does not apply, nothing was left undone."""
    body = answers if isinstance(answers, dict) else {}
    found = []
    for item in required:
        value = body.get(item.id)
        reason = skip_reason(value)
        if reason and value.strip().startswith("assumption:"):
            found.append((label(item), reason))
    return found


def skip_warnings(answers: Any, required: list[ChecklistItem]) -> list[str]:
    """One plan warning per check not done, so the PI sees each during the run (PI 2026-10-07, #446)."""
    return [f"{SKIP_WARNING} {name}: {reason}" for name, reason in skipped(answers, required)]


def limitations(answers: Any) -> list[str]:
    """The answers the PI report must name as one-line limitations: checks that apply but rest on an assumption.

    A not_applicable check is not a limitation (bench C, #373); not_applicable() lists those."""
    if not isinstance(answers, dict):
        return []
    found = []
    for item_id, value in answers.items():
        if isinstance(item_id, str) and isinstance(value, str) and value.startswith("assumption:"):
            reason = skip_reason(value)
            if reason:
                found.append(f"{item_id}: {reason}")
    return sorted(found)


def not_applicable(answers: Any) -> list[str]:
    """The ids answered not_applicable: checks that do not apply to this request."""
    if not isinstance(answers, dict):
        return []
    return sorted(item_id for item_id, value in answers.items()
                  if isinstance(item_id, str) and isinstance(value, str) and value.startswith("not_applicable:")
                  and skip_reason(value))
