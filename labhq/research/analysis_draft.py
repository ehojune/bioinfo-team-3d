"""Select the analyst who writes the report and verify their collected draft."""

from dataclasses import dataclass
from hashlib import sha256

from ..evidence.literal_check import _source_bytes
from ..report_format import PAPER_RULE
from . import compact_workflow
from .continuation import _ancestors
from .contract import plan_sha256


@dataclass(frozen=True)
class AnalysisDraft:
    step_id: str
    path: str

    def prompt(self) -> str:
        return (
            f"\n\nIn this same session, after analysis and QC, save a draft for the PI as UTF-8 Markdown "
            f"at {self.path}, in addition to returning your structured research result. "
            "Omit the draft if you have to stop and ask for a decision. "
            "Ground conclusions, numbers and result-table rows in your ledger or in the upstream ledgers: "
            "include the exact [[claim:<step_id>/<claim_id>]] anchor for each. "
            "Use only claims supported by collected artifacts or accepted sources. Link table claims to the "
            "matching tables using outputs/<file> for your step and steps/<step_id>/outputs/<file> for upstream "
            "steps. Use the same links for result figures. Place your graphical_abstract.svg below the title "
            "if produced. Include key figures under results or explain any omission. State what could not be "
            "established and why. The CSO adds the approval and review record.\n"
            + PAPER_RULE
        )

    def read(self, results) -> str:
        result = results.get(self.step_id)
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
    # Prefer the analysis step that can see the most upstream evidence. Phase labels do not gate the handoff.
    writer = max(steps, key=lambda step: (step.get("phase") == "analysis", len(ancestors[step["id"]])))
    round_no = (request.get("research_contract") or {}).get("round") or 1
    return AnalysisDraft(writer["id"], f"outputs/labhq-analysis-draft-{round_no}-{plan_sha256(plan)[:12]}.md")


def new_round_rerun(request: dict, plan: dict) -> dict[str, str]:
    draft = for_request(request, plan)
    return {draft.step_id: "the analysis session writes a fresh report draft for this round"} if draft else {}
