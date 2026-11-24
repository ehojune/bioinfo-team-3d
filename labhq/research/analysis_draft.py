"""Select the analyst who writes the report and verify their collected draft."""

import asyncio
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..evidence import report_consistency
from ..evidence.literal_check import MAX_SOURCE_BYTES, _source_bytes
from ..models import Task, TaskResult
from ..orchestrator.step_execution import upstream_inputs
from ..recruit.provenance import restricted_input_steps
from ..report_figures import image_links
from ..report_format import PAPER_RULE
from ..request_persistence import persist_request
from ..result_storage import controlled_request
from ..runner import report_artifacts
from . import compact_workflow
from .approval_notes import CP2_REPORT_NOTE_RULE, approval_note_block
from .continuation import _ancestors
from .contract import plan_sha256


@dataclass(frozen=True)
class AnalysisDraft:
    step_id: str
    path: str
    inline: bool

    def prompt(self) -> str:
        handoff = ("Return the result ledger. Put graphical_abstract.svg below the title and include key figures "
                   "if produced." if self.inline else "Read the final results from inputs/<step_id>/. Write the draft "
                   "only. Omit the figures from individual steps and explain the omission.")
        return (
            f"\n\nIn this session, after analysis and QC, save a draft for the PI as UTF-8 Markdown at {self.path}. "
            f"{handoff} Omit the draft if you have to stop for a decision. Use only collected artifacts and "
            "accepted sources. Include exact [[claim:<step_id>/<claim_id>]] anchors for conclusions, numbers "
            "and table rows. Link table claims to their tables. Use steps/<step_id>/outputs/<file> for every "
            "result link, including your own outputs. State what could not be established and why. "
            "The CSO adds approval and review records.\n" + PAPER_RULE
        )

    def followup(self, rid: str, analyst: TaskResult, context: str, runner: str | None, restricted: bool,
                 transfer: bool, inputs: dict) -> Task:
        """Resume the analyst in a fresh workspace once the complete result set exists."""
        return Task(agent_id=analyst.agent_id, request_id=rid, resume_session_id=analyst.session_id,
                    prompt=self.prompt(), context=context,
                    meta={**inputs, "kind": "analysis_draft", "parent_task": analyst.task_id, "outputs": [self.path],
                          "title": "분석 보고서 초안", "restricted_inputs": restricted,
                          **({"session_runner": runner} if runner else {}),
                          **transfer_meta(self, transfer)})

    def restored(self, receipt: dict, analyst: TaskResult | None) -> TaskResult | None:
        if (self.inline or not analyst or not analyst.ok or receipt.get("path") != self.path
                or receipt.get("parent_task") != analyst.task_id):
            return None
        result = TaskResult.model_validate(receipt.get("result"))
        if result.agent_id != analyst.agent_id or result.session_id != analyst.session_id:
            raise ValueError("the saved draft does not match the analysis session")
        return result

    def receipt(self, analyst: TaskResult, result: TaskResult) -> dict:
        return {"path": self.path, "parent_task": analyst.task_id, "result": result.model_dump(
            mode="json", include={"task_id", "agent_id", "ok", "workdir", "session_id", "outputs", "output_sha256"})}

    def read(self, result: TaskResult | None, load=None) -> str:
        if not result or not result.ok or self.path not in result.outputs:
            raise ValueError("the analysis step did not deliver the draft")
        if not (digest := result.output_sha256.get(self.path)):
            raise ValueError("the draft has no collected SHA-256")
        if load:
            body = report_artifacts.transferred_text(load(result.task_id), self.path, digest, MAX_SOURCE_BYTES)
        else:
            try:
                data = _source_bytes(result.workdir, self.path)
                body = report_artifacts.verified_text(data, digest, MAX_SOURCE_BYTES).removeprefix("\ufeff")
            except (OSError, TypeError, UnicodeError):
                raise ValueError("the collected draft is unreadable as UTF-8 Markdown") from None
        if not body.strip():
            raise ValueError("the collected draft is empty")
        if not self.inline and (images := image_links(body)):
            for image in reversed(images):
                note = "단계별 그림은 최종 보고서에서 생략했습니다." if image == images[0] else ""
                body = body[:image.start] + note + body[image.end:]
        return body


def artifact_loader(hub, request: dict):
    """Use the private receiver only where the gateway and data policy allow it."""
    steps = (request.get("plan") or {}).get("steps") or []
    if not controlled_request(request, hub.s) and not restricted_input_steps(steps, [], [], hub.s):
        return getattr(hub, "report_artifacts", None)


async def collect(hub, rid: str, draft: AnalysisDraft, results: dict[str, TaskResult], *,
                  cp2_report: str, cp2_notes: list[tuple[str, str]], render_evidence, restricted_inputs,
                  session_origin, free_session, run_step, budget_denials) -> str | None:
    """Read the collected draft, resuming its analyst if needed. None means the follow-up budget was denied."""
    request = hub.requests[rid]
    contract = request["research_contract"]
    load = artifact_loader(hub, request)
    analyst = results.get(draft.step_id)
    saved = draft.restored(contract.get("analysis_draft") or {}, analyst)
    if draft.inline or saved is not None:
        return await asyncio.to_thread(draft.read, saved or analyst, load)

    if not (analyst and analyst.ok and analyst.session_id and hub.supports_resume(analyst.agent_id)):
        raise ValueError("the analysis session cannot be resumed after the remaining steps")
    origin = session_origin(rid, analyst.agent_id, analyst.session_id)
    session, _ = await free_session(analyst.agent_id, analyst.session_id, analyst.workdir,
                                    rid=rid, step="analysis_draft")
    if session != analyst.session_id or (getattr(hub, "store", None) is not None and not origin):
        raise ValueError("the original analysis session or its runner is unavailable")
    restricted = restricted_inputs()
    context = render_evidence() + "\n\n" + cp2_report + approval_note_block("CP2", cp2_notes, CP2_REPORT_NOTE_RULE)
    inputs = upstream_inputs(results, results)
    if any(result.ok and result.outputs and sid not in inputs["upstream_artifacts"] for sid, result in results.items()):
        raise ValueError("a collected report input has no workspace")
    author = await run_step(draft.followup(rid, analyst, context, origin, restricted, bool(load), inputs))
    if rid in budget_denials:
        return None
    if author.ok and author.session_id != session:
        raise ValueError("the draft did not come from the original analysis session")
    body = await asyncio.to_thread(draft.read, author, load)
    contract["analysis_draft"] = draft.receipt(analyst, author)
    await persist_request(hub, rid)
    return body


def transfer_meta(draft: AnalysisDraft | None, transfer: bool) -> dict:
    return {report_artifacts.RESULT_FIELD: draft.path if draft else True} if transfer else {}


def tables_for(report: str, results: dict, load) -> dict[str, str | None]:
    wanted = {link.replace("\\", "/").removeprefix("./") for link in report_consistency._targets(report)
              if PurePosixPath(link).suffix.casefold() in report_consistency.TABLE_SUFFIXES and "://" not in link}
    tables = dict.fromkeys(sorted(wanted))
    for sid, result in results.items():
        matches = {link: path for path in result.get("outputs", []) if (link := f"steps/{sid}/{path}") in wanted}
        if not matches or not result.get("ok"):
            continue
        payload = load(result.get("task_id"))
        for link, path in matches.items():
            try:
                tables[link] = report_artifacts.transferred_text(
                    payload, path, result.get("output_sha256", {}).get(path), report_consistency.MAX_TABLE_BYTES)
            except ValueError:
                pass
    return tables


async def report_tables(hub, request: dict, report: str, results: dict) -> tuple[bool, dict[str, str | None]]:
    """Report whether the compact draft is being finalized and read its linked tables."""
    draft = request.get("outcome") == "research_reported" and for_request(request) is not None
    return draft, await check_tables(hub, request, report, results, use_transfer=draft)


async def check_tables(hub, request: dict, report: str, results: dict, *, use_transfer: bool = True) -> dict:
    load = artifact_loader(hub, request) if use_transfer else None
    if load:
        return await asyncio.to_thread(tables_for, report, results, load)
    return report_consistency.tables_for(report, results)


def for_request(request: dict, plan: dict | None = None) -> AnalysisDraft | None:
    plan = plan if plan is not None else request.get("plan") or {}
    if (not compact_workflow.enabled(request.get("research_options"))
            or plan.get("branches") or not (steps := plan.get("steps"))):
        return None
    ancestors = _ancestors(steps)
    # Only a writer with every other step in its input closure can safely draft on its first turn.
    writer = max(steps, key=lambda step: (step.get("phase") == "analysis", len(ancestors[step["id"]])))
    sid = writer["id"]
    round_no = (request.get("research_contract") or {}).get("round") or 1
    return AnalysisDraft(sid, f"outputs/labhq-analysis-draft-{round_no}-{plan_sha256(plan)[:12]}.md",
                         len(ancestors[sid]) == len(steps) - 1)


def new_round_rerun(request: dict, plan: dict) -> dict[str, str]:
    draft = for_request(request, plan)
    return {draft.step_id: "the analysis session writes a fresh report draft for this round"} if draft else {}
