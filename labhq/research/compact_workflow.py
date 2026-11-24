"""Planning and analysis guidance for opt-in unbranched research."""
from typing import Any


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
                     "with QC after data generation. For a branched plan, put QC after data generation."))


ANALYSIS_INSTRUCTIONS = (
    "\n\nWithin the approved design, cross-check sample information against public records, record the controls "
    "and expected direction, and compare results with published values in a table. When information is missing "
    "or a check does not apply, record the reason."
)
