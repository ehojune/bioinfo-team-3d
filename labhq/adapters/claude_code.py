"""Claude Code headless adapter.

Flags used (see https://code.claude.com/docs/en/cli-reference): -p, --output-format stream-json
--verbose, --model, --permission-mode, --permission-prompt-tool, --mcp-config + --strict-mcp-config,
--append-system-prompt-file, --max-turns, --max-budget-usd, --json-schema, --resume, --settings,
--add-dir, --plugin-dir, --tools, --allowedTools, --disallowedTools, --setting-sources,
--disable-slash-commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ..util import short
from .base import ROLE_FOOTER, AgentAdapter, RunContext, RunState, child_config_dirs, expand_env, wrap_cwd

PERMISSION_TOOL = "mcp__labhq_approval__approval_prompt"
ISOLATION_FLAGS = ["--setting-sources", "project,local", "--disable-slash-commands"]
PLUGIN_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def user_config_isolation(env: dict[str, str], cwd: Path) -> dict:
    """Settings that keep the PI's own Claude setup out of a staff session.

    Verified on Claude 2.1.282 (tests/fixtures/real/claude_code/claude_isolated.jsonl): ISOLATION_FLAGS drop
    user hooks, plugins, subagents and skills, but the user CLAUDE.md still loads until it is excluded here.
    A staff member may explicitly load a plugin skill while retaining project,local setting sources.
    """
    excludes = []
    for home in child_config_dirs(env, cwd, "CLAUDE_CONFIG_DIR", ".claude"):
        excludes += [(home / "CLAUDE.md").as_posix(), (home / "rules").as_posix() + "/**"]
    # Claude also walks up from its cwd loading project memory, so a CLAUDE.md in any ancestor of the staff
    # workspace (e.g. ~/CLAUDE.md above ~/.labhq/runs) would load. Verified on 2.1.282 with a canary file.
    for anc in Path(cwd).resolve().parents:
        excludes += [(anc / n).as_posix() for n in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")]
        excludes.append((anc / ".claude" / "rules").as_posix() + "/**")
    return {"autoMemoryEnabled": False, "claudeMdExcludes": excludes}


# Default component locations and root config files from the Claude Code plugin reference, plus any
# relative path the manifest itself declares (custom commands/agents/hooks/mcpServers/lspServers files).
PLUGIN_PARTS = (".claude-plugin", "skills", "agents", "hooks", "commands", "output-styles", "bin")
PLUGIN_ROOT_FILES = (".mcp.json", ".lsp.json", "settings.json")


def read_plugin_manifest(path: Path) -> dict | None:
    try:
        data = json.loads((path / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_paths(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("./", "../")) else []
    if isinstance(value, dict):
        return [p for v in value.values() for p in _manifest_paths(v)]
    if isinstance(value, list):
        return [p for v in value for p in _manifest_paths(v)]
    return []


def plugin_provenance(name: str, path: Path) -> dict:
    """Name, declared version and a content hash of every part Claude Code loads (no local path)."""
    manifest = read_plugin_manifest(path) or {}
    root = path.resolve()
    targets = [path / part for part in PLUGIN_PARTS + PLUGIN_ROOT_FILES]
    for rel in _manifest_paths(manifest):
        target = (path / rel).resolve()
        if target == root or root in target.parents:  # a manifest path outside the plugin is not ours to hash
            targets.append(target)
    files: set[Path] = set()
    for t in targets:
        if t.is_file():
            files.add(t.resolve())
        elif t.is_dir():
            files.update(f.resolve() for f in t.rglob("*") if f.is_file())
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f.relative_to(root).as_posix()):
        h.update(f.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return {"name": name, "version": manifest.get("version"), "sha256": h.hexdigest()}


class ClaudeCodeAdapter(AgentAdapter):
    engine = "claude_code"

    def _plugin_dirs(self, ctx: RunContext, env: dict[str, str]) -> list[str]:
        return [expand_env({"dir": raw}, env)["dir"] for raw in ctx.agent.plugin_dirs]

    def preflight_error(self, ctx: RunContext, env: dict[str, str]) -> str | None:
        # Errors name the configured entry (e.g. ${BIOINFO_AGENT_DIR}), never the resolved path: they reach
        # task results, the final report and project GitHub updates.
        found: dict[str, Path] = {}
        for raw, directory in zip(ctx.agent.plugin_dirs, self._plugin_dirs(ctx, env)):
            for var in PLUGIN_VAR.findall(raw):
                if not env.get(var):
                    return f"{var} is not set"
            path = Path(directory)
            if not path.is_absolute():
                path = ctx.workdir / path
            if not path.is_dir():
                return f"plugin directory for {raw} does not exist"
            manifest = read_plugin_manifest(path)
            if manifest is None:
                return f"plugin manifest for {raw} is missing or unreadable (.claude-plugin/plugin.json)"
            found[str(manifest.get("name") or "")] = path
        for skill in ctx.agent.required_skills:
            plugin, name = skill.split(":")
            if plugin not in found:
                return f"required plugin '{plugin}' is not among plugin_dirs"
            if not (found[plugin] / "skills" / name / "SKILL.md").is_file():
                return f"required skill '{skill}' is missing from the plugin"
        if found:
            ctx.plugin_provenance = [plugin_provenance(name, path) for name, path in found.items()]
        return None

    def prepare(self, ctx: RunContext) -> None:
        servers: dict[str, dict] = {}
        for s in ctx.mcp_servers:
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                servers[s.name] = {"type": "stdio", "command": command, "args": args, "env": expand_env(s.env)}
            else:
                servers[s.name] = {"type": "http", "url": s.url, "headers": expand_env(s.headers)}
        (ctx.meta_dir / "mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
        (ctx.meta_dir / "system_prompt.md").write_text(ctx.agent.system_prompt.strip() + "\n" + ROLE_FOOTER, encoding="utf-8")

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
            cmd += ISOLATION_FLAGS[:2] if a.allow_skills else ISOLATION_FLAGS
            settings.update(user_config_isolation({**os.environ, **self.engine_env(), **ctx.env}, ctx.workdir))
        if settings:
            cmd += ["--settings", json.dumps(settings)]
        if a.builtin_tools is not None:
            cmd += ["--tools", a.builtin_tools]
        if ctx.use_permission_tool and any(s.name == "labhq_approval" for s in ctx.mcp_servers):
            cmd += ["--permission-prompt-tool", PERMISSION_TOOL]
        cmd += b.extra_args
        for directory in self._plugin_dirs(ctx, {**os.environ, **self.engine_env(), **ctx.env}):
            cmd += ["--plugin-dir", directory]
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
