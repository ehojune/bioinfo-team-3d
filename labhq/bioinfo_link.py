"""Where the bioinfo-agent plugin checkout is (#613). `labhq init` and `labhq doctor` share this."""

from __future__ import annotations

import os
from pathlib import Path

ENV = "BIOINFO_AGENT_DIR"
SOURCE_LABEL = {
    "env": "환경변수 BIOINFO_AGENT_DIR",
    "common": "흔한 위치(D:/bioinfo-agent, ~/bioinfo-agent 등)",
    "wsl": "WSL 경로(/mnt/d/bioinfo-agent 등)",
}
NOT_CONNECTED = "bioinfo-agent 연결 안 됨 — "
FIX = ("bioinfo-agent 폴더 경로를 설정 파일의 engines.claude_code.env.BIOINFO_AGENT_DIR에 적고 runner를 다시 시작하세요"
       "(예: D:/bioinfo-agent, WSL에서 돌리면 /mnt/d/bioinfo-agent). 새 설정이면 `labhq init --bioinfo-agent <폴더>`.")


def _expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def is_plugin_dir(path: str | Path) -> bool:
    """The folder the claude_code adapter accepts as the bioinfo plugin: its manifest and the required skill."""
    try:
        folder = _expand(path)
        return ((folder / ".claude-plugin" / "plugin.json").is_file()
                and (folder / "skills" / "bioinfo-analyze" / "SKILL.md").is_file())
    except (OSError, ValueError):
        return False


def common_paths() -> list[tuple[str, str]]:
    """Places a bioinfo-agent checkout usually is, as (source, path). Tests replace this: it names real drives."""
    home = Path.home()
    found: list[tuple[str, str]] = []
    if os.name == "nt":
        found += [("common", "D:/bioinfo-agent"), ("common", "C:/bioinfo-agent")]
    found += [("common", str(home / "bioinfo-agent")), ("common", str(home / "Desktop" / "bioinfo-agent"))]
    found += [("wsl", "/mnt/d/bioinfo-agent"), ("wsl", "/mnt/c/bioinfo-agent")]
    return found


def detect(environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(path, source) of the first hit, in this order: an existing BIOINFO_AGENT_DIR, the common places, a WSL path.

    The variable wins even when its folder is not a plugin (the user set it; doctor reports the problem)."""
    configured = (environ if environ is not None else os.environ).get(ENV, "").strip()
    if configured:
        return configured, "env"
    for source, path in common_paths():
        if is_plugin_dir(path):
            return path, source
    return None


def configured_dir(env: dict[str, str]) -> str:
    """The folder a runner would use: BIOINFO_AGENT_DIR from the engine env, else the process environment."""
    return (env.get(ENV) or "").strip()
