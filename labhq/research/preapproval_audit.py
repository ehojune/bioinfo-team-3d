"""Bind the evidence card to the draft and outputs inspected by the reviewer."""

import asyncio
import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path

from ..evidence import report_consistency
from ..evidence.audit import MISMATCH, OK, verify_request
from ..evidence.report_check import check_report
from . import analysis_draft


OPTION = "audit_before_cp2"


def capture(request: dict) -> None:
    """Keep the switch separate from the frozen plan and preserve it on resume."""
    if os.environ.get("LABHQ_AUDIT_BEFORE_CP2") == "1":
        request.setdefault("research_options", {}).setdefault(OPTION, True)


def enabled(request: dict) -> bool:
    return (request.get("research_options") or {}).get(OPTION) is True


def approval_section(note: str) -> str:
    """Present the PI's annotation separately from the audited scientific claims."""
    literal = re.sub(r"([\\`*_\[\]#!])", r"\\\1", escape(note, quote=False))
    quoted = "\n".join(f"> {line}" for line in literal.splitlines())
    return "## PI 승인 메모\n\n" + quoted


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditDraft:
    body: str
    path: str
    binding: dict
    checks: dict
    restricted: bool
    workdirs: list[str]

    def card(self, reviewer: str, review: dict) -> dict:
        return {**self.binding, "reviewer": reviewer, "verdict": review["verdict"], "issues": review["issues"],
                "checks": self.checks}


async def prepare(hub, request: dict, draft: analysis_draft.AnalysisDraft, body: str, results: dict, *,
                  claim_context: dict, earlier_notes: list[tuple[str, str]], restricted: bool) -> AuditDraft:
    contract = request["research_contract"]
    analyst = results[draft.step_id]
    collected = draft.restored(contract.get("analysis_draft") or {}, analyst) or analyst
    draft_hash = sha256(body.encode("utf-8")).hexdigest()
    if draft_hash != collected.output_sha256[draft.path]:
        raise ValueError("Audit draft differs from the collected SHA-256")
    outputs = {sid: {path: result.output_sha256.get(path) for path in result.outputs}
               for sid, result in results.items() if result.ok}
    complete = all(re.fullmatch(r"[0-9a-f]{64}", digest or "")
                   for files in outputs.values() for digest in files.values())
    inventory = {sid: results[sid].model_dump(include={"task_id", "workdir", "workdir_id", "ok", "outputs",
                                                       "output_sha256"}) for sid in outputs}
    if not draft.inline:
        inventory["@draft"] = {"task_id": collected.task_id, "workdir": collected.workdir,
                               "workdir_id": Path(collected.workdir).name, "outputs": [draft.path],
                               "output_sha256": {draft.path: collected.output_sha256[draft.path]}}
    observed = await asyncio.to_thread(verify_request, {"results": inventory}, hub.s)
    for file in observed["files"]:
        if file["status"] == MISMATCH:
            raise ValueError(f"Collected output changed: {file['step_id']}/{file['path']}")
    verified = complete and all(file["status"] == OK for file in observed["files"])
    tables = await analysis_draft.check_tables(hub, request, body, {sid: inventory[sid] for sid in outputs})
    checks = {"claims": check_report(body, **claim_context),
              "table_consistency": report_consistency.check(body, tables)}
    binding = {"plan_sha256": contract["plan_sha256"],
               "artifacts_sha256": _digest(outputs) if complete else None,
               "artifacts_verified": verified,
               "draft_sha256": draft_hash,
               "evidence_sha256": _digest({**claim_context, "checks": checks, "earlier_notes": earlier_notes})}
    workdirs = list(dict.fromkeys(result.workdir for result in [*results.values(), collected]
                                if result.ok and result.outputs and result.workdir))
    return AuditDraft(body, str(Path(collected.workdir) / draft.path), binding, checks, restricted, workdirs)
