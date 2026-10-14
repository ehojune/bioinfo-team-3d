"""A small local humanizer expression check, only at the final report boundary."""
from pathlib import Path
import re


def humanizer_skill() -> Path | None:
    for root in (Path.home() / ".agents", Path.home() / ".codex"):
        path = root / "skills" / "humanizer" / "SKILL.md"
        if path.is_file():
            return path
    return None


def check_report(report: str) -> tuple[str, str]:
    """Keep quotes, code, numbers and findings intact; never run this for chat messages.

    This is the skill's three literal expression rules, not its full 40-pattern diagnosis.
    """
    if humanizer_skill() is None:
        return report, ""
    changed = 0
    lines, fenced = [], False
    for line in report.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and not line.lstrip().startswith((">", "```", "~~~")):
            # Exclude inline code and quotations as well as fenced blocks.
            parts = re.split(r'(`[^`]*`|"[^"\n]*"|“[^”\n]*”)', line)
            for i in range(0, len(parts), 2):
                for before, after in (("되어진다", "된다"), ("가지고 있다", "갖고 있다"), ("에 있어서", "에서")):
                    changed += parts[i].count(before)
                    parts[i] = parts[i].replace(before, after)
            line = "".join(parts)
        lines.append(line)
    result = f"고친 {changed}곳" if changed else "통과"
    return "".join(lines), f"humanizer 문체 점검(표현 3종): {result}"
