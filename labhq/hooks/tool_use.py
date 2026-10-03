"""Record Claude write-tool PostToolUse receipts without blocking the staff run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..runner.workspace import WRITE_TOOLS, record_tool_use


def record_post_tool_use(payload: Mapping[str, Any], env: Mapping[str, str] = os.environ,
                         *, now_ns: int | None = None) -> bool:
    tool_use_id, tool_name = payload.get("tool_use_id"), payload.get("tool_name")
    workdir, task_id = env.get("LABHQ_WORKDIR"), env.get("LABHQ_TASK_ID")
    if (not isinstance(tool_use_id, str) or not tool_use_id or tool_name not in WRITE_TOOLS
            or not workdir or not task_id):
        return False
    record_tool_use(Path(workdir), task_id, tool_use_id, tool_name, time.time_ns() if now_ns is None else now_ns)
    return True


def add_claude_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """Merge labhq's PostToolUse recorder with any configured Claude hooks."""
    merged = dict(settings)
    hooks = {key: list(value) for key, value in (merged.get("hooks") or {}).items()}
    command = subprocess.list2cmdline([sys.executable, "-m", "labhq.hooks.tool_use"])
    hooks.setdefault("PostToolUse", []).append({
        "matcher": "Write|Edit|Bash|PowerShell",
        "hooks": [{"type": "command", "command": command}],
    })
    merged["hooks"] = hooks
    return merged


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, Mapping):
            record_post_tool_use(payload)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
