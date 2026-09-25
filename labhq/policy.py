"""Guardrails shared by the permission-prompt MCP tool, the HPC tool and the adapters.

Honest scope note: these rules stop *accidental* leaks and costly mistakes by well-behaved
agents. They are not a sandbox. The real guarantee for controlled-access data is procedural:
raw records are only touched inside HPC jobs whose outputs are aggregates.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from .settings import PolicySettings

READ_LIKE = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITE_LIKE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@dataclass
class Decision:
    action: str  # allow | deny | ask
    reason: str = ""


def _norm(p: str) -> str:
    return os.path.normpath(os.path.expanduser(p))


def restricted_paths(policy: PolicySettings) -> list[str]:
    return [_norm(z.path) for z in policy.data_zones if z.level == "restricted"]


def _strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def touches(obj: Any, paths: Iterable[str]) -> str | None:
    paths = list(paths)
    for s in _strings(obj):
        for p in paths:
            if p and p in s:
                return p
    return None


def claude_rule_path(p: str) -> str:
    """Path as Claude Code matches it in permission rules.

    Claude normalises Windows paths to POSIX form before matching (`C:\\Users\\x` → `/c/Users/x`).
    Verified on Claude 2.1.282 / Windows 11: only `Read(//c/Users/x/**)` blocked the read;
    `Read(/C:\\Users\\x/**)` and `Read(//C:/Users/x/**)` did not (tests/fixtures/real/claude_code).
    """
    s = p.replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):(?:/(.*))?$", s)
    if m:
        return f"/{m.group(1).lower()}" + (f"/{m.group(2)}" if m.group(2) else "")
    return s


def claude_settings(policy: PolicySettings) -> dict:
    """Claude Code settings: deny file tools on restricted zones.

    Absolute paths in permission rules take a leading `//` (e.g. `Read(//data/cohort/**)`).
    """
    deny: list[str] = []
    for p in restricted_paths(policy):
        for tool in ("Read", "Edit", "Write"):
            deny.append(f"{tool}(/{claude_rule_path(p)}/**)")
    return {"permissions": {"deny": deny}} if deny else {}


def evaluate_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: PolicySettings,
    allowed_roots: Iterable[str] = (),
) -> Decision:
    rp = restricted_paths(policy)
    hit = touches(tool_input, rp)

    if hit and tool_name in READ_LIKE | WRITE_LIKE:
        return Decision(
            "deny",
            f"'{hit}' is a restricted data zone. Do not read raw records into the conversation; "
            "submit an HPC job (hpc_submit) that writes aggregate/QC summaries and read those instead.",
        )

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        if hit:
            return Decision("ask", f"Bash touches restricted zone {hit}: `{cmd[:200]}`")
        for pat in policy.approvals.bash_ask_patterns:
            if re.search(pat, cmd):
                return Decision("ask", f"risky command (/{pat}/): `{cmd[:200]}`")
        return Decision("allow")

    if tool_name.startswith("mcp__"):
        if hit:
            return Decision("ask", f"MCP tool {tool_name} references restricted zone {hit}")
        return Decision("allow")

    roots = [_norm(r) for r in allowed_roots if r]
    if tool_name in WRITE_LIKE and roots:
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if fp and os.path.isabs(fp) and not any(_norm(fp).startswith(r) for r in roots):
            return Decision("ask", f"write outside workspace/project dirs: {fp}")

    return Decision("allow")


def walltime_hours(walltime: str) -> float:
    """'HH:MM:SS', 'HH:MM', 'D-HH:MM:SS' or plain hours → hours."""
    w = walltime.strip()
    days = 0.0
    if "-" in w:
        d, w = w.split("-", 1)
        days = float(d)
    parts = [float(x) for x in w.split(":")]
    if len(parts) == 1:
        h = parts[0]
    elif len(parts) == 2:
        h = parts[0] + parts[1] / 60
    else:
        h = parts[0] + parts[1] / 60 + parts[2] / 3600
    return days * 24 + h


def core_hours(cores: int, walltime: str) -> float:
    return max(1, int(cores)) * walltime_hours(walltime)


def hpc_needs_approval(cores: int, walltime: str, policy: PolicySettings) -> bool:
    thr = policy.approvals.hpc_core_hours_threshold
    return thr <= 0 or core_hours(cores, walltime) > thr
