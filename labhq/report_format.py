"""Paper report conventions and the scientific/operational appendix boundary (#609)."""
from __future__ import annotations

import re

from . import edition_sources

PAPER_RULE = '''Use the basic paper format in this order: "제목", "요약(Abstract)", "서론", "방법", "결과",
"논의", "결론", "참고", "부록". Start with the report's first heading: no preamble.
Use one # title and ## for each section.
The abstract summarizes the question and findings; the introduction states the background and question.
Methods include seeds, tool/data versions and assumptions; results contain evidence, figures and tables.
Discussion covers interpretation, reviewer concerns, limitations and what would change the conclusion.
The conclusion gives the answer and concrete next steps. References name only supplied data accessions,
PMIDs and DOIs, never invented citations. The scientific appendix contains only checklist tables, review
verdict/issues and assumptions. Execution details, costs, warnings and bundle conversion belong to the
separate operation record. Keep easy Korean (#569), about 4,000 characters unless results need more;
state each result statistic once, in the results. Format comes first; do not invent background or findings.
'''

ABSTRACT_PLAN_RULE = (
    "Assign a final report step to a write-capable worker (for example an analyst), depending on the analysis "
    "steps. It creates outputs/graphical_abstract.png (or .svg) with code and declares it in outputs, along with "
    "the code. Use short Korean labels for question -> data -> methods -> key findings. CSO synthesis is "
    "read-only and only links this artifact; do not assign file creation to CSO. In a re-plan retain an already "
    "completed abstract step if its findings still hold."
)

LEGACY_EXECUTION_TITLE = "## 부록: 실행 기록"


def split_appendix(text: str) -> tuple[str, str]:
    """Read legacy operation appendices without presenting them as new scientific appendices."""
    if LEGACY_EXECUTION_TITLE in text:
        science, operation = text.split(LEGACY_EXECUTION_TITLE, 1)
        return science.strip(), "## 실행 기록" + operation
    if text.lstrip().startswith("## 실행 기록"):
        return "", text
    return text, ""


def scientific_appendix(existing: str, req: dict, review: dict | None, checklist: str) -> str:
    parts = [re.sub(r"^## 부록\s*\n?", "", existing.strip()).strip()]
    if checklist:
        parts.append(checklist)
    if review:
        lines = ["### 리뷰", f"- 판정: {review.get('verdict') or '없음'}"]
        for issue in review.get("issues") or []:
            lines.append(f"- {issue.get('priority', 'P1')} · {issue.get('step_id') or '-'}: "
                         f"{issue.get('problem') or ''} → {issue.get('request') or ''}")
        parts.append("\n".join(lines))
    assumptions = (req.get("plan") or {}).get("assumptions") or []
    if assumptions:
        parts.append("### 가정\n" + "\n".join(
            f"- {a.get('choice') or a.get('assumption') or ''}: {a.get('reason') or ''}"
            if isinstance(a, dict) else f"- {a}" for a in assumptions))
    return "## 부록\n\n" + "\n\n".join(p for p in parts if p)


def add_references(report: str, appendix: str, references: list) -> str:
    """Fill a new paper's reference section using the same extraction as SOURCES.md; leave old reports intact."""
    heading = re.search(r"(?m)^## 참고[ \t]*$", report)
    if not heading:
        return report
    tail = report[heading.end():]
    end = re.search(r"(?m)^## |^<!-- LABHQ_BENCH_RESULT -->", tail)
    stop = heading.end() + (end.start() if end else len(tail))
    found = edition_sources.collect([report, appendix], references)
    listing = edition_sources.render(found).removeprefix("# 출처·인용\n\n")
    listing = re.sub(r"(?m)^## ", "### ", listing).strip()
    # Keep model-written citations as well as the deterministic identifiers.
    citations = report[heading.end():stop].strip()
    content = "\n\n".join(p for p in (citations, listing) if p)
    return report[:heading.end()] + "\n\n" + content + "\n\n" + report[stop:]
