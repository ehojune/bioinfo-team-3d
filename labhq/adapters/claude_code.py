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
import subprocess
from pathlib import Path

from ..util import short
from .base import (ROLE_FOOTER, AgentAdapter, RunContext, RunState, child_config_dirs, expand_env,
                   record_model_id, wrap_cwd)

PERMISSION_TOOL = "mcp__labhq_approval__approval_prompt"
ISOLATION_FLAGS = ["--setting-sources", "project,local", "--disable-slash-commands"]
# Skill-enabled staff drop the project source too: with project,local a stray `.claude/skills/*` left in a reused
# workspace loads next to the plugin skill; with local only the plugin skill loads (probe 2026-09-28, Claude 2.1.282).
SKILL_ISOLATION_FLAGS = ["--setting-sources", "local"]
WORKSPACE_MEMORY = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
PLUGIN_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def user_config_isolation(env: dict[str, str], cwd: Path) -> dict:
    """Settings that keep the PI's own Claude setup out of a staff session.

    Verified on Claude 2.1.282 (tests/fixtures/real/claude_code/claude_isolated.jsonl): ISOLATION_FLAGS drop
    user hooks, plugins, subagents and skills, but the user CLAUDE.md still loads until it is excluded here.
    A skill-enabled staff member loads its plugin with only the local setting source (SKILL_ISOLATION_FLAGS).
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
PLUGIN_PARTS = (".claude-plugin", "skills", "agents", "hooks", "commands", "output-styles", "bin", "scripts")
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


def _git(path: Path, *args: str) -> bytes | None:
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _plugin_files(path: Path) -> tuple[set[Path], str | None]:
    """Files whose content defines the plugin's behaviour, and the git commit when there is one.

    In a git checkout that is every tracked or untracked-but-not-ignored file, so hooks, MCP/LSP configs and the
    scripts they run are all covered while ignored run output (a plugin may keep GBs of runs/) is not.
    Without git, fall back to the reference's component locations plus paths the manifest declares.
    """
    # In its own checkout or a parent repo's subfolder, ls-files lists only this subtree, relative to it.
    # A plugin the parent repo ignores lists nothing and falls through to the component walk.
    listed = _git(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    names = [n for n in (listed or b"").decode("utf-8", "surrogateescape").split("\0") if n]
    if names:
        commit = (_git(path, "rev-parse", "HEAD") or b"").decode().strip() or None
        return {(path / n) for n in names if (path / n).is_file()}, commit
    root = path.resolve()
    targets = [path / part for part in PLUGIN_PARTS + PLUGIN_ROOT_FILES]
    for rel in _manifest_paths(read_plugin_manifest(path) or {}):
        target = (path / rel).resolve()
        if target == root or root in target.parents:  # a manifest path outside the plugin is not ours to hash
            targets.append(target)
    files: set[Path] = set()
    for t in targets:
        if t.is_file():
            files.add(t)
        elif t.is_dir():
            files.update(f for f in t.rglob("*") if f.is_file())
    return files, None


def plugin_provenance(name: str, path: Path) -> dict:
    """Name, declared version, git commit and a content-and-mode hash (no local path)."""
    root = path.resolve()
    files, commit = _plugin_files(path)
    staged = _git(path, "ls-files", "--stage", "-z")  # paths relative to the plugin, as above
    index_modes = {}
    if staged is not None:
        for entry in staged.decode("utf-8", "surrogateescape").split("\0"):
            if entry:
                meta, relative = entry.split("\t", 1)
                mode, _, stage = meta.split(" ")
                if stage == "0":
                    index_modes[relative] = mode
    resolved = {f.resolve() for f in files}
    if any(root not in f.parents for f in resolved):
        raise ValueError("plugin file resolves outside the plugin")  # no path: errors reach reports
    h = hashlib.sha256()
    for f in sorted(resolved, key=lambda f: f.relative_to(root).as_posix()):
        relative = f.relative_to(root).as_posix()
        work_mode = "100755" if f.stat().st_mode & 0o111 else "100644"
        index_mode = index_modes.get(relative, work_mode)
        h.update(relative.encode("utf-8") + b"\0" + index_mode.encode("ascii") + b"\0" +
                 work_mode.encode("ascii") + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return {"name": name, "version": (read_plugin_manifest(path) or {}).get("version"), "commit": commit,
            "sha256": h.hexdigest()}


class ClaudeCodeAdapter(AgentAdapter):
    engine = "claude_code"
    enforces_read_only = True  # --permission-mode plan and --tools Read,Glob,Grep

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
            try:
                ctx.plugin_provenance = [plugin_provenance(name, path) for name, path in found.items()]
            except (OSError, ValueError):
                return "a plugin in plugin_dirs has a file that is unreadable or links outside the plugin"
            if ctx.record_run:
                ctx.record_run(plugins=ctx.plugin_provenance)  # persisted before the CLI is spawned
        return None

    def prepare(self, ctx: RunContext) -> None:
        servers: dict[str, dict] = {}
        for s in ctx.mcp_servers:
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                servers[s.name] = {"type": "stdio", "command": command, "args": args, "env": expand_env(s.env)}
                if s.timeout_s:
                    servers[s.name]["timeout"] = s.timeout_s * 1000
            else:
                servers[s.name] = {"type": "http", "url": s.url, "headers": expand_env(s.headers)}
            if s.name.startswith("labhq_") and not s.timeout_s:  # labhq_ask sets its own, longer wait
                servers[s.name]["timeout"] = (self.settings.policy.approvals.timeout_s + 120) * 1000
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
        permissions = dict(settings.get("permissions") or {})
        deny = list(permissions.get("deny") or [])
        permissions["deny"] = list(dict.fromkeys([*deny, "SendMessage", "ListAgents"]))
        settings["permissions"] = permissions
        if b.isolate_user_config:
            cmd += SKILL_ISOLATION_FLAGS if a.allow_skills else ISOLATION_FLAGS
            settings.update(user_config_isolation({**os.environ, **self.engine_env(), **ctx.env}, ctx.workdir))
            if a.allow_skills:
                # A skill-enabled member takes instructions only from labhq and its pinned plugin. A reused
                # workspace (HPC wake-up) could otherwise carry memory files a previous run wrote.
                wd = Path(ctx.workdir).resolve()
                settings["claudeMdExcludes"] += [(wd / n).as_posix() for n in WORKSPACE_MEMORY] + \
                    [(wd / ".claude" / "rules").as_posix() + "/**"]
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
        for d in [*ctx.extra_dirs, *ctx.read_dirs]:  # read_dirs carry Edit/Write deny rules in settings
            cmd += ["--add-dir", d]
        allowed = [*a.tools, *(f"mcp__{s.name}" for s in ctx.mcp_servers if s.auto_approve)]
        if allowed:
            cmd += ["--allowedTools", *allowed]
        if a.disallowed_tools:
            cmd += ["--disallowedTools", *a.disallowed_tools]
        return cmd

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            st.session_id = ev.get("session_id")
            record_model_id(st, ctx, ev.get("model"))
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
            st.error_kind = ev.get("subtype") if ev.get("subtype") != "success" else None
            st.session_id = ev.get("session_id") or st.session_id
            from .base import cumulative_cost, record_accounting, token_counts
            cumulative_cost(ev.get("total_cost_usd"), st, ctx)
            st.usage = token_counts(ev.get("usage"), ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens"))
            st.usage_known = bool(st.usage)
            st.session_usage_total = ev.get("modelUsage")
            record_accounting(st, ctx)
            if ev.get("structured_output") is not None:
                st.structured = ev["structured_output"]
            if ev.get("is_error"):
                st.error = str(ev.get("result") or ev.get("subtype") or "Claude Code error")
            elif ev.get("subtype") not in (None, "success"):
                st.error = ev.get("subtype") or "error"
            await ctx.emit("agent.usage", {"cost_usd": st.cost_usd, "cost_known": st.cost_usd is not None,
                                           "tokens": st.usage, "usage_known": st.usage_known,
                                           "num_turns": ev.get("num_turns"),
                                           "duration_ms": ev.get("duration_ms")})
