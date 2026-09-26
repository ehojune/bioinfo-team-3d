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
from .base import ROLE_FOOTER, AgentAdapter, child_config_dir, RunContext, RunState, expand_env, wrap_cwd


def _toml(v: object) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)  # JSON strings/arrays/bools/numbers are valid TOML here


def _is_windows() -> bool:
    return os.name == "nt"


class CodexAdapter(AgentAdapter):
    engine = "codex"

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
        if not b.isolate_user_config or b.allow_global_agents_md:
            return None
        home = child_config_dir(env, ctx.workdir, "CODEX_HOME", ".codex")
        found = [n for n in ("AGENTS.md", "AGENTS.override.md") if (home / n).is_file()]
        if not found:
            return None
        return (f"Codex staff session refused: $CODEX_HOME/{found[0]} (global instructions) would load and no flag "
                "turns it off. Run the runner under a dedicated account, point engines.codex.env.CODEX_HOME at a "
                "separate staff login, or set engines.codex.allow_global_agents_md: true.")

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.codex
        flags = ["--json", "--skip-git-repo-check", "-C", str(ctx.workdir), "-s", a.sandbox,
                 "-o", str(ctx.meta_dir / "last_message.txt")]
        if b.isolate_user_config:
            # config.toml carries the PI's plugins, notify hook and MCP servers. The global AGENTS.md in
            # CODEX_HOME still loads; set engines.codex.env.CODEX_HOME to a separate staff login to drop it.
            flags += ["--ignore-user-config", "--ignore-rules"]
            if _is_windows() and b.windows_sandbox:
                flags += ["-c", f"windows.sandbox={_toml(b.windows_sandbox)}"]
        if a.model:
            flags += ["-m", a.model]
        for d in ctx.extra_dirs:
            flags += ["--add-dir", d]
        if t.output_schema:
            flags += ["--output-schema", str(ctx.meta_dir / "output_schema.json")]
        for s in ctx.mcp_servers:
            key = f"mcp_servers.{s.name}"
            if s.name in ("labhq_hpc", "labhq_approval"):
                # The labhq broker enforces phone approval inside these tools.
                flags += ["-c", f'{key}.default_tools_approval_mode="approve"']
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                flags += ["-c", f"{key}.command={_toml(command)}", "-c", f"{key}.args={_toml(args)}"]
                if s.env:
                    flags += ["-c", f"{key}.env={_toml(expand_env(s.env))}"]
            else:
                flags += ["-c", f"{key}.url={_toml(s.url)}"]
        flags += b.extra_args
        prompt = self.compose_prompt(ctx)
        if t.resume_session_id:
            return [b.bin, "exec", *flags, "resume", t.resume_session_id, prompt]
        return [b.bin, "exec", *flags, prompt]

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
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
            elif it == "mcp_tool_call" and typ == "item.completed" and item.get("status") == "failed":
                err = item.get("error")
                message = str((err.get("message") if isinstance(err, dict) else err) or "MCP tool failed")
                # The agent may recover from an ordinary tool failure; an approval-policy refusal means
                # labhq wired the server wrong (openai/codex#24135), so that one fails the task loudly.
                if "approval policy" in message:
                    st.error = message
                await ctx.emit("agent.tool_error", {"text": short(message, 400)})
            elif it == "file_change" and typ == "item.completed":
                await ctx.emit("agent.tool", {"name": "edit", "input": short(item.get("changes"), 300)})
            elif it == "web_search" and typ == "item.started":
                await ctx.emit("agent.tool", {"name": "web_search", "input": short(item.get("query"), 200)})
        elif typ == "turn.completed":
            st.result_seen = True
            st.usage = ev.get("usage") or {}
            await ctx.emit("agent.usage", {"tokens": st.usage})
        elif typ in ("turn.failed", "error"):
            err = ev.get("error")
            st.error = (err.get("message") if isinstance(err, dict) else None) or ev.get("message") or "codex error"

    def finalize(self, st: RunState, ctx: RunContext, returncode: int | None):
        last = ctx.meta_dir / "last_message.txt"
        if last.exists() and last.read_text(encoding="utf-8").strip():
            st.final_text = last.read_text(encoding="utf-8")
        return super().finalize(st, ctx, returncode)
