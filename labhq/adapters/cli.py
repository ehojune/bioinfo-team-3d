"""Adapter for any other agent program — e.g. the lab's own bioinfo-agent.

The agent is described by a command template (AgentSpec.cli). labhq writes the task files, runs the
command in the task workspace, and reads stdout.

With `output: jsonl`, each stdout line may be a labhq event (anything else is treated as a log line):
  {"type": "status", "state": "working"}                     → drives the office animation
  {"type": "log", "text": "aligning sample 3/12"}             → speech bubble + messenger
  {"type": "tool", "name": "bwa-mem2", "input": {...}}        → tool icon
  {"type": "usage", "cost_usd": 0.12, "tokens": {...}}
  {"type": "result", "ok": true, "text": "...", "structured": {...}, "session_id": "...", "cost_usd": 0.3}
The labhq MCP servers (approval, hpc) are written to {mcp_config} in Claude-style mcp.json format, so an
agent built on the Claude Agent SDK can load them directly.
"""

from __future__ import annotations

import json

from ..models import CliSpec
from ..util import short
from .base import ROLE_FOOTER, AgentAdapter, RunContext, RunState, expand_env, wrap_cwd


class _Keep(dict):
    def __missing__(self, key: str) -> str:  # unknown {placeholders} are left as-is
        return "{" + key + "}"


class CliAdapter(AgentAdapter):
    engine = "cli"

    @staticmethod
    def _spec(ctx: RunContext) -> CliSpec:
        if not ctx.agent.cli:
            raise RuntimeError(f"agent {ctx.agent.id!r} uses engine: cli but has no `cli:` block")
        return ctx.agent.cli

    def prepare(self, ctx: RunContext) -> None:
        md = ctx.meta_dir
        (md / "prompt.md").write_text(ctx.prompt, encoding="utf-8")
        (md / "role.md").write_text(ctx.agent.system_prompt.strip() + "\n" + ROLE_FOOTER, encoding="utf-8")
        if ctx.task.output_schema:
            (md / "output_schema.json").write_text(json.dumps(ctx.task.output_schema), encoding="utf-8")
        servers: dict[str, dict] = {}
        for s in ctx.mcp_servers:
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                servers[s.name] = {"type": "stdio", "command": command, "args": args, "env": expand_env(s.env)}
            else:
                servers[s.name] = {"type": "http", "url": s.url, "headers": expand_env(s.headers)}
        (md / "mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
        ctx.env.update(expand_env(self._spec(ctx).env))

    def _values(self, ctx: RunContext) -> _Keep:
        md = ctx.meta_dir
        return _Keep(
            prompt_file=str(md / "prompt.md"), prompt=ctx.prompt, role_file=str(md / "role.md"),
            workdir=str(ctx.workdir), outputs=str(ctx.workdir / "outputs"), task_id=ctx.task.id,
            schema_file=str(md / "output_schema.json") if ctx.task.output_schema else "",
            mcp_config=str(md / "mcp.json"), session_id=ctx.task.resume_session_id or "",
            model=ctx.agent.model or "",
        )

    def build_command(self, ctx: RunContext) -> list[str]:
        spec, v = self._spec(ctx), self._values(ctx)
        cmd = [part.format_map(v) for part in spec.command]
        if ctx.task.resume_session_id and spec.resume_args:
            cmd += [part.format_map(v) for part in spec.resume_args]
        return cmd

    def stdin_payload(self, ctx: RunContext) -> bytes | None:
        return ctx.prompt.encode() if self._spec(ctx).stdin == "prompt" else None

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = None
        if self._spec(ctx).output == "jsonl":
            try:
                ev = json.loads(line)
            except ValueError:
                ev = None
        if not isinstance(ev, dict) or "type" not in ev:
            st.text_parts.append(line + "\n")
            await ctx.emit("agent.log", {"text": short(line, 2000)})
            return
        typ = ev["type"]
        if typ == "status":
            await ctx.emit("agent.status", {"state": ev.get("state", "working"), "task": ev.get("task")})
        elif typ == "log":
            st.text_parts.append(str(ev.get("text", "")) + "\n")
            await ctx.emit("agent.log", {"text": short(ev.get("text"), 2000)})
        elif typ == "tool":
            await ctx.emit("agent.tool", {"name": ev.get("name"), "input": short(ev.get("input"), 400)})
        elif typ == "usage":
            st.cost_usd = ev.get("cost_usd", st.cost_usd)
            await ctx.emit("agent.usage", {"cost_usd": ev.get("cost_usd"), "tokens": ev.get("tokens")})
        elif typ == "result":
            st.result_seen = True
            st.final_text = str(ev.get("text", ""))
            st.structured = ev.get("structured")
            st.session_id = ev.get("session_id") or st.session_id
            st.cost_usd = ev.get("cost_usd", st.cost_usd)
            if ev.get("ok") is False:
                st.error = ev.get("error") or "agent reported failure"
        else:
            await ctx.emit("agent.log", {"text": short(ev, 400), "level": "debug"})
