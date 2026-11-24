"""Record the PI's evidence checkpoint decision against the frozen plan."""

from .contract import read_evidence_decision


MAX_ASKS = 3


async def ask(hub, rid: str, detail: dict, summary: str, revise_continues: bool) -> tuple[str, str, int, dict]:
    retry = ""
    for asks in range(1, MAX_ASKS + 1):
        decision = await hub.request_approval(kind="research_evidence", request_id=rid,
                                              detail=detail, summary=retry + summary)
        decided = read_evidence_decision(decision)
        note = str(decision.get("note") or "").strip()
        if decided == "revision_requested" and revise_continues and asks < MAX_ASKS and not note:
            # A revised plan is written from the note (R10).
            retry = ("수정 요청에 메모가 없어 다시 묻습니다. 무엇을 고칠지 메모에 적고 수정 요청을 "
                     "누르세요. ")
        elif decided is None:
            retry = ("The previous CP2 answer had no readable approve/revise/deny choice and was not "
                     "approved. ")
        else:
            return decided, note, asks, decision
    return "unreadable", note, asks, decision


def record_receipt(contract: dict, decision: dict, decided: str, note: str, asks: int, *,
                   warnings: list, refused_rows: list, refused: list, unsupported: list,
                   artifact_sha256: dict, unreported_outputs: dict, literal_checks: list,
                   source_verification: list, source_reports: list | None) -> None:
    # A continuation reuses a step together with its per-step salvage records (#90).
    salvage = contract.pop("result_salvage", None) or {}
    contract.setdefault("checkpoints", {})["cp2"] = {
        "gate": "research_evidence", "decision": decided, "choice": decision.get("choice"), "note": note,
        "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
        "plan_sha256": contract["plan_sha256"], "asks": asks,
        **({"warnings": warnings} if warnings else {}),
        **({"refused_rows": refused_rows} if refused_rows else {}),
        "refused_evidence": refused, "unsupported_claims": unsupported,
        "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs,
        **({"literal_checks": literal_checks} if literal_checks else {}),
        **({"source_verification": source_verification,
            "source_verification_reports": source_reports} if source_reports is not None else {}),
        **({"result_salvage": salvage} if salvage else {})}
