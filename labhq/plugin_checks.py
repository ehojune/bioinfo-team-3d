"""Stdlib-only plugin checks shared by adapter preflight and WSL bootstrap."""
import json
from pathlib import Path


def read_plugin_manifest(path: Path) -> dict | None:
    try:
        data = json.loads((path / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def skill_file(path: Path, name: str) -> Path:
    return path / "skills" / name / "SKILL.md"
