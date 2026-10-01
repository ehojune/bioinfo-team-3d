"""OpenAI Codex CLI adapter (`codex exec --json`).

Role instructions go to AGENTS.md in the workspace (Codex reads it as project guidance).
Prompts use compact XML blocks (task / output contract / action safety / verification / grounding),
which Codex follows more reliably than long prose.

Observed in codex-cli 0.155.0-alpha.16 (openai/codex#24135): exec defaults to approval
policy never, so MCP calls fail unless the server's default_tools_approval_mode is approve.
labhq's own MCP tools enforce phone approval inside the broker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..util import short
from .base import (ROLE_FOOTER, AgentAdapter, child_config_dirs, RunContext, RunState, expand_env,
                   record_model_id, wrap_cwd)

# Staff tool names that mean "web". Codex has no per-tool rules for them; its native search turns on instead.
WEB_TOOLS = {"WebSearch", "WebFetch"}
# Features a read-only task turns off: each runs code or acts outside the `-s read-only` sandbox (hooks run shell
# commands, plugins bring hooks and MCP servers, apps are remote connector tools, computer/browser use drive the
# desktop). Names checked against `codex features list` on codex-cli 0.159.2; an unknown name is a CLI error, so a
# Codex that lacks one fails the read-only run instead of running it with that feature on.
READ_ONLY_DISABLED_FEATURES = ("hooks", "plugins", "apps", "computer_use", "browser_use")


def _toml(v: object) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)  # JSON strings/arrays/bools/numbers are valid TOML here


def _is_windows() -> bool:
    return os.name == "nt"


class CodexAdapter(AgentAdapter):
    engine = "codex"
    enforces_read_only = True  # -s read-only, no MCP, hooks/plugins/apps off, no user config

    def prepare(self, ctx: RunContext) -> None:
        (ctx.workdir / "AGENTS.md").write_text(ctx.agent.system_prompt.strip() + "\n" + ROLE_FOOTER, encoding="utf-8")
        if ctx.task.output_schema:
            (ctx.meta_dir / "output_schema.json").write_text(json.dumps(ctx.task.output_schema), encoding="utf-8")

    def compose_prompt(self, ctx: RunContext) -> str:
        t = ctx.task
        blocks = [f"<task>\n{ctx.prompt}\n</task>"]
        if t.output_schema:
            blocks.append("<structured_output_contract>\nReturn only JSON that matches the provided output "
                          "schema. No prose outside the JSON.\n</structured_output_contract>")
        blocks.append("<action_safety>\nStay inside the workspace and the listed project dirs. No unrelated "
                      "refactors. Heavy compute and anything touching restricted data goes through the "
                      "labhq_hpc tools.\n</action_safety>")
        blocks.append("<verification_loop>\nBefore finishing, check that every deliverable exists in ./outputs "
                      "and that tests or sanity checks you ran actually passed; report what you verified.\n"
                      "</verification_loop>")
        blocks.append("<grounding_rules>\nTie every claim to a file, command output or cited source. Label "
                      "hypotheses as hypotheses.\n</grounding_rules>")
        return "\n\n".join(blocks)

    def preflight_error(self, ctx: RunContext, env: dict[str, str]) -> str | None:
        b = self.settings.engines.codex
        # Codex reads AGENTS.override.md in its cwd instead of AGENTS.md, so a copy an earlier run left in a reused
        # workspace would replace the role prepare() writes, for a follow-up and for every later step (#147).
        override = ctx.workdir / "AGENTS.override.md"
        if override.exists() or override.is_symlink():
            return ("Codex staff session refused: the workspace holds AGENTS.override.md, which Codex reads instead "
                    "of labhq's role instructions (AGENTS.md) and no flag turns off. An earlier run left it; move it "
                    "out of the workspace to continue.")
        if not (b.isolate_user_config or ctx.read_only) or b.allow_global_agents_md:
            return None
        found = [n for home in child_config_dirs(env, ctx.workdir, "CODEX_HOME", ".codex")
                 for n in ("AGENTS.md", "AGENTS.override.md") if (home / n).is_file()]
        if not found:
            return None
        return (f"Codex staff session refused: $CODEX_HOME/{found[0]} (global instructions) would load and no flag "
                "turns it off. Run the runner under a dedicated account, point engines.codex.env.CODEX_HOME at a "
                "separate staff login, or set engines.codex.allow_global_agents_md: true.")

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.codex
        flags = ["--json", "--skip-git-repo-check", "-C", str(ctx.workdir), "-s", a.sandbox,
                 "-o", str(ctx.meta_dir / "last_message.txt")]
        if b.isolate_user_config or ctx.read_only:
            # config.toml carries the PI's plugins, notify hook and MCP servers. The global AGENTS.md in
            # CODEX_HOME still loads; set engines.codex.env.CODEX_HOME to a separate staff login to drop it.
            flags += ["--ignore-user-config", "--ignore-rules"]
            if _is_windows() and b.windows_sandbox:
                flags += ["-c", f"windows.sandbox={_toml(b.windows_sandbox)}"]
        if ctx.read_only:
            for feature in READ_ONLY_DISABLED_FEATURES:
                flags += ["--disable", feature]
        if a.model:
            flags += ["-m", a.model]
        web_search = "live" if any(t.split("(")[0] in WEB_TOOLS for t in a.tools) else "disabled"
        flags += ["-c", f'web_search="{web_search}"']
        for d in ctx.extra_dirs:
            flags += ["--add-dir", d]
        if t.output_schema:
            flags += ["--output-schema", str(ctx.meta_dir / "output_schema.json")]
        for s in ctx.mcp_servers:
            key = f"mcp_servers.{s.name}"
            if s.name in ("labhq_hpc", "labhq_approval", "labhq_ask") or s.auto_approve:
                # labhq tools enforce phone approval inside the broker; auto_approve servers are read-only.
                flags += ["-c", f'{key}.default_tools_approval_mode="approve"']
            if s.timeout_s:  # labhq_ask waits longer than a phone approval
                flags += ["-c", f"{key}.tool_timeout_sec={s.timeout_s}"]
            elif s.name.startswith("labhq_"):
                flags += ["-c", f"{key}.tool_timeout_sec={self.settings.policy.approvals.timeout_s + 120}"]
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                flags += ["-c", f"{key}.command={_toml(command)}", "-c", f"{key}.args={_toml(args)}"]
                if s.env:
                    flags += ["-c", f"{key}.env={_toml(expand_env(s.env))}"]
            else:
                flags += ["-c", f"{key}.url={_toml(s.url)}"]
        if not ctx.read_only:  # PI extra_args could widen the sandbox; a read-only run takes none
            flags += b.extra_args
        prompt = self.compose_prompt(ctx)
        if t.resume_session_id:
            return [b.bin, "exec", *flags, "resume", t.resume_session_id, prompt]
        return [b.bin, "exec", *flags, prompt]

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        record_model_id(st, ctx, ev.get("model_id") or ev.get("model"))
        typ = ev.get("type")
        if typ == "thread.started":
            st.session_id = ev.get("thread_id")
            await ctx.emit("agent.status", {"state": "working", "engine": "codex"})
        elif typ in ("item.started", "item.completed"):
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if it in ("agent_message", "assistant_message") and typ == "item.completed":
                st.text_parts.append(item.get("text", ""))
                await ctx.emit("agent.log", {"text": short(item.get("text"), 2000)})
            elif it == "reasoning" and typ == "item.completed":
                await ctx.emit("agent.log", {"level": "thinking", "text": short(item.get("text"), 400)})
            elif it == "command_execution":
                if typ == "item.started":
                    await ctx.emit("agent.tool", {"name": "shell", "input": short(item.get("command"), 300)})
                elif item.get("exit_code") not in (None, 0):
                    await ctx.emit("agent.tool_error", {"text": short(item.get("aggregated_output"), 400)})
            elif it == "mcp_tool_call" and typ == "item.started":
                await ctx.emit("agent.tool", {"name": f"mcp:{item.get('server')}.{item.get('tool')}"})
            elif it == "mcp_tool_call" and typ == "item.completed":
                result = item.get("result") or {}
                tool_error = isinstance(result, dict) and (result.get("isError") or result.get("is_error"))
                if item.get("status") != "failed" and not tool_error:
                    return
                err = item.get("error")
                detail = "\n".join(str(block.get("text", "")) for block in result.get("content", [])
                                   if isinstance(block, dict) and block.get("type") == "text") if tool_error else ""
                message = str((err.get("message") if isinstance(err, dict) else err) or detail or "MCP tool failed")
                # The agent may recover from an ordinary tool failure; an approval-policy refusal means
                # labhq wired the server wrong (openai/codex#24135), so that one fails the task loudly.
                if "approval policy" in message:
                    st.error = message
                await ctx.emit("agent.tool_error", {"text": short(message, 400)})
            elif it == "file_change" and typ == "item.completed":
                await ctx.emit("agent.tool", {"name": "edit", "input": short(item.get("changes"), 300)})
            elif it == "web_search" and typ == "item.completed":
                action = item.get("action") or {}
                query = action.get("query") if isinstance(action, dict) else None
                await ctx.emit("agent.tool", {"name": "web_search",
                                               "input": short(query or item.get("query"), 200)})
        elif typ == "turn.completed":
            st.result_seen = True
            from .base import cumulative_usage, record_accounting, token_counts
            total = token_counts(ev.get("usage"), ("input_tokens", "cached_input_tokens",
                "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"))
            usage = cumulative_usage(total, st, ctx)
            record_accounting(st, ctx)
            await ctx.emit("agent.usage", {"tokens": usage, "usage_known": st.usage_known,
                                           "cost_usd": None, "cost_known": False})
        elif typ in ("turn.failed", "error"):
            err = ev.get("error")
            st.error = (err.get("message") if isinstance(err, dict) else None) or ev.get("message") or "codex error"

    def finalize(self, st: RunState, ctx: RunContext, returncode: int | None):
        last = ctx.meta_dir / "last_message.txt"
        if last.exists() and last.read_text(encoding="utf-8").strip():
            st.final_text = last.read_text(encoding="utf-8")
        return super().finalize(st, ctx, returncode)
