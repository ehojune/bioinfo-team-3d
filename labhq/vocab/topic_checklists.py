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
    """Return declared-topic requirements once, preserving file/topic order."""
    selected = set(topics) if isinstance(topics, list) else set()
    found: dict[str, ChecklistItem] = {}
    for topic, items in checklists.items():
        if topic in selected:
            for item in items:
                found.setdefault(item.id, item)
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
        "`step:<step id>`, `assumption: <one line>`, or `not_applicable: <reason>`.",
        "A topic with no listed items adds no checklist requirement.",
    ]
    return "\n".join(lines)


def answer_errors(answers: Any, required: list[ChecklistItem], step_ids: list[str]) -> list[str]:
    """Validate required answers and step references without rejecting unrelated extra keys."""
    if not required:
        return []
    body = answers if isinstance(answers, dict) else {}
    errors: list[str] = []
    known = set(step_ids)
    for item in required:
        value = body.get(item.id)
        if not isinstance(value, str) or not ANSWER.fullmatch(value.strip()):
            errors.append(f"checklist.{item.id} must answer step:<step id>, assumption: <one line>, or "
                          "not_applicable: <reason>")
            continue
        match = ANSWER.fullmatch(value.strip())
        if match and match.group(2) and match.group(2) not in known:
            errors.append(f"checklist.{item.id} refers to unknown step {match.group(2)}")
    return errors


def limitations(answers: Any) -> list[str]:
    """The answers that the PI report must name as one-line limitations."""
    if not isinstance(answers, dict):
        return []
    found = []
    for item_id, value in answers.items():
        if not isinstance(item_id, str) or not isinstance(value, str):
            continue
        for prefix in ("assumption:", "not_applicable:"):
            if value.startswith(prefix) and value[len(prefix):].strip():
                found.append(f"{item_id}: {value[len(prefix):].strip()}")
                break
    return sorted(found)
