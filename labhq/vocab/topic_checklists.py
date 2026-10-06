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
CHECKLIST_TSV = "topic_checklists.tsv"  # written in the planner's work folder (runner REFERENCE_FILE rule)
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


def _cell(text: str) -> str:
    return " ".join(str(text).split())  # one TSV cell: no tab or line break


def table(checklists: Mapping[str, list[ChecklistItem]]) -> str:
    """The whole catalog as the TSV the plan reads from its work folder (topic, id, check, why)."""
    rows = ["topic\tid\tcheck\twhy"]
    rows += ["\t".join(_cell(value) for value in (topic, item.id, item.check, item.why))
             for topic, items in checklists.items() for item in items]
    return "\n".join(rows) + "\n"


def workspace_files(checklists: Mapping[str, list[ChecklistItem]]) -> dict[str, str]:
    """``task.meta["workspace_files"]`` for a plan or re-plan: the runner writes the TSV next to TASK.md."""
    return {CHECKLIST_TSV: table(checklists)} if any(checklists.values()) else {}


def prompt_rule(checklists: Mapping[str, list[ChecklistItem]]) -> str:
    """Name the checklist file instead of listing every topic's checks (PI 2026-10-05, #420).

    The CSO declares topics in the same response, so it reads the rows of the topics it declares from the TSV the
    runner writes in its work folder (workspace_files). Missing answers are still caught against the catalog
    (answer_errors), and the reviewer sees the checks with reasons for the declared topics (plan_review_context)."""
    if not any(checklists.values()):
        return ""
    lines = [
        "\n\nTopic checklists:",
        f"Every topic has checks in `{CHECKLIST_TSV}` in the current directory (columns topic, id, check, why).",
        "After choosing `topics`, read only the rows of the topics you declare.",
        "For every item under each topic you declare, answer top-level `checklist` with exactly one of:",
        "`step:<step id>`, `assumption: <one line>`, or `not_applicable: <reason>`.",
        "Use not_applicable only when the check does not apply to this request. When it applies but the plan cannot",
        "do it (e.g. no independent cohort exists), answer `assumption: <why>`: that becomes a stated limitation.",
        "A topic with no rows in the file adds no checklist requirement.",
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
            # The check itself rides along: the plan prompt names only the file (#420), so a correction round
            # still says what to answer if the planner never read it.
            errors.append(f"checklist.{item.id} must answer step:<step id>, assumption: <one line>, or "
                          f"not_applicable: <reason> (check: {item.check})")
            continue
        match = ANSWER.fullmatch(value.strip())
        if match and match.group(2) and match.group(2) not in known:
            errors.append(f"checklist.{item.id} refers to unknown step {match.group(2)}")
    return errors


def limitations(answers: Any) -> list[str]:
    """The answers the PI report must name as one-line limitations: checks that apply but rest on an assumption.

    A not_applicable check is not a limitation (bench C, #373); not_applicable() lists those."""
    if not isinstance(answers, dict):
        return []
    found = []
    for item_id, value in answers.items():
        if isinstance(item_id, str) and isinstance(value, str) and value.startswith("assumption:"):
            reason = value[len("assumption:"):].strip()
            if reason:
                found.append(f"{item_id}: {reason}")
    return sorted(found)


def not_applicable(answers: Any) -> list[str]:
    """The ids answered not_applicable: checks that do not apply to this request."""
    if not isinstance(answers, dict):
        return []
    return sorted(item_id for item_id, value in answers.items()
                  if isinstance(item_id, str) and isinstance(value, str) and value.startswith("not_applicable:")
                  and value[len("not_applicable:"):].strip())
