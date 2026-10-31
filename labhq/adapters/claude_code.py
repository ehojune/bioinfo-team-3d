"""Claude Code headless adapter.

Flags used (see https://code.claude.com/docs/en/cli-reference): -p, --output-format stream-json
--verbose, --model, --permission-mode, --permission-prompt-tool, --mcp-config + --strict-mcp-config,
--append-system-prompt-file, --max-turns, --max-budget-usd, --json-schema, --resume, --settings,
--add-dir, --plugin-dir, --tools, --allowedTools, --disallowedTools, --setting-sources,
--disable-slash-commands.
"""

from __future__ import annotations

import hashlib
import os
import json
import re
import subprocess
from pathlib import Path, PurePath

from .. import disk_space
from ..policy import claude_allowed_tools
from ..plugin_checks import read_plugin_manifest, skill_file
from ..staff_skills import (BUNDLED_PLUGIN_DIR, BUNDLED_PLUGIN_NAME, bundled_requirements)
from ..util import event_preview, short, tool_input_fingerprint, tool_input_summary
from .base import (AgentAdapter, role_footer, RunContext, RunState, SHELL_TOOLS, child_config_dirs, expand_env,
                   note_command_ok, note_failed_output, record_model_id, wrap_cwd)
from .owned import OwnedPathError, awrite_owned, case_sensitive_directory, is_link
from .read_only import WORKSPACE_INSTRUCTION_RULES, workspace_instruction_paths

PERMISSION_TOOL = "mcp__labhq_approval__approval_prompt"
ISOLATION_FLAGS = ["--setting-sources", "project,local", "--disable-slash-commands"]
# Skill-enabled staff drop the project source too: with project,local a stray `.claude/skills/*` left in a reused
# workspace loads next to the plugin skill; with local only the plugin skill loads (probe 2026-09-28, Claude 2.1.282).
SKILL_ISOLATION_FLAGS = ["--setting-sources", "local"]
# A read-only task loads no settings file at all: a reused workspace can hold a `.claude/settings.json` (hooks,
# enabledPlugins, helper commands) that an earlier writable run left. The --settings JSON still applies, with
# disableAllHooks on. Probed on Claude 2.1.282 (tests/fixtures/real/claude_code/claude_read_only_*.jsonl): with
# `--setting-sources project,local` and --plugin-dir, project and plugin SessionStart/Stop hooks ran under plan
# mode and Read,Glob,Grep; with this profile none ran and the --settings deny rules still held.
READ_ONLY_FLAGS = ["--setting-sources", "", "--disable-slash-commands"]
PLUGIN_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _collect_structured_output_delta(event: dict, st: RunState) -> None:
    """Reassemble invalid tool input before Claude clips it in the final transcript event."""
    kind = event.get("type")
    index = event.get("index")
    if not isinstance(index, int):
        return
    if kind == "content_block_start":
        block = event.get("content_block") or {}
        if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
            st.structured_output_streams[index] = {"id": str(block.get("id") or ""), "parts": []}
    elif kind == "content_block_delta" and index in st.structured_output_streams:
        delta = event.get("delta") or {}
        part = delta.get("partial_json") if delta.get("type") == "input_json_delta" else None
        if isinstance(part, str):
            st.structured_output_streams[index]["parts"].append(part)
    elif kind == "content_block_stop":
        block = st.structured_output_streams.pop(index, None)
        if block and block["id"]:
            st.structured_output_raw[block["id"]] = "".join(block["parts"])


async def _save_structured_output_failure(ctx: RunContext, tool_id: str, raw: str) -> str | None:
    """Keep the full invalid JSON in the task workspace without copying it into public event logs."""
    if not raw:
        return None
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_id or "unknown")[-60:]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    relative = f".tmp/structured_output_failures/{safe_id}-{digest}.json"
    try:
        await awrite_owned(ctx.workdir, relative, raw)
    except (OSError, OwnedPathError):
        return None
    return relative


def workspace_memory_excludes(workdir: Path) -> list[str]:
    """Memory files a previous run could have left in a reused workspace (HPC wake-up, follow-up). Claude also loads
    a subfolder's CLAUDE.md once it reads a file there (outputs/CLAUDE.md), so every depth is excluded (#147)."""
    wd = Path(workdir).resolve().as_posix()
    rules = WORKSPACE_INSTRUCTION_RULES["claude_code"]
    broad = ([path for name in rules["exclude_files"] for path in (f"{wd}/{name}", f"{wd}/**/{name}")] +
             [path for name in rules["exclude_dirs"]
              for path in (f"{wd}/{name}/**", f"{wd}/**/{name}/**")] +
             [f"{wd}/{name}" for name in rules["exclude_root_globs"]])
    # Claude's glob matcher skips dot-prefixed folders under `**`. Add every matching path literally after walking
    # the actual workspace with the same policy the read-only refusal check uses.
    literal = []
    for relative in workspace_instruction_paths("claude_code", Path(workdir), [], "exclude"):
        path = Path(workdir) / relative
        literal.append(f"{wd}/{relative}" + ("/**" if path.is_dir() else ""))
        if not path.is_dir() and not case_sensitive_directory(path.parent):
            for canonical in rules["exclude_files"]:
                if path.name.casefold() == canonical.casefold() and path.name != canonical:
                    literal.append(f"{wd}/{PurePath(relative).with_name(canonical).as_posix()}")
    return list(dict.fromkeys([*broad, *literal]))


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


# Skipped in a contract hire's folder hash (_folder_files): caches and environments an install adds (#424).
UNHASHED_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"})


def _folder_files(path: Path) -> tuple[set[Path], str | None]:
    """_plugin_files for a folder that is not a plugin, such as a contract hire's skill or MCP server (#424).

    Git-listed files as there, else every file. UNHASHED_DIRS are skipped either way: a delivered MCP folder may
    be a checkout whose `.venv` is not git-ignored."""
    listed = _git(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    names = [n for n in (listed or b"").decode("utf-8", "surrogateescape").split("\0")
             if n and not UNHASHED_DIRS & set(n.split("/"))]
    if names:
        commit = (_git(path, "rev-parse", "HEAD") or b"").decode().strip() or None
        return {(path / n) for n in names if (path / n).is_file()}, commit
    found: set[Path] = set()
    for folder, dirs, files in os.walk(path):  # links to folders are not followed
        dirs[:] = [d for d in dirs if d not in UNHASHED_DIRS]
        found.update(Path(folder) / f for f in files if (Path(folder) / f).is_file())
    return found, None


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


def _file_sha256(path: Path) -> bytes:
    """Read in 1 MiB chunks: a delivered MCP folder may hold model weights larger than memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()


def plugin_provenance(name: str, path: Path, every_file: bool = False) -> dict:
    """Name, declared version, git commit and a content-and-mode hash (no local path)."""
    root = path.resolve()
    files, commit = _folder_files(path) if every_file else _plugin_files(path)
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
        h.update(_file_sha256(f))
    return {"name": name, "version": (read_plugin_manifest(path) or {}).get("version"), "commit": commit,
            "sha256": h.hexdigest()}


class ClaudeCodeAdapter(AgentAdapter):
    engine = "claude_code"
    enforces_read_only = True  # file tools + scoped permission gate, no user hooks/plugins or shell

    def engine_env(self) -> dict[str, str]:
        # Claude Code documents this as disabling run_in_background, automatic backgrounding and Ctrl+B.
        # A PI-supplied engines.claude_code.env value remains authoritative.
        return {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", **super().engine_env()}

    def _plugin_dirs(self, ctx: RunContext, env: dict[str, str]) -> list[str]:
        directories = [expand_env({"dir": raw}, env)["dir"] for raw in ctx.agent.plugin_dirs]
        if bundled_requirements(ctx.agent) and not ctx.read_only:
            directories.append(str(BUNDLED_PLUGIN_DIR))
        if ctx.agent.id == self.settings.recruit.agent_id and not ctx.read_only:
            from ..recruit.paper2agent import skill_path
            directories.append(str(skill_path("claude_code", self.settings).parent.parent))
        return directories

    def _settings_file(self, ctx: RunContext, value: str) -> Path:
        """The --settings JSON as a file, for a command line that would overflow (Windows, 32,000 units).

        It lives next to the workspace, not in it: the member's write rules cover only its own folder, so it cannot
        rewrite its permission rules (a shell write there reaches the gate). The name is the task id; a rerun
        replaces it."""
        folder = Path(ctx.workdir).resolve().parent / ".labhq-settings"
        # is_link also catches a Windows junction, which is_symlink() misses (PR #483 review).
        if os.path.lexists(folder) and (is_link(folder) or not folder.is_dir()):
            raise ValueError("the settings folder next to the workspace is not a plain folder")
        folder.mkdir(exist_ok=True)
        if is_link(folder):  # replaced between the check and mkdir
            raise ValueError("the settings folder next to the workspace is not a plain folder")
        path = folder / f"{ctx.task.id}.json"
        temporary = folder / f".{ctx.task.id}.{os.getpid()}.tmp"
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def prompt_pointer_error(self, ctx: RunContext) -> str | None:
        tools = ctx.agent.builtin_tools
        if (ctx.prompt_pointer and ctx.prompt == ctx.prompt_pointer and tools is not None
                and "read" not in {tool.strip().casefold() for tool in tools.split(",")}):
            return "long prompt requires the Claude Read builtin to open its TASK file; execution refused"
        return None

    def preflight_error(self, ctx: RunContext, env: dict[str, str]) -> str | None:
        # Errors name the configured entry (e.g. ${BIOINFO_AGENT_DIR}), never the resolved path: they reach
        # task results, the final report and project GitHub updates.
        found: dict[str, Path] = {}
        try:
            directories = self._plugin_dirs(ctx, env)
        except ValueError as exc:
            return str(exc)
        labels = [*ctx.agent.plugin_dirs]
        if bundled_requirements(ctx.agent) and not ctx.read_only:
            labels.append("labhq bundled skills")
        if ctx.agent.id == self.settings.recruit.agent_id and not ctx.read_only:
            labels.append("Paper2Agent (labhq setup-paper2agent)")
        for raw, directory in zip(labels, directories):
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
            if ":" not in skill:
                plugin = found.get(BUNDLED_PLUGIN_NAME)
                if plugin is None:
                    return f"required bundled skill '{skill}' has no validated Claude plugin"
                if not skill_file(plugin, skill).is_file():
                    return f"required bundled skill '{skill}' is missing from the Claude plugin"
                continue
            plugin, name = skill.split(":")
            if plugin not in found:
                return f"required plugin '{plugin}' is not among plugin_dirs"
            if not skill_file(found[plugin], name).is_file():
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
        ctx.write_meta("mcp.json", json.dumps({"mcpServers": servers}, indent=2))
        ctx.write_meta("system_prompt.md", ctx.agent.system_prompt.strip() + "\n" + role_footer(ctx))

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.claude_code
        # Prompt goes right after -p; variadic flags (--mcp-config, --add-dir, --allowedTools…) go last.
        cmd = [b.bin, "-p", ctx.prompt, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--permission-mode", a.permission_mode,
               "--append-system-prompt-file", str(ctx.meta_dir / "system_prompt.md")]
        if a.model:
            cmd += ["--model", a.model]
        if a.effort:
            cmd += ["--effort", a.effort]
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
        # Orchestration tools start agents labhq neither approves nor counts (#421, from the #316 open-science review):
        # a staff member works its own step. Sub-agents (Agent, and its older name Task) stay only for the configured
        # recruiter when it declares one — Paper2Agent's parallel conversion (docs/manual.md; PR #425 review).
        declared = {str(tool).split("(")[0] for tool in a.tools}
        recruiter_sub_agents = a.id == self.settings.recruit.agent_id and bool(declared & {"Agent", "Task"})
        sub_agents = [] if recruiter_sub_agents else ["Agent", "Task"]
        permissions["deny"] = list(dict.fromkeys([*deny, *sub_agents, "Workflow", "TeamCreate", "TeamDelete",
                                                  "SendMessage", "ListAgents"]))
        settings["permissions"] = permissions
        # The deny rules stop this session from sending; this stops the PI's other sessions from messaging it
        # (#276). --settings is Claude's flagSettings: it outranks user settings, project/local files can only make
        # the value stricter, and only managed policy can override it. Probed on Claude 2.1.282
        # (scripts/probe_inbound_mcp.py): without it a message sent by name was queued in the staff session; with
        # it the message was refused and the sender got a refused receipt.
        settings["crossSessionInbound"] = "refuse"
        if ctx.read_only:  # isolation is not optional here, and no hook of any source runs
            cmd += READ_ONLY_FLAGS
            settings.update(user_config_isolation(self.staff_env(ctx), ctx.workdir))
            # Only labhq's instructions: memory files an earlier writable run left here stay out (#147).
            settings["claudeMdExcludes"] += workspace_memory_excludes(ctx.workdir)
            settings["disableAllHooks"] = True
        elif b.isolate_user_config:
            skills = a.allow_skills or a.id == self.settings.recruit.agent_id
            cmd += SKILL_ISOLATION_FLAGS if skills else ISOLATION_FLAGS
            settings.update(user_config_isolation(self.staff_env(ctx), ctx.workdir))
            if skills:
                # A skill-enabled member takes instructions only from labhq and its pinned plugin. A reused
                # workspace (HPC wake-up) could otherwise carry memory files a previous run wrote.
                settings["claudeMdExcludes"] += workspace_memory_excludes(ctx.workdir)
        if settings:
            value = json.dumps(settings)
            cmd += ["--settings", str(self._settings_file(ctx, value)) if ctx.compact_command else value]
        if a.builtin_tools is not None:
            cmd += ["--tools", a.builtin_tools]
        if ctx.use_permission_tool and any(s.name == "labhq_approval" for s in ctx.mcp_servers):
            cmd += ["--permission-prompt-tool", PERMISSION_TOOL]
        if not ctx.read_only:  # PI extra_args could load a plugin or lift plan mode; a read-only run takes none
            cmd += b.extra_args
            for directory in self._plugin_dirs(ctx, self.staff_env(ctx)):
                cmd += ["--plugin-dir", directory]
        cmd += ["--mcp-config", str(ctx.meta_dir / "mcp.json"), "--strict-mcp-config"]
        for d in [*ctx.extra_dirs, *ctx.read_dirs]:  # read_dirs carry Edit/Write deny rules in settings
            cmd += ["--add-dir", d]
        # Bare Write/Edit become Edit rules for the task's write roots, so an outside write reaches the gate (#219).
        # With PI personal paths active, shell rules go and reads narrow to the task's folders (PR #324 review).
        # On with no active path too (PR #327), and while a project link into a zone is denied by rule (#178).
        on = (ctx.private_enabled or bool(ctx.private_paths) or bool(ctx.denied_links)
              or ctx.gate_internal_reads or bool(ctx.gated_read_roots))
        # The task's own project folder in the staff config folder is read-only: Read rules, no Edit rule (#298 ⑤).
        all_read_roots = [ctx.workdir, *ctx.extra_dirs, *ctx.read_dirs, *ctx.private_open_reads]
        gated = set(ctx.gated_read_roots)
        read_roots = [root for root in all_read_roots if str(root) not in gated] if on else None
        # A step with the artifact contract writes only outputs/ and .tmp/: a rule for the whole folder would also
        # pre-approve a case-varied spelling of the read-only inputs/ (Claude compares deny rules literally, #946).
        meta = ctx.task.meta or {}
        contract = (meta.get("kind") in {"step", "result_correction", "slot_repair"}
                    and isinstance(meta.get("upstream_artifacts"), dict))
        write_roots = ([Path(ctx.workdir) / "outputs", Path(ctx.workdir) / ".tmp"] if contract
                       else [ctx.workdir])
        rules = claude_allowed_tools(
            a.tools, [*write_roots, *ctx.extra_dirs], read_roots,
            shared_environment_gate=(ctx.shared_environment_protected or ctx.gate_non_public_data))
        if ctx.use_permission_tool and any(s.name == "labhq_approval" for s in ctx.mcp_servers):
            # A pre-approved download or unpack (`Bash(unzip *)`) never reaches the gate, so it would skip the disk check
            # (#678). Only with the gate installed: without it the rule is the only thing letting the command run.
            rules = [rule for rule in rules if not disk_space.preapproves_bulk_write(rule)]
        allowed = [*rules,
                   *(f"mcp__{s.name}" for s in ctx.mcp_servers if s.auto_approve)]
        if allowed:
            cmd += ["--allowedTools", *allowed]
        if a.disallowed_tools:
            cmd += ["--disallowedTools", *a.disallowed_tools]
        return cmd

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        typ = ev.get("type")
        if typ == "stream_event":
            event = ev.get("event")
            if isinstance(event, dict):
                _collect_structured_output_delta(event, st)
            return
        if typ == "system" and ev.get("subtype") == "init":
            st.session_id = ev.get("session_id")
            st.mcp_status = [{"name": str(m.get("name")), "status": str(m.get("status"))}
                             for m in ev.get("mcp_servers", []) if isinstance(m, dict)][:20]
            record_model_id(st, ctx, ev.get("model"))
            await ctx.emit("agent.status", {"state": "working", "model": ev.get("model"),
                                            "mcp": [m.get("name") for m in ev.get("mcp_servers", []) if isinstance(m, dict)]})
        elif typ == "system" and ev.get("subtype") == "compact_boundary":
            # The session was just compacted (#1008): what it did before is gone from its context, so it may repeat itself.
            meta = ev.get("compact_metadata") if isinstance(ev.get("compact_metadata"), dict) else {}
            await ctx.emit("agent.compaction", {key: meta.get(key) for key in ("trigger", "pre_tokens", "post_tokens")})
        elif typ == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    st.text_parts.append(block["text"])
                    await ctx.emit("agent.log", {**event_preview("text", block["text"], 2000),
                                                 "subagent": bool(ev.get("parent_tool_use_id"))})
                elif block.get("type") == "tool_use":
                    if block.get("name") == "StructuredOutput" and block.get("id"):
                        unparsed = block.get("input") if isinstance(block.get("input"), dict) else {}
                        unparsed = unparsed.get("__unparsedToolInput")
                        fallback = unparsed.get("raw") if isinstance(unparsed, dict) else None
                        if isinstance(fallback, str):
                            st.structured_output_raw.setdefault(block["id"], fallback)
                    if block.get("name") in SHELL_TOOLS and block.get("id"):
                        st.shell_calls.add(block["id"])
                    await ctx.emit("agent.tool", {"id": block.get("id"), "name": block.get("name"),
                                                 "input_fingerprint": tool_input_fingerprint(
                                                     block.get("name"), block.get("input")),
                                                 "summary": tool_input_summary(block.get("input")),
                                                 **event_preview("input", block.get("input"), 400)})
        elif typ == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                await ctx.emit("agent.tool_result", {"id": block.get("tool_use_id")})
                if block.get("is_error"):
                    note_failed_output(st, block.get("content"))
                    raw = str(block.get("content") or "")
                    # Classify before the display preview truncates the tool's original error.
                    parse_error = "StructuredOutput" in raw and "could not be parsed as JSON" in raw
                    details = {}
                    if parse_error:
                        tool_id = str(block.get("tool_use_id") or "")
                        received = st.structured_output_raw.pop(tool_id, "")
                        saved = await _save_structured_output_failure(ctx, tool_id, received)
                        if received:
                            limit = ctx.settings.orchestrator.plan_max_chars
                            chars = len(received)
                            closed = received.rstrip().endswith(("}", "]"))
                            details = {
                                "structured_output_chars": chars,
                                "structured_output_closed": closed,
                                "structured_output_near_limit": chars >= int(limit * 0.9),
                                **({"structured_output_failure_path": saved} if saved else {}),
                            }
                    await ctx.emit("agent.tool_error", {
                        **({"id": block["tool_use_id"]} if block.get("tool_use_id") is not None else {}),
                        **event_preview("text", block.get("content"), 400),
                        **({"structured_output_parse_error": True} if parse_error else {}),
                        **details,
                    })
                elif block.get("tool_use_id") in st.shell_calls:
                    note_command_ok(st)
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
