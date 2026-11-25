"""Checklist context and findings shared by the generic and research reviewers."""

import json
import re
from typing import Any

from ..vocab import topic_checklists


# The reviewer's answer to each declared checklist item and required precedent item (#586). Optional in both review
# schemas: a review saved before it existed, or one from a request with no checklist, stays valid.
CHECKLIST_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"id": {"type": "string"},
                       "status": {"type": "string", "enum": list(topic_checklists.REVIEW_STATUSES)},
                       "evidence": {"type": "string"}},
        "required": ["id", "status", "evidence"]}}


# What both reviews are asked about the declared checklist (#586). The context block that follows the prompt lists
# the ids (plan_review_context); labhq reads the answers in review_with_checklist and checklist_appendix.
CHECKLIST_REVIEW_RULE = (
    "When the declared topic checklist or Analysis precedents below list items, also fill `checklist` with one\n"
    "entry for every listed checklist item and required precedent item, using its id exactly as listed (a check\n"
    "listed once per topic as `topic.id` gets one entry per listed id). Each entry\n"
    "has `status` (done, partly or not_done) and `evidence`: the step id or output path in the results that shows\n"
    "it, or a short reason when not_done. One line each. Judge from the results, not from the plan's answer.\n"
    "For an item the plan answered step:<id> that you mark partly or not_done, labhq adds a P2 issue unless you\n"
    "already filed a P1 or P2 issue that names the item id; name the id when you file one.")


def _citations(row: dict[str, Any]) -> str:
    return "; ".join(str(item) for item in row.get("citations") or []) or "citation unavailable"


def analysis_precedents_text(record: Any) -> str:
    """Compact context shared by planning, solo execution, review and report prompts."""
    if not isinstance(record, dict):
        return ""
    if record.get("status") != "ok":
        return "Analysis precedents:\n- unavailable; continue without precedent requirements."
    lines = ["Analysis precedents:",
             "Paper access/receipt declarations are unverified until artifact hash/ranges are checked after approval. "
             "Do not invent method/QC parameters; request missing evidence.", "Papers:"]
    lines.extend(f"- {row.get('title')}: {_citations(row)}" +
                 "".join(f"; {key}: {row[key]}" for key in ("repo", "data_url", "fulltext_receipt") if row.get(key))
                 for row in record.get("papers") or [])
    lines.append("Required analyses (answer each id in `checklist`; if one cannot be done, say why):")
    lines.extend(f"- {row.get('id')}: {row.get('analysis')} — {row.get('why')} [{_citations(row)}]"
                 for row in record.get("required") or [])
    lines.append("Recommended analyses:")
    lines.extend(f"- {row.get('analysis')} — {row.get('why')} [{_citations(row)}]"
                 for row in record.get("recommended") or [])
    if record.get("limitations"):
        lines.append("Search limitations: " + "; ".join(map(str, record["limitations"])))
    return "\n".join(lines)


def _precedent_items(record: Any) -> list[topic_checklists.ChecklistItem]:
    if not isinstance(record, dict) or record.get("status") != "ok":
        return []
    return [topic_checklists.ChecklistItem(str(row["id"]), str(row.get("analysis") or ""),
                                           str(row.get("why") or ""), "precedent")
            for row in record.get("required") or [] if row.get("id")]


def required_checklist_items(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                             precedents: Any) -> list[topic_checklists.ChecklistItem]:
    body = plan if isinstance(plan, dict) else {}
    items = topic_checklists.requirements(body.get("topics"), catalog)
    by_id = {item.id: item for item in items}
    for item in _precedent_items(precedents):
        by_id.setdefault(item.id, item)
    return list(by_id.values())


def plan_review_context(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]], precedents: Any) -> str:
    body = plan if isinstance(plan, dict) else {}
    required = required_checklist_items(body, catalog, precedents)
    rendered = (analysis_precedents_text(precedents)
                if isinstance(precedents, dict) and precedents.get("status") == "ok" else "")
    if not required and not rendered:
        return ""
    lines = ["\n\nDeclared topic checklist and plan answers:"]
    answers = body.get("checklist") if isinstance(body.get("checklist"), dict) else {}
    # A shared id the plan answered per topic is listed once per topic as topic.id, with the plan's answer, so the
    # reviewer's rows say which topic's step missed the check (PR #602 review).
    lines.extend(f"- {entry.id}: {entry.check} Why: {entry.why}" + (f" — 계획 답: {entry.answer or '없음'}"
                                                                  if entry.topic else "")
                 for entry in topic_checklists.review_entries(answers, required, catalog))
    lines.append("Answers: " + json.dumps(answers, ensure_ascii=False, sort_keys=True))
    if rendered:
        lines += ["", rendered]
    return "\n".join(lines)


def _names_check(issue: dict, miss: topic_checklists.ReviewMiss) -> bool:
    """Whether a review issue names the check the miss is about. ASCII boundaries: a Korean particle may follow the id.

    When the check is shared by several topics, an issue about another topic's ``topic/id`` or ``topic.id`` does not
    stand in for this topic's miss (PR #600 review)."""
    text = " ".join(str(issue.get(key) or "") for key in ("problem", "request", "evidence_quote"))

    def named(spelling: str) -> bool:
        return re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(spelling)}(?![A-Za-z0-9_-])", text) is not None

    qualified = {topic: named(f"{topic}/{miss.item.id}") or named(f"{topic}.{miss.item.id}")
                 for topic in miss.item.topic.split(",")}
    if any(qualified[topic] for topic in miss.topics or qualified):
        return True
    return not any(qualified.values()) and named(miss.item.id)


def review_with_checklist(review: dict, plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                          precedents: Any, *, research: bool = False, owner: str | None = None) -> dict:
    """The review plus a P2 issue for each check the plan answered with a step that the reviewer found partly or not
    done (#586). In the 10-13 trial the reviewers got the checklist and cited it in no finding.

    A P1 or P2 issue that already names the check's id stands in for it; a P3 one does not. assumption and
    not_applicable answers add nothing: the plan stated why, and the review already files a wrong reason. A check
    several topics share, answered per topic, gets one issue per topic on that topic's own step. The research lane's
    issues carry its extra fields (claim_id, category, evidence_quote).

    ``owner`` is the result a solo review judged (SOLO_OWNER). The plan's steps never ran then, so the issue goes
    to that result: it is neither filed on a plan step nor tells the PI to fix one (PR #600 review)."""
    body = plan if isinstance(plan, dict) else {}
    required = required_checklist_items(body, catalog, precedents)
    issues = [issue for issue in review.get("issues") or [] if isinstance(issue, dict)]
    added: list[dict] = []
    for miss in topic_checklists.review_misses(body.get("checklist"), required, review.get("checklist")):
        if any(issue.get("priority") in ("P1", "P2") and _names_check(issue, miss) for issue in [*issues, *added]):
            continue
        verdict = "일부만 했다" if miss.status == "partly" else "하지 않았다"
        if owner:
            issue = {"step_id": owner, "priority": "P2",
                     "problem": f"점검 {miss.name}: {owner} 결과에서 리뷰어는 {verdict}고 판정했습니다. "
                                f"근거: {miss.evidence or '없음'}",
                     "request": f"{owner} 결과에서 점검 {miss.name}을(를) 끝내거나, 못 하는 이유를 한계에 남기세요."}
        else:
            steps = ", ".join(miss.steps)
            issue = {"step_id": miss.steps[0], "priority": "P2",
                     "problem": f"점검 {miss.name}: 계획은 {steps} 단계로 답했지만 리뷰어는 {verdict}고 "
                                f"판정했습니다. 근거: {miss.evidence or '없음'}",
                     "request": f"{steps} 단계에서 점검 {miss.name}을(를) 끝내거나, 못 하는 이유를 계획에 "
                                "assumption으로 남기세요."}
        if research:
            issue.update(claim_id="", category="other", evidence_quote=miss.evidence)
        added.append(issue)
    return {**review, "issues": [*issues, *added]} if added else review
