"""Antigravity headless stream-json adapter.

There is no per-tool approval hook: the labhq phone gate cannot block agy tool calls.
Do not assign this engine to staff who can access controlled data. Whether agy reads
workspace instruction files is UNVERIFIED; role instructions are prepended to the prompt.
"""

from __future__ import annotations

import json

from ..util import short
from .base import ROLE_FOOTER, AgentAdapter, RunContext, RunState


class AntigravityAdapter(AgentAdapter):
    engine = "antigravity"
    supports_resume = False

    def prepare(self, ctx: RunContext) -> None:
        if ctx.mcp_servers:
            raise ValueError("antigravity does not support MCP servers")
        if ctx.task.output_schema:
            (ctx.meta_dir / "output_schema.json").write_text(json.dumps(ctx.task.output_schema))

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.antigravity
        prompt = a.system_prompt.strip() + "\n" + ROLE_FOOTER + "\n" + ctx.prompt
        cmd = [b.bin, "-p", prompt, "--output-format", "stream-json"]
        if a.model:
            cmd += ["--model", a.model]
        mode = a.permission_mode
        if mode in ("plan", "default", "manual", "acceptEdits", "auto"):
            cmd.append("--sandbox")
        if mode in ("acceptEdits", "auto", "bypassPermissions"):
            cmd.append("--dangerously-skip-permissions")
        if mode not in ("plan", "default", "manual", "acceptEdits", "auto", "bypassPermissions"):
            raise ValueError(f"unsupported antigravity permission_mode: {mode}")
        if t.output_schema:
            cmd += ["--json-schema", str(ctx.meta_dir / "output_schema.json")]
        cmd += ["--print-timeout", f"{self.settings.runner.task_timeout_s}s"]
        return cmd + b.extra_args

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        typ = ev.get("event")
        data = ev.get(typ) or {}
        if typ == "init":
            await ctx.emit("agent.status", {"state": "working", "model": data.get("model")})
        elif typ == "step_update":
            st.session_id = data.get("conversation_id") or st.session_id
            if data.get("step_type") == "agent_response":
                delta = data.get("text_delta") or ""
                if delta:
                    st.text_parts.append(delta)
                    for part in delta.splitlines():
                        if part:
                            await ctx.emit("agent.log", {"text": short(part, 2000)})
            elif data.get("step_type") == "tool":
                info = data.get("tool_info") or {}
                if data.get("state") == "ACTIVE":
                    await ctx.emit("agent.tool", {"name": data.get("tool_name"),
                                                  "input": short(info.get("parameters"), 400)})
                elif data.get("state") == "ERROR":
                    await ctx.emit("agent.tool_error", {"text": short(info.get("error") or "agy tool failed", 400)})
        elif typ == "result":
            st.result_seen = True
            st.session_id = data.get("conversation_id") or st.session_id
            st.final_text = data.get("response") or ""
            st.usage = data.get("usage") or {}
            if data.get("status") != "SUCCESS":
                st.error = str(data.get("error") or data.get("status") or "agy error")
            elif data.get("denied_actions"):
                actions = data["denied_actions"]
                names = [str(x.get("display_name") or x.get("action")) if isinstance(x, dict) else str(x)
                         for x in actions]
                st.error = "권한이 필요한 도구가 헤드리스에서 거부됨: " + ", ".join(names)
            await ctx.emit("agent.usage", {"tokens": st.usage})
