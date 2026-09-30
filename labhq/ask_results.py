"""One terminal ask contract for live replies and persisted continuations."""

from __future__ import annotations

from typing import Any, Iterable

from .models import TaskResult


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


def read_ask_results(answers: Iterable[dict]) -> dict:
    """Gate a whole batch before any answer is rendered or work is continued."""
    outcomes = [ask_result(**answer) for answer in answers]
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
