"""Claude Code headless adapter.

Flags used (see https://code.claude.com/docs/en/cli-reference): -p, --output-format stream-json
--verbose, --model, --permission-mode, --permission-prompt-tool, --mcp-config + --strict-mcp-config,
--append-system-prompt-file, --max-turns, --max-budget-usd, --json-schema, --resume, --settings,
--add-dir, --tools, --allowedTools, --disallowedTools, --setting-sources, --disable-slash-commands.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..util import short
from .base import ROLE_FOOTER, AgentAdapter, RunContext, RunState, expand_env, wrap_cwd

PERMISSION_TOOL = "mcp__labhq_approval__approval_prompt"
ISOLATION_FLAGS = ["--setting-sources", "project,local", "--disable-slash-commands"]


def user_config_isolation(env: dict[str, str]) -> dict:
    """Settings that keep the PI's own Claude setup out of a staff session.

    Verified on Claude 2.1.282 (tests/fixtures/real/claude_code/claude_isolated.jsonl): ISOLATION_FLAGS drop
    user hooks, plugins, subagents and skills, but the user CLAUDE.md still loads until it is excluded here.
    """
    home = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return {"autoMemoryEnabled": False,
            "claudeMdExcludes": [(home / "CLAUDE.md").as_posix(), (home / "rules").as_posix() + "/**"]}


class ClaudeCodeAdapter(AgentAdapter):
    engine = "claude_code"

    def prepare(self, ctx: RunContext) -> None:
        servers: dict[str, dict] = {}
        for s in ctx.mcp_servers:
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                servers[s.name] = {"type": "stdio", "command": command, "args": args, "env": expand_env(s.env)}
            else:
                servers[s.name] = {"type": "http", "url": s.url, "headers": expand_env(s.headers)}
        (ctx.meta_dir / "mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2))
        (ctx.meta_dir / "system_prompt.md").write_text(ctx.agent.system_prompt.strip() + "\n" + ROLE_FOOTER)

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.claude_code
        # Prompt goes right after -p; variadic flags (--mcp-config, --add-dir, --allowedTools…) go last.
        cmd = [b.bin, "-p", ctx.prompt, "--output-format", "stream-json", "--verbose",
               "--permission-mode", a.permission_mode,
               "--append-system-prompt-file", str(ctx.meta_dir / "system_prompt.md")]
        if a.model:
            cmd += ["--model", a.model]
        if t.resume_session_id:
            cmd += ["--resume", t.resume_session_id]
        if a.max_turns:
            cmd += ["--max-turns", str(a.max_turns)]
        budget = t.budget_usd or a.max_budget_usd or self.settings.policy.budget.per_task_usd
        if budget:
            cmd += ["--max-budget-usd", f"{budget:.2f}"]
        if t.output_schema:
            cmd += ["--json-schema", json.dumps(t.output_schema)]
        settings = dict(ctx.claude_settings)
        if b.isolate_user_config:
            cmd += ISOLATION_FLAGS
            settings.update(user_config_isolation({**os.environ, **self.engine_env(), **ctx.env}))
        if settings:
            cmd += ["--settings", json.dumps(settings)]
        if a.builtin_tools is not None:
            cmd += ["--tools", a.builtin_tools]
        if ctx.use_permission_tool and any(s.name == "labhq_approval" for s in ctx.mcp_servers):
            cmd += ["--permission-prompt-tool", PERMISSION_TOOL]
        cmd += b.extra_args
        cmd += ["--mcp-config", str(ctx.meta_dir / "mcp.json"), "--strict-mcp-config"]
        for d in ctx.extra_dirs:
            cmd += ["--add-dir", d]
        if a.tools:
            cmd += ["--allowedTools", *a.tools]
        if a.disallowed_tools:
            cmd += ["--disallowedTools", *a.disallowed_tools]
        return cmd

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            st.session_id = ev.get("session_id")
            await ctx.emit("agent.status", {"state": "working", "model": ev.get("model"),
                                            "mcp": [m.get("name") for m in ev.get("mcp_servers", []) if isinstance(m, dict)]})
        elif typ == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    st.text_parts.append(block["text"])
                    await ctx.emit("agent.log", {"text": short(block["text"], 2000),
                                                 "subagent": bool(ev.get("parent_tool_use_id"))})
                elif block.get("type") == "tool_use":
                    await ctx.emit("agent.tool", {"name": block.get("name"), "input": short(block.get("input"), 400)})
        elif typ == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    await ctx.emit("agent.tool_error", {"text": short(block.get("content"), 400)})
        elif typ == "result":
            st.result_seen = True
            st.final_text = ev.get("result")
            st.session_id = ev.get("session_id") or st.session_id
            st.cost_usd = ev.get("total_cost_usd")
            st.usage = ev.get("usage") or {}
            if ev.get("structured_output") is not None:
                st.structured = ev["structured_output"]
            if ev.get("is_error"):
                st.error = str(ev.get("result") or ev.get("subtype") or "Claude Code error")
            elif ev.get("subtype") not in (None, "success"):
                st.error = ev.get("subtype") or "error"
            await ctx.emit("agent.usage", {"cost_usd": st.cost_usd, "num_turns": ev.get("num_turns"),
                                           "duration_ms": ev.get("duration_ms")})
