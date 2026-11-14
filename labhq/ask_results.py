"""One terminal ask contract for live replies and persisted continuations."""

from __future__ import annotations

from typing import Any, Iterable

from .models import TaskResult


def _failed_before_start(previous: dict) -> bool:
    evidence = previous.get("consult_evidence") or {}
    if not isinstance(evidence, dict):
        return False
    return (all(key in evidence for key in ("spawn", "end_observed", "read_only_check", "error_kind", "exit_code"))
            and previous.get("status") == "rejected" and not previous.get("hard_stop")
            and evidence.get("spawn") == "failed" and evidence.get("end_observed") == "start_failed"
            and evidence.get("read_only_check") == "no_changes_observed"
            and evidence.get("error_kind") is None and evidence.get("exit_code") is None)


def replacement_allowed(previous_ask: dict, previous: dict, current_ask: dict) -> bool:
    """Only an identified, never-started consult can be replaced; unknown failures stay rejected."""
    if not all(isinstance(value, dict) for value in (previous_ask, previous, current_ask)):
        return False
    old_question, new_question = previous_ask.get("question"), current_ask.get("question")
    if not isinstance(old_question, str) or not isinstance(new_question, str):
        return False
    return bool(
        previous_ask.get("task_id") and previous_ask.get("task_id") == current_ask.get("task_id")
        and previous_ask.get("request_id") == current_ask.get("request_id")
        and previous_ask.get("agent_id") == current_ask.get("agent_id")
        and previous_ask.get("id") == current_ask.get("replaces_ask_id")
        and previous_ask.get("to") != "pi" and current_ask.get("to") != "pi"
        and old_question.strip().casefold() == new_question.strip().casefold()
        and previous_ask.get("refs", []) == current_ask.get("refs", [])
        and _failed_before_start(previous)
    )


def ask_result(*, answer: Any = None, reason: Any = None, status: str | None = None,
               decision: dict | None = None, **metadata: Any) -> dict:
    """Build answered/answer or rejected/reason; denial always overrides prose."""
    text = str(answer or "").strip()
    explanation = str(reason or "").strip()
    # Also recognize old persisted PI outcomes carrying approved at the top level.
    approval = decision if decision is not None else metadata if "approved" in metadata else None
    denied = approval is not None and (approval.get("approved") is not True or
                                      approval.get("state") == "timed_out" or approval.get("timed_out"))
    if approval is not None:
        text = text or str(approval.get("note") or "").strip()
    if denied or explanation or status not in {None, "answered"} or not text:
        note = str(approval.get("note") or "").strip() if denied else ""
        return {**metadata, "status": "rejected",
                "reason": explanation or note or text or "question was rejected or unanswered"}
    return {**metadata, "status": "answered", "answer": text}


def read_ask_results(answers: Iterable[dict], *, pending_replacements: Iterable[str] = ()) -> dict:
    """Gate a whole batch before any answer is rendered or work is continued."""
    outcomes = [ask_result(**answer) for answer in answers]
    replaced = {target for outcome in outcomes if outcome["status"] == "answered"
                and isinstance(target := outcome.get("verified_replacement_of"), str) and target}
    replaced.update(target for target in pending_replacements if isinstance(target, str) and target)
    outcomes = [outcome for outcome in outcomes if not (
        isinstance(outcome.get("ask_id"), str) and outcome["ask_id"] in replaced and _failed_before_start(outcome))]
    rejected = [outcome["reason"] for outcome in outcomes if outcome["status"] == "rejected"]
    if rejected:
        return ask_result(reason="; ".join(rejected))
    # Empty batches are valid when a turn made no asks.
    return {"status": "answered", "answer": "\n".join(
        f"- {outcome.get('from', 'unknown')}: {outcome['answer']}" for outcome in outcomes)}


def rejected_step(result: TaskResult, reason: str) -> TaskResult:
    """Keep artifacts and jobs for audit while making the blocked turn terminal."""
    return result.model_copy(update={"ok": False, "error_kind": "ask_rejected",
                                     "error": f"question was rejected or unanswered: {reason}",
                                     "pending_asks": [], "blocking_decision": None})
