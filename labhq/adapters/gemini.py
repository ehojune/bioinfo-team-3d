"""Google Gemini CLI adapter (`gemini -p … --output-format stream-json`).

MCP servers go into ./.gemini/settings.json of the workspace; role instructions into GEMINI.md.
Stream event names differ across Gemini CLI versions, so parsing is defensive.
Session resume is not wired up here: continuations get the previous result as context instead.
"""

from __future__ import annotations

import json

from ..util import short
from .base import ROLE_FOOTER, AgentAdapter, RunContext, RunState, expand_env, wrap_cwd

APPROVAL_MAP = {"plan": "plan", "acceptEdits": "auto_edit", "auto": "auto_edit",
                "bypassPermissions": "yolo", "default": "default", "manual": "default"}


class GeminiAdapter(AgentAdapter):
    engine = "gemini"
    supports_resume = False

    def stderr_error(self, stderr: str) -> str | None:
        if "IneligibleTierError" in stderr:
            return "Gemini CLI 개인 계정은 지원 종료, engine: antigravity를 쓰라"
        return None

    def engine_env(self) -> dict[str, str]:
        return {**super().engine_env(), "GEMINI_CLI_TRUST_WORKSPACE": "true", "NO_COLOR": "1"}

    def prepare(self, ctx: RunContext) -> None:
        servers: dict[str, dict] = {}
        for s in ctx.mcp_servers:
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                servers[s.name] = {"command": command, "args": args, "env": expand_env(s.env)}
            else:
                servers[s.name] = {"httpUrl": s.url, "headers": expand_env(s.headers)}
        gdir = ctx.workdir / ".gemini"
        gdir.mkdir(exist_ok=True)
        (gdir / "settings.json").write_text(json.dumps({"mcpServers": servers}, indent=2))
        (ctx.workdir / "GEMINI.md").write_text(ctx.agent.system_prompt.strip() + "\n" + ROLE_FOOTER)

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.gemini
        prompt = ctx.prompt
        if t.output_schema:
            prompt += ("\n\nReturn your final answer as a single JSON object matching this schema, "
                       f"with no prose outside it:\n{json.dumps(t.output_schema)}")
        cmd = [b.bin, "-p", prompt, "--output-format", "stream-json",
               "--approval-mode", APPROVAL_MAP.get(a.permission_mode, "default")]
        if a.model:
            cmd += ["-m", a.model]
        if ctx.extra_dirs:
            cmd += ["--include-directories", ",".join(ctx.extra_dirs)]
        return cmd + b.extra_args

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        typ = ev.get("type")
        if typ == "init":
            st.session_id = ev.get("session_id")
            await ctx.emit("agent.status", {"state": "working", "model": ev.get("model")})
        elif typ == "message" and ev.get("role") == "assistant":
            chunk = ev.get("content") or ""
            st.text_parts.append(chunk)
            if not ev.get("delta") or chunk.endswith("\n") or len(chunk) > 200:
                await ctx.emit("agent.log", {"text": short(chunk, 2000)})
        elif typ == "tool_use":
            await ctx.emit("agent.tool", {"name": ev.get("tool_name"), "input": short(ev.get("parameters"), 400)})
        elif typ == "tool_result" and ev.get("status") not in (None, "success"):
            await ctx.emit("agent.tool_error", {"text": short(ev.get("output") or ev.get("error"), 400)})
        elif typ == "error":
            st.error = ev.get("message") or "gemini error"
        elif typ == "result":
            st.result_seen = True
            st.usage = ev.get("stats") or {}
            if ev.get("status") not in (None, "success"):
                st.error = st.error or str(ev.get("error") or ev.get("status"))
            await ctx.emit("agent.usage", {"tokens": st.usage})
