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
    an id once, so the checks are joined instead of the later topic's check being dropped. Every sharing topic is
    retained even when its check text is identical, because topic-qualified answers are validated one by one."""
    selected = set(topics) if isinstance(topics, list) else set()
    found: dict[str, ChecklistItem] = {}
    for topic, items in checklists.items():
        if topic in selected:
            for item in items:
                prior = found.get(item.id)
                if prior is None:
                    found[item.id] = item
                else:
                    checks = prior.check.split(" / ")
                    whys = prior.why.split(" / ")
                    found[item.id] = ChecklistItem(
                        item.id,
                        " / ".join([*checks, *([] if item.check in checks else [item.check])]),
                        " / ".join([*whys, *([] if item.why in whys else [item.why])]),
                        f"{prior.topic},{item.topic}",
                    )
    return list(found.values())


def normalize_answers(answers: Any, topics: Any, checklists: Mapping[str, list[ChecklistItem]]) -> Any:
    """Re-key answers written ``topic.id`` or ``topic/id`` (the TSV's two columns joined) to the bare id.

    The plan answers each id once, but a planner reading the topic and id columns often joins them; the v0.5 trial
    plan was rejected twice for that spelling alone (2026-10-07). Only an unambiguous alias is re-keyed: when an exact
    id answer exists, or two topics sharing an id (``library_qc`` for ATAC and ChIP) were answered differently, every
    key stays as written. Validation accepts those topic-qualified answers only when every topic supplied a valid
    answer, so no topic's check is dropped (PR #470 review, #586)."""
    if not isinstance(answers, dict):
        return answers
    selected = set(topics) if isinstance(topics, list) else set()
    aliases = {f"{topic}{sep}{item.id}": item.id for topic, items in checklists.items() if topic in selected
               for item in items for sep in (".", "/")}
    groups: dict[str, list[str]] = {}
    for key in answers:
        target = aliases.get(key) if isinstance(key, str) else None
        if target is not None:
            groups.setdefault(target, []).append(key)
    renames = {key: target for target, keys in groups.items()
               if target not in answers and len({repr(answers[key]) for key in keys}) == 1 for key in keys}
    normalized: dict[str, Any] = {}
    for key, value in answers.items():
        target = renames.get(key)
        if target is None:
            normalized[key] = value
        elif target not in normalized:
            normalized[target] = value
    return normalized


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
        "`step:<step id>`, `assumption: <why it could not be done>`, or `not_applicable: <why it does not apply>`.",
        "For an id shared by topics, use one bare id answer for all, or answer every topic as `topic.id`.",
        "Do every applicable check the data allow. When a check applies but cannot be done (e.g. no independent",
        "cohort exists), answer `assumption: <why it could not be done>`: a justified skip is acceptable, becomes a",
        "stated limitation and is shown to the PI as a warning. Never skip silently or with an empty reason.",
        "Use not_applicable only when the check does not apply to this request.",
        "A topic with no rows in the file adds no checklist requirement.",
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


def _answer_slots(body: dict[str, Any], item: ChecklistItem) -> list[tuple[str, str, Any]]:
    """(answer key, topic, answer) for each place the plan answers a check: one bare slot (topic ""), or one
    qualified slot per topic sharing the id when the plan answered them separately."""
    if item.id in body:
        return [(item.id, "", body[item.id])]
    topics = item.topic.split(",")
    if len(topics) == 1:
        return [(item.id, "", None)]
    slots = []
    for topic in topics:
        keys = [key for key in (f"{topic}.{item.id}", f"{topic}/{item.id}") if key in body]
        key = keys[0] if len(keys) == 1 else f"{topic}.{item.id}"
        slots.append((key, topic, body.get(key) if len(keys) == 1 else None))
    return slots


def _answer_values(body: dict[str, Any], item: ChecklistItem) -> list[tuple[str, Any]]:
    """Bare answer, or one qualified answer slot per topic sharing the id."""
    return [(key, value) for key, _, value in _answer_slots(body, item)]


def answer_errors(answers: Any, required: list[ChecklistItem], step_ids: list[str]) -> list[str]:
    """Validate required answers and step references without rejecting unrelated extra keys."""
    if not required:
        return []
    body = answers if isinstance(answers, dict) else {}
    errors: list[str] = []
    known = set(step_ids)
    for item in required:
        for answer_id, value in _answer_values(body, item):
            if skip_reason(value) == "":
                errors.append(f"checklist.{answer_id} must answer with a reason: "
                              "assumption: <why it could not be done> or "
                              "not_applicable: <why it does not apply>")
                continue
            if not isinstance(value, str) or not ANSWER.fullmatch(value.strip()):
                # The check itself rides along: the plan prompt names only the file (#420), so a correction round
                # still says what to answer if the planner never read it.
                errors.append(f"checklist.{answer_id} must answer step:<step id>, "
                              "assumption: <why it could not be done>, or "
                              f"not_applicable: <why it does not apply> (check: {item.check})")
                continue
            match = ANSWER.fullmatch(value.strip())
            if match and match.group(2) and match.group(2) not in known:
                errors.append(f"checklist.{answer_id} refers to unknown step {match.group(2)}")
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
        for answer_id, value in _answer_values(body, item):
            reason = skip_reason(value)
            if reason and value.strip().startswith("assumption:"):
                if answer_id == item.id:
                    name = label(item)
                elif answer_id.endswith(f".{item.id}"):
                    name = f"{answer_id[:-len(item.id) - 1]}/{item.id}"
                else:
                    name = answer_id
                found.append((name, reason))
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


# --- Reviewer verdicts per check (#586) -------------------------------------------------------------------------
# The review gets the declared checks and the plan's answers, answers each one (done, partly, not_done, with
# evidence) and labhq turns a miss on a step-answered check into a P2 issue and the appendix table.
REVIEW_STATUSES = ("done", "partly", "not_done")
REVIEW_STATUS_LABELS = {"done": "했다", "partly": "일부", "not_done": "안 했다"}
REVIEW_MISSES = ("partly", "not_done")


def _id_aliases(item: ChecklistItem) -> dict[str, str]:
    """``{spelling: topic}`` a reviewer may use for a check: the bare id (topic ""), or ``topic.id`` / ``topic/id``.

    The topic is kept only when several topics share the id: a lone topic's qualified spelling is the bare check."""
    topics = item.topic.split(",")
    return {item.id: "", **{f"{topic}{sep}{item.id}": topic if len(topics) > 1 else ""
                            for topic in topics for sep in (".", "/")}}


def review_verdicts(rows: Any, required: list[ChecklistItem]) -> dict[tuple[str, str], tuple[str, str]]:
    """``{(item id, topic): (status, evidence)}`` from the reviewer's ``checklist`` rows, for the required checks only.

    ``topic`` is "" for a bare-id row (and for a check that only one topic has). A ``topic.id`` or ``topic/id`` row
    of a shared id keeps its topic, so a plan that answered each topic differently can match each verdict to its own
    answer instead of one merged verdict (PR #600 review). A row naming no required check is dropped. When rows
    repeat a check, the worse status wins, so a miss is never hidden by a second ``done``."""
    if not isinstance(rows, list):
        return {}
    by_alias = {alias: (item.id, topic) for item in required for alias, topic in _id_aliases(item).items()}
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        key = by_alias.get(row["id"].strip())
        status, evidence = row.get("status"), row.get("evidence")
        if key is None or status not in REVIEW_STATUSES:
            continue
        prior = found.get(key)
        if prior is None or REVIEW_STATUSES.index(status) > REVIEW_STATUSES.index(prior[0]):
            found[key] = (status, evidence.strip() if isinstance(evidence, str) else "")
    return found


@dataclass(frozen=True)
class CheckSlot:
    """One answer of the plan to a check, with the reviewer's verdict on it.

    A bare answer (or a check of one topic) is one slot. A shared id the plan answered per topic is one slot per
    topic, each with its own answer, steps and verdict."""
    name: str                # topic/id as shown to the PI
    topic: str               # "" for a bare answer
    answer: str              # the plan's answer text, "" when it gave none
    steps: tuple[str, ...]   # the step ids the answer names (``step:<id>``)
    status: str              # "" when the reviewer gave no verdict for it
    evidence: str
    shared: bool = False     # the verdict is the reviewer's bare-id row, which names no topic


def _worst(verdicts: list[tuple[str, str]]) -> tuple[str, str]:
    return max(verdicts, key=lambda verdict: REVIEW_STATUSES.index(verdict[0]), default=("", ""))


def check_slots(answers: Any, item: ChecklistItem,
                verdicts: Mapping[tuple[str, str], tuple[str, str]]) -> list[CheckSlot]:
    """The slots of one check. A bare answer takes the worst verdict among the reviewer's rows for the id, since the
    plan gave one answer for all of them. A topic's own answer takes that topic's row; without one it takes the
    reviewer's bare-id row (``shared``) and never another topic's verdict."""
    body = answers if isinstance(answers, dict) else {}
    bare = verdicts.get((item.id, ""))
    slots = []
    for _, topic, value in _answer_slots(body, item):
        text = value.strip() if isinstance(value, str) else ""
        match = ANSWER.fullmatch(text)
        steps = (match.group(2),) if match and match.group(2) else ()
        if topic:
            own = verdicts.get((item.id, topic))
            status, evidence = own or bare or ("", "")
            slots.append(CheckSlot(f"{topic}/{item.id}", topic, text, steps, status, evidence,
                                   shared=own is None and bare is not None))
        else:
            status, evidence = _worst([verdict for (verdict_id, _), verdict in verdicts.items()
                                       if verdict_id == item.id])
            slots.append(CheckSlot(label(item), "", text, steps, status, evidence))
    return slots


def answered_steps(answers: Any, item: ChecklistItem) -> list[str]:
    """The step ids the plan answered a check with (``step:<id>``), once each, in answer order."""
    steps: list[str] = []
    for slot in check_slots(answers, item, {}):
        steps += [step for step in slot.steps if step not in steps]
    return steps


@dataclass(frozen=True)
class ReviewMiss:
    """A check the plan answered with a step that the reviewer found only partly or not done."""
    item: ChecklistItem
    name: str                # topic/id of each answer the verdict covers, joined by ", "
    topics: tuple[str, ...]  # those answers' topics; empty for a bare answer
    steps: tuple[str, ...]   # the steps those answers name
    status: str
    evidence: str


def review_misses(answers: Any, required: list[ChecklistItem], rows: Any) -> list[ReviewMiss]:
    """Each check the plan answered with a step that the reviewer found only partly or not done.

    A miss belongs to the step answer of the topic whose verdict it is, so a shared id the plan answered per topic
    can give one miss per topic. A bare-id reviewer row is one verdict: it covers the topics without a row of their
    own in a single miss that names all their steps. assumption and not_applicable answers are not here: the plan
    already said why."""
    verdicts = review_verdicts(rows, required)
    found = []
    for item in required:
        misses = [slot for slot in check_slots(answers, item, verdicts)
                  if slot.status in REVIEW_MISSES and slot.steps]
        shared = [slot for slot in misses if slot.shared]
        for slot in misses:
            if slot.shared and slot is not shared[0]:
                continue  # inside the shared miss
            group = shared if slot.shared else [slot]
            found.append(ReviewMiss(
                item, ", ".join(member.name for member in group),
                tuple(member.topic for member in group if member.topic),
                tuple(dict.fromkeys(step for member in group for step in member.steps)),
                slot.status, slot.evidence))
    return found


def _table_cell(text: Any, limit: int = 120) -> str:
    cell = _cell(text).replace("|", "\\|")
    return cell if len(cell) <= limit else cell[:limit - 1].rstrip() + "…"


def report_table(answers: Any, required: list[ChecklistItem], rows: Any, *, owner: str | None = None) -> str:
    """The appendix table: item, owning step, reviewer verdict, evidence (#586). One line per check, and one per
    topic answer when the plan answered a shared id per topic (PR #600 review).

    A check answered assumption shows 가정 and a not_applicable one 해당 없음 (no verdict: nothing was left undone);
    both fall back to the plan's reason when the reviewer gave no evidence. Without a reviewer row the verdict is -.
    ``owner`` replaces the step ids of a step-answered check: the solo route reviews one direct result, and the
    plan's steps never ran."""
    if not required:
        return ""
    verdicts = review_verdicts(rows, required)
    lines = ["| 항목 | 담당 단계 | 리뷰 판정 | 근거 |", "| --- | --- | --- | --- |"]
    for item in required:
        for slot in check_slots(answers, item, verdicts):
            reason = skip_reason(slot.answer) or ""
            if slot.steps:
                cell = owner or ", ".join(slot.steps)
            elif slot.answer.startswith("assumption:"):
                cell = "가정"
            elif slot.answer.startswith("not_applicable:"):
                cell = "해당 없음"
            else:
                cell = "-"
            verdict = "-" if cell == "해당 없음" or not slot.status else REVIEW_STATUS_LABELS[slot.status]
            lines.append("| " + " | ".join(_table_cell(value) for value in (
                slot.name, cell, verdict, slot.evidence or (reason if not slot.steps else "") or "-")) + " |")
    return "\n".join(lines)
