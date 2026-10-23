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

# Case and spaces vary in what a model writes ([[Claim: s1/c1]]); a variant is checked, not skipped.
ANCHOR = re.compile(r"\[\[\s*claim\s*:([^\[\]\n]*)\]\]", re.IGNORECASE)
# A fenced block or inline code shows the anchor syntax; it states no conclusion.
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
CITABLE_STATUSES = frozenset(STATUS_NEEDS)  # supported, partially_supported, contradicted
# A retrieval that failed, could not reach its source, or found nothing: neither evidence nor proof of absence.
FAILED_LOOKUP_STATUSES = frozenset({"failed", "unavailable", "not_found"})
FAILED_LOOKUP_TITLE = "실패한 조회 — 증거도 부재 증명도 아님"
SOURCE_EVIDENCE_TITLE = "주장별 원문 근거"

ACCESS_LABELS = {"full_text": "전문", "abstract_only": "초록만", "not_obtained": "못 구함",
                 "paywalled": "유료 막힘", "undetermined": "미판정"}
LINEAGE_LABELS = {"resolved": "확인", "undetermined": "미판정", "cycle": "순환 인용",
                  "dangling": "출처 ID 누락"}
SUPPLEMENT_LABELS = {"available": "확보", "missing": "누락", "not_applicable": "비해당",
                     "undetermined": "미판정"}


def source_description(source: Mapping[str, Any]) -> str:
    """Identifiers and recorded location only; missing metadata is never inferred."""
    parts = []
    if source.get("id_scheme") and source.get("id_value"):
        parts.append(f"{str(source['id_scheme']).upper()}:{source['id_value']}")
    if source.get("uri"):
        parts.append(str(source["uri"]))
    if source.get("artifact_id"):
        parts.append(f"artifact:{source['artifact_id']}")
    return "; ".join(parts) or "출처 미기록"


def _location(value: Any) -> str:
    """Keep receipt line numbers when the publication sanitizer removes a machine-local path."""
    parts = []
    for part in str(value or "미기록").split("; "):
        if re.match(r"^(?:[A-Za-z]:[/\\]|[/\\]|~[/\\])", part) and re.search(r":L\d+(?:-L\d+)?$", part):
            part = part.replace("\\", "/").rsplit("/", 1)[-1]
        parts.append(part)
    return "; ".join(parts)


def _accepted_lineage(ledger: Any, step_id: str, refused_ids: set[tuple[str, str]]) -> dict:
    from pydantic import ValidationError
    from .claims import Evidence, assess_lineage

    parsed = []
    for row in _rows(ledger, "evidence"):
        if (step_id, str(row.get("id"))) in refused_ids:
            continue
        try:
            parsed.append(Evidence.model_validate(row))
        except ValidationError:
            continue
    paths = {str(row.get("artifact_id")): str(row.get("path") or "") for row in _rows(ledger, "artifact_refs")}
    return assess_lineage(parsed, artifact_paths=paths)


def _claims_without_accepted_support(ledgers: Mapping[str, Any], refused: Iterable[Any]) -> set[tuple[str, str]]:
    """CP2 refusal applies to a re-citation's origins too, even if CP2 did not mark the claim itself."""
    refused_ids = _pairs(refused, "evidence_id")
    blocked = set()
    for step_id, ledger in ledgers.items():
        evidence = {row.get("id"): row for row in _rows(ledger, "evidence")}
        lineage = (_accepted_lineage(ledger, step_id, refused_ids)
                   if any(row.get("lineage") is not None for row in evidence.values()) else {})
        for claim in _rows(ledger, "claims"):
            needed = STATUS_NEEDS.get(claim.get("status"))
            links = [link for link in _rows(ledger, "links") if needed and link.get("claim_id") == claim.get("id")
                     and link.get("relation") == needed]
            accepted = []
            for link in links:
                evidence_id = link.get("evidence_id")
                row = evidence.get(evidence_id)
                if not row or (step_id, str(evidence_id)) in refused_ids or row.get("status") != "observed":
                    continue
                if row.get("lineage") is None or (evidence_id in lineage and lineage[evidence_id].status == "resolved"):
                    accepted.append(evidence_id)
            if links and not accepted:
                blocked.add((step_id, str(claim.get("id"))))
    return blocked


def claim_evidence_rows(ledgers: Mapping[str, Any], *, refused: Iterable[Any] = (),
                        unsupported: Iterable[Any] = ()) -> list[dict[str, str]]:
    """All claims and lookup gaps, including uncitable/refused rows, for every reader surface.

    Lineage is assessed only over valid, CP2-accepted evidence. A refused original therefore cannot be
    resurrected by a re-citation. Older incomplete publication ledgers retain their source links as undetermined.
    """
    refused = list(refused)
    refused_ids, blocked = _pairs(refused, "evidence_id"), _pairs(unsupported, "claim_id")
    no_support = _claims_without_accepted_support(ledgers, refused)
    refusal_reasons = {(str(row.get("step_id")), str(row.get("evidence_id"))): str(row.get("reason") or "")
                       for row in refused if isinstance(row, Mapping)}
    rows = []
    for step_id, ledger in ledgers.items():
        evidence = {str(row.get("id")): row for row in _rows(ledger, "evidence")}
        assessments = _accepted_lineage(ledger, step_id, refused_ids)
        linked = set()

        def display(claim: Mapping[str, Any], evidence_id: str, relation: str) -> None:
            row = evidence.get(evidence_id, {})
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            is_refused = (step_id, evidence_id) in refused_ids
            assessment = assessments.get(evidence_id)
            lineage = LINEAGE_LABELS.get(assessment.status, "미판정") if assessment else "미판정"
            if is_refused:
                lineage = "CP2 거부 — 최초 출처로 사용 안 함"
            elif assessment and assessment.status == "resolved":
                originals = []
                for original_id in assessment.original_evidence_ids:
                    original = evidence.get(original_id, {})
                    origin = original.get("source") or {}
                    originals.append(f"{original_id}: {source_description(origin)}; 위치: "
                                     f"{_location(origin.get('locator'))}")
                lineage = ("재인용" if (row.get("lineage") or {}).get("cited_from") else "최초 실증 출처")
                lineage += " → " + "; ".join(originals)
                lineage += "; 독립 group: " + ", ".join(assessment.independence_groups)
            elif assessment and assessment.status in {"cycle", "dangling"}:
                lineage += "; 독립 group 0"
            state = str(claim.get("status") or "미기록")
            if (step_id, str(claim.get("id"))) in blocked:
                state += "; CP2 지지 근거 거부"
            elif (step_id, str(claim.get("id"))) in no_support:
                state += "; 승인된 지지 계보 없음"
            evidence_state = "CP2 거부" if is_refused else str(row.get("status") or "미기록")
            rows.append({"step_id": step_id, "claim_id": str(claim.get("id") or "—"),
                         "claim": str(claim.get("statement") or "claim 연결 없음"), "claim_status": state,
                         "evidence_id": evidence_id or "—", "relation": relation or "연결 없음",
                         "evidence_status": evidence_state,
                         "detail": refusal_reasons.get((step_id, evidence_id), "") if is_refused
                         else str(row.get("status_detail") or ""),
                         "source": source_description(source), "locator": _location(source.get("locator")),
                         "access": ACCESS_LABELS.get(str(source.get("access_status")), "미판정"),
                         "methods": _location(source.get("methods_locator")),
                         "qc": _location(source.get("qc_locator")),
                         "supplement": SUPPLEMENT_LABELS.get(str(source.get("supplementary_status")), "미판정"),
                         "lineage": lineage})

        for claim in _rows(ledger, "claims"):
            links = [link for link in _rows(ledger, "links") if link.get("claim_id") == claim.get("id")]
            for link in links:
                evidence_id = str(link.get("evidence_id") or "")
                linked.add(evidence_id)
                display(claim, evidence_id, str(link.get("relation") or ""))
            if not links:
                display(claim, "", "")
        for evidence_id in evidence:
            if evidence_id not in linked:
                display({}, evidence_id, "context")
        for sid, evidence_id in sorted(refused_ids):
            if sid == step_id and evidence_id not in evidence and evidence_id not in linked:
                display({}, evidence_id, "context")
    return rows


def evidence_note(row: Mapping[str, str]) -> str:
    """One readable evidence note, shared by report tables and publication footnotes."""
    return (f"{row['source']}; 위치: {row['locator']}; 접근: {row['access']}; "
            f"Methods: {row['methods']}; QC: {row['qc']}; 보충자료: {row['supplement']}; "
            f"계보: {row['lineage']}")


def claim_evidence_section(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""

    def cell(value: str) -> str:
        return " ".join(value.split()).replace("|", "&#124;")

    lines = [f"## {SOURCE_EVIDENCE_TITLE}", "", "접근 상태는 지지 여부와 별개입니다. 미기록 위치·최초 출처는 추정하지 않습니다.", "",
             "| Claim (상태) | Evidence · 관계 (상태) | 출처·원문 위치·접근·계보 |",
             "|---|---|---|"]
    for row in rows:
        claim = f"{row['step_id']}/{row['claim_id']} ({row['claim_status']}): {row['claim']}"
        relation = f"{row['evidence_id']} · {row['relation']} ({row['evidence_status']})"
        if row["detail"]:
            relation += "; " + row["detail"]
        lines.append("| " + " | ".join(map(cell, (claim, relation, evidence_note(row)))) + " |")
    return "\n".join(lines) + "\n"


def anchor(step_id: str, claim_id: str) -> str:
    return f"[[claim:{step_id}/{claim_id}]]"


def _rows(ledger: Any, key: str) -> list[dict]:
    if not isinstance(ledger, Mapping):
        return []
    return [row for row in ledger.get(key) or [] if isinstance(row, Mapping)]


def _pairs(rows: Iterable[Any], field: str) -> set[tuple[str, str]]:
    return {(str(row.get("step_id")), str(row.get(field))) for row in rows or [] if isinstance(row, Mapping)}


def claim_rows(ledgers: Mapping[str, Any], unsupported: Iterable[Any] = (), *,
               refused: Iterable[Any] = ()) -> tuple[list[dict], list[dict]]:
    """(citable, not citable) claims, each ``{"step_id", "claim", "reason"}``.

    A report may cite a claim only when its status asserts a supporting or contradicting relation and CP2 did not
    find it resting only on refused evidence."""
    blocked = _pairs(unsupported, "claim_id")
    no_support = _claims_without_accepted_support(ledgers, refused)
    citable: list[dict] = []
    other: list[dict] = []
    for step_id, ledger in ledgers.items():
        for claim in _rows(ledger, "claims"):
            if (step_id, str(claim.get("id"))) in blocked:
                other.append({"step_id": step_id, "claim": claim, "reason": "rests only on evidence refused at CP2"})
            elif (step_id, str(claim.get("id"))) in no_support:
                other.append({"step_id": step_id, "claim": claim,
                              "reason": "no CP2-accepted support with resolved citation lineage"})
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
    found = ANCHOR.findall(CODE.sub(" ", report or ""))
    unsupported, refused = list(unsupported), list(refused)
    blocked = _pairs(unsupported, "claim_id")
    refused_rows = _pairs(refused, "evidence_id")
    no_support = _claims_without_accepted_support(ledgers, refused)
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
        if (step_id, claim_id) in no_support:
            problems.append(f"{mark}: no CP2-accepted support with resolved citation lineage")
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
    if not found and claim_rows(ledgers, unsupported, refused=refused)[0]:
        problems.append("the report anchors no claim while the ledgers hold citable claims; every conclusion "
                        "needs [[claim:<step_id>/<claim_id>]]")
    return {"anchors": len(found), "problems": problems}
