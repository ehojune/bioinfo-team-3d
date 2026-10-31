"""labhq-approval MCP server, used with Claude Code's `--permission-prompt-tool`.

Claude Code calls `approval_prompt(tool_name, input)` for any tool use not pre-approved by
--allowedTools. We apply policy.evaluate_tool; "ask" goes to the PI via the runner's broker.
The return value must be a JSON *string*: {"behavior": "allow", "updatedInput": {...}}
or {"behavior": "deny", "message": "..."}.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .. import disk_space
from ..policy import Decision, WRITE_LIKE, evaluate_tool
from ..environment_install import shared_environment_install_denial
from ..private_paths import gate_private_paths, resolve_private_paths
from ..settings import Settings
from ..util import short
from ..pi_text import PI_TEXT_RULE
from ._mcpcompat import NOT_EVIDENCE, make_server

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")
WORKDIR_INPUT = os.environ.get("LABHQ_WORKDIR", ".")
WORKDIR = str(Path(WORKDIR_INPUT).resolve())
READ_ONLY = os.environ.get("LABHQ_READ_ONLY") == "1"
ENVIRONMENT_STEP = os.environ.get("LABHQ_ENVIRONMENT_STEP") == "1"
SHARED_ENVIRONMENT_PROTECTED = os.environ.get("LABHQ_SHARED_ENVIRONMENT_PROTECTED") == "1"
EXTRA_ROOTS = [p for p in os.environ.get("LABHQ_EXTRA_ROOTS", "").split(os.pathsep) if p]
# The runner sets this task's list (possibly empty) and switch; without them the gate works both out itself.
PRIVATE = gate_private_paths(os.environ, lambda: resolve_private_paths(S, [WORKDIR_INPUT, WORKDIR, *EXTRA_ROOTS]))

server = make_server("labhq-approval", instructions="Permission gate for tool calls (policy + PI approval)." + PI_TEXT_RULE)


def _allow(tool_input: dict) -> str:
    return json.dumps({"behavior": "allow", "updatedInput": tool_input})


def _deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message})


def _text_only_tool():
    # Claude Code's --permission-prompt-tool contract: exactly one text block, no structuredContent.
    # mcp>=1.10 wraps `-> str` results in structuredContent {"result": ...} unless told not to, and
    # Claude 2.1.282 then rejects the answer ("Expected a single text block"). Older mcp has no flag.
    try:
        return server.tool(structured_output=False)
    except TypeError:
        return server.tool()


async def _disk_decision(d: Decision, command: str) -> Decision:
    """A download or unpack aimed at a drive that is short on space asks the PI instead of running (#678). Only
    allow becomes ask; an unknown size is never blocked, and a short drive with no known size only logs a warning."""
    try:
        check = disk_space.shell_check(
            command, WORKDIR, fail_gb=S.runner.disk_fail_free_gb, warn_gb=S.runner.disk_warning_free_gb,
            warn_percent=S.runner.disk_warning_free_percent)
    except Exception:  # noqa: BLE001 - a probe bug must not stop a command the policy already allowed
        return d
    if check is None:
        return d
    if check.action == "ask":
        return Decision("ask", check.reason, updated_input=d.updated_input)
    if TOKEN:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{BROKER}/event", headers={"X-Labhq-Token": TOKEN}, json={
                    "task_id": TASK, "agent_id": AGENT, "type": "agent.log",
                    "data": {"level": "warn", "text": check.reason, "tool_name": "Bash", "policy_action": "allow",
                             "policy_rule": "disk_space"}})
                r.raise_for_status()
        except httpx.HTTPError:
            pass  # a lost warning does not stop the command
    return d


@_text_only_tool()
async def approval_prompt(tool_name: str, input: dict[str, Any] | None = None,
                          tool_use_id: str | None = None) -> str:
    """Decide whether a tool call may run. Returns a JSON string with behavior allow|deny."""
    install_denial = shared_environment_install_denial(
        tool_name, input or {}, protected=SHARED_ENVIRONMENT_PROTECTED,
        environment_step=ENVIRONMENT_STEP, workdir=WORKDIR)
    if install_denial:
        return _deny(install_denial)
    d = evaluate_tool(tool_name, input or {}, S.policy,
                      allowed_roots=[WORKDIR_INPUT, WORKDIR, *EXTRA_ROOTS], workdir=WORKDIR,
                      private_paths=PRIVATE.paths, private_enabled=PRIVATE.enabled,
                      private_open_reads=PRIVATE.open_reads, read_only=READ_ONLY)
    # A respelled write path (#219) is what was judged and what the PI sees, so Claude must write that one.
    tool_input = d.updated_input or input or {}
    if d.action == "allow" and tool_name == "Bash":
        d = await _disk_decision(d, str(tool_input.get("command") or ""))
    if d.action == "allow":
        if d.reason.startswith('문법 안:') and TOKEN:
            # A task-scoped log, never an approval card. The broker authenticates the task identity.
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.post(f'{BROKER}/event', headers={'X-Labhq-Token': TOKEN}, json={
                        'task_id': TASK, 'agent_id': AGENT, 'type': 'agent.log',
                        'data': {'level': 'info', 'text': d.reason,
                                 'tool_name': tool_name, 'policy_action': 'allow',
                                 'policy_rule': 'task_workspace'}})
                    r.raise_for_status()
            except httpx.HTTPError:
                pass  # Telemetry outages do not turn a proven local operation into a PI card.
        return _allow(tool_input)
    if d.action == "deny":
        return _deny(d.reason)
    detail = {"tool_name": tool_name, "input": short(tool_input, 1500),
              "tool_input": tool_input, "policy_reason": d.reason}
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
