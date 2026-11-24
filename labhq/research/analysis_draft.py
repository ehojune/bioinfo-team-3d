"""Select the analyst who writes the report and verify their collected draft."""

from dataclasses import dataclass
from hashlib import sha256

from ..evidence.literal_check import _source_bytes
from ..models import Task, TaskResult
from ..report_format import PAPER_RULE
from . import compact_workflow
from .continuation import _ancestors
from .contract import plan_sha256


@dataclass(frozen=True)
class AnalysisDraft:
    step_id: str
    path: str
    inline: bool

    def prompt(self) -> str:
        handoff = (
            "Also return your structured research result. " if self.inline else
            "Use the completed results and approved evidence in context. Write only the draft. "
            "Include downstream validation and independent results. Keep existing outputs and evidence intact. "
            "Confirm the draft path in your reply. "
        )
        return (
            f"\n\nIn this same session, after analysis and QC, save a draft for the PI as UTF-8 Markdown "
            f"at {self.path}. {handoff}"
            "Omit the draft if you have to stop and ask for a decision. "
            "Ground conclusions, numbers and result-table rows in your ledger or in the upstream ledgers: "
            "include the exact [[claim:<step_id>/<claim_id>]] anchor for each. "
            "Use only claims supported by collected artifacts or accepted sources. Link table claims to the "
            "matching tables. Use steps/<step_id>/outputs/<file> for every result link, including your own "
            "outputs and the graphical abstract. Place graphical_abstract.svg below the title "
            "if produced. Include key figures under results or explain any omission. State what could not be "
            "established and why. The CSO adds the approval and review record.\n"
            + PAPER_RULE
        )

    def followup(self, rid: str, analyst: TaskResult, context: str, runner: str | None, restricted: bool) -> Task:
        """Resume the analyst in a fresh workspace once the complete result set exists."""
        return Task(agent_id=analyst.agent_id, request_id=rid, resume_session_id=analyst.session_id,
                    prompt=self.prompt(), context=context,
                    meta={"kind": "analysis_draft", "parent_task": analyst.task_id, "outputs": [self.path],
                          "title": "분석 보고서 초안", "restricted_inputs": restricted,
                          **({"session_runner": runner} if runner else {})})

    def restored(self, receipt: dict, analyst: TaskResult | None) -> TaskResult | None:
        if (self.inline or not analyst or not analyst.ok or receipt.get("path") != self.path
                or receipt.get("parent_task") != analyst.task_id):
            return None
        result = TaskResult.model_validate(receipt.get("result"))
        if result.agent_id != analyst.agent_id or result.session_id != analyst.session_id:
            raise ValueError("the saved draft does not match the analysis session")
        return result

    def receipt(self, analyst: TaskResult, result: TaskResult) -> dict:
        fields = {"task_id", "agent_id", "ok", "workdir", "session_id", "outputs", "output_sha256"}
        return {"path": self.path, "parent_task": analyst.task_id,
                "result": result.model_dump(mode="json", include=fields)}

    def read(self, result: TaskResult | None) -> str:
        if not result or not result.ok or self.path not in result.outputs:
            raise ValueError("the analysis step did not deliver the draft")
        digest = result.output_sha256.get(self.path)
        if not digest:
            raise ValueError("the draft has no collected SHA-256")
        if not result.workdir:
            raise ValueError("the draft workspace is unavailable")
        try:
            data = _source_bytes(result.workdir, self.path)
            body = data.decode("utf-8-sig")
        except (OSError, TypeError, ValueError):
            raise ValueError("the collected draft is unreadable as UTF-8 Markdown") from None
        if sha256(data).hexdigest() != digest:
            raise ValueError("the draft differs from the collected SHA-256")
        if not body.strip():
            raise ValueError("the collected draft is empty")
        return body


def for_request(request: dict, plan: dict | None = None) -> AnalysisDraft | None:
    if not compact_workflow.enabled(request.get("research_options")):
        return None
    plan = plan if plan is not None else request.get("plan") or {}
    if plan.get("branches") or not (steps := plan.get("steps")):
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
