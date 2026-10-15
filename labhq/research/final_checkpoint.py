"""CP4 binds the existing PI report review to one immutable research draft."""
from __future__ import annotations

import copy
import hashlib
import json
import time


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def enabled(req: dict) -> bool:
    contract = req.get("research_contract") or {}
    return (contract.get("final_checkpoint") is True or
            (req.get("research_options") or {}).get("final_checkpoint") is True)


def payload(req: dict) -> dict:
    contract = req.get("research_contract") or {}
    results = req.get("results") or {}
    return {"plan_sha256": contract.get("plan_sha256"),
            "report": req.get("report") or "", "report_appendix": req.get("report_appendix") or "",
            **({"execution_record": req["execution_record"]} if "execution_record" in req else {}),
            "review": contract.get("review"), "report_check": contract.get("report_check"),
            "cp2": (contract.get("checkpoints") or {}).get("cp2"),
            "claims": {step["id"]: (results.get(step["id"]) or {}).get("structured")
                       for step in (req.get("plan") or {}).get("steps") or []}}


def hashes(content: dict) -> dict:
    audit_fields = ("report_appendix", "review", "report_check", "cp2")
    if "execution_record" in content:  # #609: keep old frozen drafts byte-compatible
        audit_fields += ("execution_record",)
    return {"target_sha256": digest(content), "report_sha256": digest(content["report"]),
            "audit_sha256": digest({k: content[k] for k in audit_fields}),
            "claims_sha256": digest(content["claims"])}


def draft_errors(req: dict) -> list[str]:
    contract = req.get("research_contract") or {}
    draft = contract.get("final_draft") or {}
    review, check = contract.get("review") or {}, contract.get("report_check") or {}
    errors = []
    if contract.get("schema_version") != 2 or contract.get("final_checkpoint") is not True:
        errors.append("CP4 contract is missing")
    if review.get("verdict") != "accept" or any(i.get("priority") == "P1" for i in review.get("issues") or []):
        errors.append("review is not accept")
    if "problems" not in check or check["problems"]:
        errors.append("report check is incomplete")
    if (not draft or draft.get("request_id") != req.get("id")
            or type(draft.get("revision")) is not int or draft["revision"] < 1):
        errors.append("draft is missing or belongs to another request")
    current = payload(req)
    if any(draft.get(k) != v for k, v in hashes(current).items()) or draft.get("content") != current:
        errors.append("draft hash mismatch")
    return errors


def receipt_errors(req: dict) -> list[str]:
    errors = draft_errors(req)
    contract = req.get("research_contract") or {}
    draft, receipt = contract.get("final_draft") or {}, contract.get("final_receipt") or {}
    expected = {"gate": "research_final", "request_id": req.get("id"),
                "revision": draft.get("revision"), "target_sha256": draft.get("target_sha256"),
                "decision": "accepted", "decider": "PI", "delegation_scope": []}
    if any(receipt.get(k) != v for k, v in expected.items()) or not receipt.get("decided_at"):
        errors.append("accepted CP4 receipt is missing or stale")
    elif not draft.get("created_at", float("inf")) <= receipt["decided_at"] < draft.get("deadline_at", 0):
        errors.append("CP4 receipt is outside the draft decision window")
    return errors


def freeze(req: dict, timeout_s: float) -> None:
    contract = req["research_contract"]
    if contract.get("final_draft"):
        return  # no CP4 revision or second synthesis in this lane
    content = payload(req)
    contract["final_draft"] = {"request_id": req["id"], "revision": 1,
        **hashes(content), "content": copy.deepcopy(content),
        "created_at": time.time(), "deadline_at": time.time() + timeout_s}


def decide(req: dict, decision: str, *, revision=None, target_sha256=None, now=None) -> dict:
    contract = req["research_contract"]
    draft = contract.get("final_draft") or {}
    now = time.time() if now is None else now
    if decision not in {"accepted", "rejected", "timed_out", "cancelled"}:
        raise ValueError("unknown CP4 decision")
    if decision in {"accepted", "rejected"}:
        if req.get("status") != "review_pending":
            if (decision == "accepted" and req.get("status") == "done" and not receipt_errors(req)
                    and revision == draft.get("revision") and target_sha256 == draft.get("target_sha256")):
                return contract["final_receipt"]
            raise ValueError("CP4 검토 대기 상태가 아닙니다")
        errors = draft_errors(req)
        if errors or revision != draft.get("revision") or target_sha256 != draft.get("target_sha256"):
            raise ValueError("CP4 초안이 바뀌었거나 검증되지 않았습니다. 보고서를 다시 확인하세요")
        if now >= draft.get("deadline_at", 0):
            raise ValueError("CP4 검토 기한이 지났습니다")
    receipt = {"gate": "research_final", "request_id": req["id"], "revision": draft.get("revision"),
               "target_sha256": draft.get("target_sha256"), "decision": decision,
               "decider": "PI" if decision in {"accepted", "rejected", "cancelled"} else "system",
               "decided_at": now, "delegation_scope": []}
    contract["final_receipt"] = receipt
    req.update(status={"accepted": "done", "rejected": "rejected", "timed_out": "failed",
                       "cancelled": "cancelled"}[decision], outcome="research_final_" + decision)
    return receipt


def reconcile(req: dict) -> None:
    """Never persist a successful CP4 request with a missing or stale receipt."""
    if not enabled(req):
        return
    contract = req.get("research_contract") or {}
    if ((contract.get("final_receipt") or {}).get("decision") == "accepted" and receipt_errors(req)):
        contract["final_receipt"]["decision"] = "stale"
    if contract.get("final_draft") and req.get("status") in {"running", "interrupted", "review_pending"}:
        if not receipt_errors(req):
            req.update(status="done", pi_review_pending=False,
                       pi_reviewed_at=contract["final_receipt"]["decided_at"])
        else:
            req.update(status="review_pending", pi_review_pending=True, pi_reviewed_at=None)
    if req.get("status") == "done" and receipt_errors(req):
        req.update(status="review_pending" if contract.get("final_draft") else "failed",
                   outcome="research_final_unverified", pi_reviewed_at=None, pi_review_pending=True)
        if contract.get("final_receipt"):
            contract["final_receipt"]["decision"] = "stale"


def summary(req: dict) -> dict | None:
    contract = req.get("research_contract") or {}
    draft = contract.get("final_draft")
    if not draft:
        return None
    return {k: draft[k] for k in ("revision", "target_sha256", "report_sha256", "audit_sha256",
                                  "claims_sha256", "deadline_at")} | {"receipt": contract.get("final_receipt")}
