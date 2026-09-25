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

from ..policy import evaluate_tool
from ..settings import Settings
from ..util import short
from ._mcpcompat import make_server

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")
WORKDIR = str(Path(os.environ.get("LABHQ_WORKDIR", ".")).resolve())
EXTRA_ROOTS = [p for p in os.environ.get("LABHQ_EXTRA_ROOTS", "").split(os.pathsep) if p]

server = make_server("labhq-approval", instructions="Permission gate for tool calls (policy + PI approval).")


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


@_text_only_tool()
async def approval_prompt(tool_name: str, input: dict[str, Any] | None = None,
                          tool_use_id: str | None = None) -> str:
    """Decide whether a tool call may run. Returns a JSON string with behavior allow|deny."""
    tool_input = input or {}
    d = evaluate_tool(tool_name, tool_input, S.policy, allowed_roots=[WORKDIR, *EXTRA_ROOTS])
    if d.action == "allow":
        return _allow(tool_input)
    if d.action == "deny":
        return _deny(d.reason)
    try:
        async with httpx.AsyncClient(timeout=S.policy.approvals.timeout_s + 30) as c:
            r = await c.post(f"{BROKER}/approval", headers={"X-Labhq-Token": TOKEN}, json={
                "task_id": TASK, "agent_id": AGENT, "kind": "tool_permission",
                "summary": f"{tool_name}: {d.reason}",
                "detail": {"tool_name": tool_name, "input": short(tool_input, 1500)},
                "timeout_s": S.policy.approvals.timeout_s,
            })
            r.raise_for_status()
            res = r.json()
    except Exception as e:  # fail closed
        return _deny(f"approval broker unreachable ({e}); ask the CSO to reschedule")
    if res.get("approved"):
        return _allow(tool_input)
    return _deny(res.get("note") or f"Denied by the PI: {d.reason}")


if __name__ == "__main__":
    server.run()
