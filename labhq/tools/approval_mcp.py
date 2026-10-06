"""labhq-approval MCP server, used with Claude Code's `--permission-prompt-tool`.

Claude Code calls `approval_prompt(tool_name, input)` for any tool use not pre-approved by
--allowedTools. We apply policy.evaluate_tool; "ask" goes to the PI via the runner's broker.
The return value must be a JSON *string*: {"behavior": "allow", "updatedInput": {...}}
or {"behavior": "deny", "message": "..."}.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from ..policy import WRITE_LIKE, evaluate_tool
from ..private_paths import gate_private_paths, resolve_private_paths
from ..settings import Settings
from ..util import short
from ._mcpcompat import NOT_EVIDENCE, make_server

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")
WORKDIR_INPUT = os.environ.get("LABHQ_WORKDIR", ".")
WORKDIR = str(Path(WORKDIR_INPUT).resolve())
ENVIRONMENT_STEP = os.environ.get("LABHQ_ENVIRONMENT_STEP") == "1"
EXTRA_ROOTS = [p for p in os.environ.get("LABHQ_EXTRA_ROOTS", "").split(os.pathsep) if p]
# The runner sets this task's list (possibly empty) and switch; without them the gate works both out itself.
PRIVATE = gate_private_paths(os.environ, lambda: resolve_private_paths(S, [WORKDIR_INPUT, WORKDIR, *EXTRA_ROOTS]))

server = make_server("labhq-approval", instructions="Permission gate for tool calls (policy + PI approval).")


def _allow(tool_input: dict) -> str:
    return json.dumps({"behavior": "allow", "updatedInput": tool_input})


def _deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message})


_PIP_INSTALL = re.compile(r"(?:^|\s)(?:-m\s+)?pip(?:\d+(?:\.\d+)*)?(?:\.exe)?\s+install\b", re.IGNORECASE)
_PIP_TARGET = re.compile(r"(?:--target(?:=|\s+)|-t\s+)(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.IGNORECASE)
_R_INSTALL = re.compile(r"\b(?:install\.packages|BiocManager::install)\s*\(", re.IGNORECASE)
_R_LIBRARY = re.compile(
    r"(?:\bR_LIBS_USER\b\s*=|\$env:R_LIBS_USER\s*=|\blib\s*=\s*)(\"[^\"]+\"|'[^']+'|[^\s,;)]+)",
    re.IGNORECASE,
)


def _task_library(path: str, name: str, workdir: str) -> bool:
    value = path.strip().strip("\"'")
    if any(mark in value for mark in ("$", "%", "`")):
        return False
    normalized = value.replace("\\", "/").rstrip("/")
    if normalized in {name, f"./{name}"}:
        return True
    try:
        return Path(value).is_absolute() and Path(value).resolve() == (Path(workdir) / name).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def shared_environment_install_denial(tool_name: str, tool_input: dict[str, Any], *,
                                      environment_step: bool, workdir: str) -> str | None:
    """Reject a visible package install that would mutate an environment shared by parallel steps.

    The environment step is the sole shared installer. Other steps may install only into their task-local
    Python/R library. This is deliberately a command gate, not a shell sandbox; engines without the Claude
    permission hook receive the same rule as an instruction.
    """
    if environment_step or tool_name not in {"Bash", "PowerShell"}:
        return None
    command = str(tool_input.get("command") or "")
    r_libraries = _R_LIBRARY.findall(command)
    for segment in re.split(r"&&|\|\||[;\r\n]", command):
        if _PIP_INSTALL.search(segment):
            targets = _PIP_TARGET.findall(segment)
            if not targets or not all(_task_library(target, ".pylib", workdir) for target in targets):
                return ("Only the environment step may modify the shared Python environment. Install the extra "
                        "package in this step with `python -m pip install --target ./.pylib ...` and record "
                        "`python -m pip freeze --path ./.pylib` in outputs/env/<step>.txt.")
        if _R_INSTALL.search(segment):
            if not r_libraries or not all(_task_library(path, ".rlib", workdir) for path in r_libraries):
                return ("Only the environment step may modify the shared R library. Install the extra package "
                        "with `lib='./.rlib'` (or task-local R_LIBS_USER) and record that library's package table "
                        "in outputs/env/<step>.txt.")
    return None



def _text_only_tool():
    # Claude Code's --permission-prompt-tool contract: exactly one text block, no structuredContent.
    # mcp>=1.10 wraps `-> str` results in structuredContent {"result": ...} unless told not to, and
    # Claude 2.1.282 then rejects the answer ("Expected a single text block"). Older mcp has no flag.
    try:
        return server.tool(structured_output=False)
    except TypeError:
        return server.tool()


@_text_only_tool()
async def approval_prompt(tool_name: str, input: dict[str, Any] | None = None,
                          tool_use_id: str | None = None) -> str:
    """Decide whether a tool call may run. Returns a JSON string with behavior allow|deny."""
    install_denial = shared_environment_install_denial(
        tool_name, input or {}, environment_step=ENVIRONMENT_STEP, workdir=WORKDIR)
    if install_denial:
        return _deny(install_denial)
    d = evaluate_tool(tool_name, input or {}, S.policy,
                      allowed_roots=[WORKDIR_INPUT, WORKDIR, *EXTRA_ROOTS], workdir=WORKDIR,
                      private_paths=PRIVATE.paths, private_enabled=PRIVATE.enabled,
                      private_open_reads=PRIVATE.open_reads)
    # A respelled write path (#219) is what was judged and what the PI sees, so Claude must write that one.
    tool_input = d.updated_input or input or {}
    if d.action == "allow":
        return _allow(tool_input)
    if d.action == "deny":
        return _deny(d.reason)
    detail = {"tool_name": tool_name, "input": short(tool_input, 1500)}
    path = (tool_input.get("file_path") or tool_input.get("notebook_path")) if tool_name in WRITE_LIKE else None
    if isinstance(path, str):
        detail["path"] = path  # in full: `input` is cut at 1,500 characters
    try:
        async with httpx.AsyncClient(timeout=S.policy.approvals.timeout_s + 30) as c:
            r = await c.post(f"{BROKER}/approval", headers={"X-Labhq-Token": TOKEN}, json={
                "task_id": TASK, "agent_id": AGENT, "kind": "tool_permission",
                "summary": f"{tool_name}: {d.reason}",
                "detail": detail,
                "timeout_s": S.policy.approvals.timeout_s,
            })
            r.raise_for_status()
            res = r.json()
    except Exception as e:  # fail closed; a deny is the permission-prompt contract's only answer (shown as an error)
        return _deny(f"approval broker unreachable ({e}); ask the CSO to reschedule\n{NOT_EVIDENCE}")
    if res.get("approved"):
        return _allow(tool_input)
    return _deny(res.get("note") or f"Denied by the PI: {d.reason}")


if __name__ == "__main__":
    server.run()
