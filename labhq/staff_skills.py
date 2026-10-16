"""First-party skills that labhq copies into each named employee's workspace."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .models import AgentSpec

if TYPE_CHECKING:
    from .runner.workspace import TaskWorkspace


SKILLS_DIR = Path(__file__).resolve().with_name("skills")
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


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


def install_required_skills(workspace: "TaskWorkspace", agent: AgentSpec) -> str | None:
    """Install every first-party requirement for both Claude and Codex, or fail closed."""
    for name in bundled_requirements(agent):
        source = bundled_skill_source(name)
        if source is None:
            return f"required bundled skill '{name}' is missing or unreadable"
        if error := workspace.install_skill(source, source_label="bundled skill"):
            return f"required bundled skill '{name}' could not be installed: {error}"
    return None
