"""First-party skills that labhq copies into each named employee's workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .models import AgentSpec

if TYPE_CHECKING:
    from .runner.workspace import TaskWorkspace


SKILLS_DIR = Path(__file__).resolve().with_name("skills")
BUNDLED_PLUGIN_DIR = SKILLS_DIR
BUNDLED_PLUGIN_NAME = "labhq-bundled-skills"
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

READ_ONLY_SKILL_RULES = {
    "humanizer": (
        "- 한국어 금지 표현 `되어진다`, `가지고 있다`, `에 있어서`는 0회로 둡니다. "
        "`비유의`는 `통계적으로 유의하지 않음`, `유의차`는 `통계적으로 유의한 차이`, "
        "`미보정`은 `보정 전`으로 씁니다. 쉼표는 한 줄 0~1개를 기본으로 하고 짧은 문장을 잇지 않습니다."
    ),
    "graphical-abstract": (
        "- 그래픽 초록은 질문 → 자료 → 방법 → 핵심 결과 → 결론 순서로 검토합니다. SVG에는 `<title>`과 "
        "`<desc>` 및 읽을 수 있는 text가 있어야 합니다. 두 방향·세 관점으로 검토하고, 렌더한 미리보기로 "
        "잘림·겹침을 확인합니다(결과물은 SVG만)."
    ),
}


def bundled_skill_source(name: str) -> Path | None:
    """Return a readable first-party skill folder, never a caller-chosen path."""
    if not SKILL_NAME.fullmatch(name):
        return None
    source = SKILLS_DIR / name
    return source if (source / "SKILL.md").is_file() else None


def is_bundled_skill(name: str) -> bool:
    return bundled_skill_source(name) is not None


def bundled_requirements(agent: AgentSpec) -> list[str]:
    return [name for name in agent.required_skills if ":" not in name]


def bundled_read_only_rules(names: list[str]) -> str:
    """Prompt-only subset for runs where skill tools and plugins stay disabled."""
    rules = [READ_ONLY_SKILL_RULES[name] for name in names if name in READ_ONLY_SKILL_RULES]
    return ("\n\n## 읽기 전용 글쓰기 규칙\n" + "\n".join(rules)) if rules else ""


def install_required_skills(workspace: "TaskWorkspace", agent: AgentSpec) -> str | None:
    """Install every first-party requirement for both Claude and Codex, or fail closed."""
    for name in bundled_requirements(agent):
        source = bundled_skill_source(name)
        if source is None:
            return f"required bundled skill '{name}' is missing or unreadable"
        if error := workspace.install_skill(source, source_label="bundled skill"):
            return f"required bundled skill '{name}' could not be installed: {error}"
    return None
