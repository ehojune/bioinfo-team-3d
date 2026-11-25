"""Planning and analysis guidance for opt-in unbranched research."""
from typing import Any

from ..report_format import ABSTRACT_PLAN_RULE


def enabled(options: dict[str, Any] | None) -> bool:
    """Only the creation-time option enables compact planning, including after resume."""
    return (options or {}).get("compact_workflow") is True


def unbranched_plan_limit(max_steps: int, options: dict[str, Any] | None) -> int:
    return min(max_steps, 3) if enabled(options) else max_steps


def plan_template(template: str) -> str:
    return (template
            .replace(" steps, exactly as before.", " steps.")
            .replace("Keep the biological interpretation, an independent technical or scientific QC step, the ",
                     "For a branched plan, keep biological interpretation and independent technical or scientific "
                     "QC separate. Keep the ")
            .replace("Put QC after data generation.",
                     "For a plan without branches, include QC and interpretation in the analysis step as applicable, "
                     "with QC after data generation. For a branched plan, put QC after data generation.")
            .replace(ABSTRACT_PLAN_RULE,
                     "For an unbranched plan, use one write-capable analysis step after all other steps. Make "
                     "every other step its direct or indirect dependency. Have it create the graphical abstract "
                     "and its code as declared outputs. In each producing step, declare the UTF-8 tables, summaries "
                     "and logs that will contain the literal report values and method or QC facts. Put their exact "
                     "outputs/<file> paths in outputs and in the step instructions. Plan claim_ids that cover the "
                     "expected report statements and table rows, including input, methods, QC and comparisons. "
                     "Keep Korean "
                     "labels as SVG <text> and cover question, data, methods and findings. Do not add a separate "
                     "writing step: labhq requests the draft from the analysis session during dispatch. "
                     "For a branched plan: " + ABSTRACT_PLAN_RULE))


ANALYSIS_INSTRUCTIONS = (
    "\n\nWhen your assigned work includes analysis, QC, or interpretation, cross-check sample information against "
    "public records, record the controls and expected direction, and compare results with published values in a "
    "table. Keep these checks within the approved design. When information is missing or a check does not apply, "
    "record the reason. Before returning your ledger, write and read back the declared evidence outputs. Put "
    "counts, denominators, units, conversions and rounding used in the report in the collected text or tables. "
    "An aggregate belongs in a summary that prints it, not just in its underlying detail rows or plot. Bind each "
    "evidence row via artifact_refs to the file that contains the value, and set source.locator to its exact "
    "row and column, named field, or line range. Record the value in evidence.quantities at that location. "
    "Use links with the required support or contradiction relation for each planned claim. For comparisons, "
    "link the measured result and the external benchmark separately and record the access and comparison limits."
)
