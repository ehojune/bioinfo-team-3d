"""Blind reviewer draft (#889, first case of #856 "blind re-review").

With ``research.blind_draft`` on, the science reviewer first answers the PI's original request alone: right after CP1
approval and before the first step is sent, so no team plan, step or result can exist yet. The draft is sealed in
the request's research contract (text, sha256, UTC time). The later research review receives the sealed draft and
compares it with the team's results in the optional ``blind_comparison`` field.

The draft's agreement with the team's answer is never evidence: it is recorded to find alternatives the team missed.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

KEY = "blind_draft"
MAX_CHARS = 20_000
SEALED, LATE, UNAVAILABLE = "sealed", "late", "unavailable"

PROMPT = """Write only in your own outputs/ and .tmp/; never edit inputs.
Save your draft to outputs/blind_draft.md and return the same text as your final answer.
You are the independent blind reviewer of this research request. Below is the PI's original request and nothing
else: you have not seen, and must not look for, any team plan, step, result or report.
Answer the request yourself, as a draft of at most 1200 words:
- your own conclusions and the claims they rest on, each marked UNVERIFIED when you did not check it at the source
  (never invent a citation, identifier or number);
- the alternative explanations, methods or framings you considered and why you kept or dropped each;
- what you could not read or verify, and what observation would change your mind.

Request: {request}"""

COMPARE_RULE = """
Blind draft: before any team result existed, you wrote the independent draft below (sealed {sealed_at}, sha256
{sha256}). Compare it with the team's results above and fill "blind_comparison" in your answer:
missed_alternatives = alternatives, explanations or methods your draft considered that the team did not;
differing_conclusions = conclusions where draft and team disagree; evidence_gaps = evidence either side lacks.
Agreement between the draft and the team is not evidence that a claim is true: do not count it as support.
If the comparison finds nothing, return empty lists.

Sealed blind draft:
{text}
"""

COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {name: {"type": "array", "items": {"type": "string"}}
                   for name in ("missed_alternatives", "differing_conclusions", "evidence_gaps")},
    "required": ["missed_alternatives", "differing_conclusions", "evidence_gaps"],
}


def enabled(settings: Any) -> bool:
    return bool(getattr(getattr(settings, "research", None), "blind_draft", False))


def _utc(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def prompt(request: str) -> str:
    return PROMPT.format(request=request)


def seal(text: str, *, results_exist: bool, now: float | None = None) -> dict[str, Any]:
    """The contract record of a finished draft. ``late`` when a team result already existed: not used by the review."""
    body = (text or "").strip()[:MAX_CHARS]
    if not body:
        return unavailable("empty_draft", now=now)
    at = time.time() if now is None else now
    return {"status": LATE if results_exist else SEALED, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "sealed_at": _utc(at), "sealed_at_epoch": at, "chars": len(body), "text": body}


def unavailable(reason: str, *, now: float | None = None) -> dict[str, Any]:
    at = time.time() if now is None else now
    return {"status": UNAVAILABLE, "reason": reason, "recorded_at": _utc(at)}


def sealed_text(record: Any) -> str | None:
    """The draft text of a sealed record whose bytes still match their hash, else None."""
    if not isinstance(record, dict) or record.get("status") != SEALED:
        return None
    text = record.get("text")
    if not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != record.get("sha256"):
        return None
    return text


def review_block(record: Any) -> str:
    text = sealed_text(record)
    if text is None:
        return ""
    return COMPARE_RULE.format(sealed_at=record["sealed_at"], sha256=record["sha256"], text=text)


def without_unfounded_comparison(review: dict, record: Any) -> dict:
    """The review without a ``blind_comparison`` when no sealed draft was given to the reviewer: a comparison of a
    draft that was never shown (feature off, late, unavailable) must not be recorded."""
    if "blind_comparison" in review and sealed_text(record) is None:
        return {key: value for key, value in review.items() if key != "blind_comparison"}
    return review


def settled(record: Any) -> bool:
    """A record that needs no new draft: sealed, late or unavailable (a finished decision survives a restart)."""
    return isinstance(record, dict) and record.get("status") in {SEALED, LATE, UNAVAILABLE}
