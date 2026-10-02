"""Claim anchors in a research report (#58 ③, #90): each conclusion names the ledger claim it rests on.

The CSO ends every sentence that states a conclusion or a number with ``[[claim:<step_id>/<claim_id>]]``. These
functions read only the report text and the CP2-approved ledgers. Whether a sentence says what its claim says
stays a reviewer judgment; code checks that the named claim exists, may be cited, and rests on an artifact whose
sha256 labhq recorded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .claims import STATUS_NEEDS

ANCHOR = re.compile(r"\[\[claim:([^\[\]\n]*)\]\]")
CITABLE_STATUSES = frozenset(STATUS_NEEDS)  # supported, partially_supported, contradicted
# A retrieval that failed, could not reach its source, or found nothing: neither evidence nor proof of absence.
FAILED_LOOKUP_STATUSES = frozenset({"failed", "unavailable", "not_found"})
FAILED_LOOKUP_TITLE = "실패한 조회 — 증거도 부재 증명도 아님"


def anchor(step_id: str, claim_id: str) -> str:
    return f"[[claim:{step_id}/{claim_id}]]"


def _rows(ledger: Any, key: str) -> list[dict]:
    if not isinstance(ledger, Mapping):
        return []
    return [row for row in ledger.get(key) or [] if isinstance(row, Mapping)]


def _pairs(rows: Iterable[Any], field: str) -> set[tuple[str, str]]:
    return {(str(row.get("step_id")), str(row.get(field))) for row in rows or [] if isinstance(row, Mapping)}


def claim_rows(ledgers: Mapping[str, Any], unsupported: Iterable[Any] = ()) -> tuple[list[dict], list[dict]]:
    """(citable, not citable) claims, each ``{"step_id", "claim", "reason"}``.

    A report may cite a claim only when its status asserts a supporting or contradicting relation and CP2 did not
    find it resting only on refused evidence."""
    blocked = _pairs(unsupported, "claim_id")
    citable: list[dict] = []
    other: list[dict] = []
    for step_id, ledger in ledgers.items():
        for claim in _rows(ledger, "claims"):
            if (step_id, str(claim.get("id"))) in blocked:
                other.append({"step_id": step_id, "claim": claim, "reason": "rests only on evidence refused at CP2"})
            elif claim.get("status") not in CITABLE_STATUSES:
                other.append({"step_id": step_id, "claim": claim, "reason": f"status {claim.get('status')}"})
            else:
                citable.append({"step_id": step_id, "claim": claim, "reason": ""})
    return citable, other


def failed_lookups(ledgers: Mapping[str, Any]) -> list[dict[str, str]]:
    """Evidence rows whose retrieval failed, was unavailable, or found nothing, in ledger order."""
    found = []
    for step_id, ledger in ledgers.items():
        for row in _rows(ledger, "evidence"):
            if row.get("status") in FAILED_LOOKUP_STATUSES:
                source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
                found.append({"step_id": step_id, "evidence_id": str(row.get("id")), "status": str(row["status"]),
                              "observation": str(row.get("observation") or ""),
                              "detail": str(row.get("status_detail") or ""), "query": str(source.get("query") or "")})
    return found


def failed_lookup_lines(rows: list[dict[str, str]]) -> list[str]:
    lines = []
    for row in rows:
        line = f"- {row['step_id']}/{row['evidence_id']} ({row['status']}): {row['observation']}"
        if row["detail"]:
            line += f"; {row['detail']}"
        if row["query"]:
            line += f"; query: {row['query']}"
        lines.append(line)
    return lines


def check_report(report: str, ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                 refused: Iterable[Any] = (), artifact_sha256: Mapping[str, str | None] | None = None) -> dict:
    """``{"anchors": n, "problems": [...]}`` for one report against the CP2-approved ledgers.

    ``unsupported`` and ``refused`` are the CP2 receipt rows (``step_id`` with ``claim_id`` or ``evidence_id``);
    ``artifact_sha256`` maps ``<step_id>/<artifact_id>`` to the hash labhq recorded, as the receipt does. A
    problem: an anchor that is malformed or names no claim in the plan; a claim that rests only on refused
    evidence or whose status cannot be cited; supporting evidence whose artifact has no recorded hash; and a
    report that anchors nothing while the ledgers hold citable claims.
    """
    found = ANCHOR.findall(report or "")
    blocked = _pairs(unsupported, "claim_id")
    refused_rows = _pairs(refused, "evidence_id")
    hashes = artifact_sha256 or {}
    problems: list[str] = []
    for raw in dict.fromkeys(found):
        mark = f"[[claim:{raw}]]"
        step_id, separator, claim_id = (part.strip() for part in raw.rpartition("/"))
        if not separator or not step_id or not claim_id:
            problems.append(f"{mark}: not of the form [[claim:<step_id>/<claim_id>]]")
            continue
        if step_id not in ledgers:
            problems.append(f"{mark}: step {step_id} has no ledger in this plan")
            continue
        claim = next((row for row in _rows(ledgers[step_id], "claims") if row.get("id") == claim_id), None)
        if claim is None:
            problems.append(f"{mark}: claim {claim_id} is not in step {step_id}'s ledger")
            continue
        if (step_id, claim_id) in blocked:
            problems.append(f"{mark}: claim {claim_id} rests only on evidence refused at CP2")
            continue
        status = claim.get("status")
        if status not in CITABLE_STATUSES:
            problems.append(f"{mark}: status {status} cannot be cited as a conclusion")
            continue
        evidence = {row.get("id"): row for row in _rows(ledgers[step_id], "evidence")}
        for link in _rows(ledgers[step_id], "links"):
            if link.get("claim_id") != claim_id or link.get("relation") != STATUS_NEEDS[status]:
                continue
            row = evidence.get(link.get("evidence_id"))
            if row is None or (step_id, str(row.get("id"))) in refused_rows:
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            artifact = source.get("artifact_id")
            if artifact and not hashes.get(f"{step_id}/{artifact}"):
                problems.append(f"{mark}: evidence {row.get('id')} cites artifact {artifact}, which has no "
                                "recorded artifact_sha256")
    if not found and claim_rows(ledgers, unsupported)[0]:
        problems.append("the report anchors no claim while the ledgers hold citable claims; every conclusion "
                        "needs [[claim:<step_id>/<claim_id>]]")
    return {"anchors": len(found), "problems": problems}
