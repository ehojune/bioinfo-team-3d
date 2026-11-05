"""Korean result QC at the final report boundary."""

from pathlib import Path

from .korean_qc import check_document
from .staff_skills import bundled_skill_source


def humanizer_skill() -> Path | None:
    source = bundled_skill_source("humanizer")
    return source / "SKILL.md" if source is not None else None


def check_report(report: str) -> tuple[str, str]:
    """Measure without rewriting; quotes and code are excluded by the shared QC."""
    if humanizer_skill() is None:
        return report, ""
    result = check_document(report)
    if result["status"] == "PASS":
        return report, "한국어 문서 검사: PASS (따옴표 밖 금지 표현 0건)"
    details = ", ".join(f"{name} {count}건" for name, count in result["must_zero"].items())
    return report, f"한국어 문서 검사: WARN ({details})"
