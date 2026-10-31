"""What a read-only task (consult, follow-up) runs with, decided in one place (#36)."""

from __future__ import annotations

import copy
import fnmatch
import os
import sys
from pathlib import Path, PurePath

from ..models import AgentSpec
from .owned import case_sensitive_directory, is_link as _is_link

# Reviews, consults and follow-ups read existing work and write only their own outputs/temp. Their run is an allowlist, not
# the staff spec minus a list of risky fields: MCP servers, plugins (and their hooks), pre-approved tools and CLI
# templates all run outside the engine's sandbox or plan mode, and a field added to AgentSpec later would be a new
# channel nobody removed (#36). `read_only_profile` keeps who the staff member is and switches everything else off.
READ_ONLY_FIELDS = {"sandbox": "workspace-write", "permission_mode": "default", "builtin_tools": "Read,Glob,Grep,Write,Edit",
                    "tools": [], "builtin_mcp": [], "annot_tools": None, "mcp": [], "plugin_dirs": [],
                    "allow_skills": False, "required_skills": [], "cli": None, "can_orchestrate": False}
# Identity, persona, limits and denials: none of these lets the CLI run anything. project_dirs stay readable;
# disallowed_tools (e.g. a Read deny on a data folder) only take away, so a read-only run keeps them.
READ_ONLY_KEEPS = ("id", "name", "role", "character", "engine", "model", "model_tier", "effort", "system_prompt", "project_dirs",
                   "disallowed_tools", "max_turns", "max_budget_usd", "employment", "contract", "tags")
# The marker consults and follow-ups carry in task meta. The runner never applies it: it rebuilds the profile.
READ_ONLY_OVERRIDES = {**copy.deepcopy(READ_ONLY_FIELDS), "sandbox": "read-only"}
# What a read-only run takes from `engines.<engine>.env` (#145): where the CLI keeps its login and settings, the
# credentials it signs in with, and how it reaches the API. The rest is left out, since a variable can change what
# the CLI loads: on Claude 2.1.282 CLAUDE_CODE_PLUGIN_DIRS loaded a plugin whose hooks and MCP stayed off only because
# of disableAllHooks and --strict-mcp-config, and CLAUDE_CODE_MANAGED_SETTINGS_PATH, CLAUDE_CODE_SYNC_PLUGINS,
# NODE_OPTIONS or a CODEX_* variable mean whatever the next CLI release makes them mean. The parent session's own
# CLAUDE_*/CODEX_* are already gone (util.merge_staff_env). Names compare case-insensitively, as Windows does.
# Bedrock and Vertex logins name their credential files and endpoints too (#165): without them a follow-up signs in
# differently from the step it follows, or not at all.
READ_ONLY_ENV_KEEP = frozenset({
    "PATH", "HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_REGION", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE", "ANTHROPIC_BEDROCK_BASE_URL", "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_VERTEX_BASE_URL", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CODEX_CA_CERTIFICATE",
})
# Per-model Vertex regions (`VERTEX_REGION_CLAUDE_3_5_HAIKU`): a family of names, matched by prefix.
READ_ONLY_ENV_KEEP_PREFIXES = ("VERTEX_REGION_",)
# Workspace names the adapters say their engines load as instructions, memory, skills or configuration. This is the
# single policy list and `workspace_instruction_action` is the single matcher: depth and dot-prefixed ancestors do
# not change the answer. Claude can exclude its own project sources with flags/settings; agents-md has no measured
# off switch, and Codex project sources are refused because their trusted-workspace behaviour remains unverified.
WORKSPACE_INSTRUCTION_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "claude_code": {
        "exclude_files": ("CLAUDE.md", "CLAUDE.local.md"),
        "exclude_dirs": (".claude",),
        "exclude_root_globs": (".claude/CLAUDE.md", ".claude/rules/**"),
        "refuse_files": ("AGENTS*.md",),
        "refuse_dirs": (".agents",),
    },
    "codex": {
        "exclude_files": (),
        "exclude_dirs": (),
        "exclude_root_globs": (),
        "refuse_files": ("AGENTS*.md",),
        "refuse_dirs": (".agents", ".codex"),
    },
}
# Where TaskWorkspace.install_skill copies a contract staff member's paper skill.
SKILL_DIRS = (".claude/skills", ".agents/skills")
# Windows and macOS file systems ignore letter case by default: there `claude.md` is the CLAUDE.md an engine opens
# and `.Agents/` is `.agents/` (#190). Names are then compared case-insensitively; Linux compares them exactly.
CASE_INSENSITIVE = os.name == "nt" or sys.platform == "darwin"


def _folded(text: str, case_sensitive: bool | None = None) -> str:
    insensitive = CASE_INSENSITIVE if case_sensitive is None else not case_sensitive
    return text.casefold() if insensitive else text


def is_read_only_task(meta: dict | None, agent: AgentSpec | None = None) -> bool:
    meta = meta or {}
    return (meta.get("kind") in {"consult", "followup", "review", "slot_repair", "blind_draft"}
            or (agent is not None and (agent.sandbox == "read-only" or agent.permission_mode == "plan"))
            or (agent is not None and bool(agent.builtin_tools) and set(agent.builtin_tools.split(","))
                <= {"Read", "Glob", "Grep", "WebSearch", "WebFetch"})
            or (meta.get("agent_overrides") or {}).get("sandbox") == "read-only")


TOOL_NOTICE_HEAD = "## Tools in this read-only run"


def available_tools(fields: dict, denied: tuple | list = ()) -> list[str]:
    """The tools a read-only run really has, by the name an instruction would use: built-in file tools, the engine's
    web tools the profile kept, and the labhq_annot tools of an explicit allowlist (#1224). A tool the staff file denies
    outright (`disallowed_tools: [Write]`) is not listed; a denial of part of a tool (`Read(./data/**)`) leaves it."""
    gone = {name for name in denied if isinstance(name, str) and "(" not in name}
    names = [name for name in (fields.get("builtin_tools") or "").split(",") if name and name not in gone]
    names += [name for name in fields.get("tools") or [] if isinstance(name, str) and name not in gone]
    annot = fields.get("annot_tools")
    if "annot" in (fields.get("builtin_mcp") or []) and annot:
        names.append("labhq_annot (" + ", ".join(annot) + ")")
    return names


def with_tool_notice(prompt: str, fields: dict, denied: tuple | list = ()) -> str:
    """`prompt` plus what this read-only run can use. The persona and the role text are written for the staff member's
    ordinary run, where MCP servers and web tools exist; a read-only profile drops them, and without this the
    instructions keep sending the model to tools it does not have (#1224). Appended once: a profile of a profile
    keeps the notice it already has."""
    if TOOL_NOTICE_HEAD in prompt:
        return prompt
    tools = ", ".join(available_tools(fields, denied)) or "none"
    return (prompt.rstrip() + "\n\n" + TOOL_NOTICE_HEAD + "\nAvailable now: " + tools + ".\n"
            "Every other tool or service named in the instructions above (for example PubMed or bioRxiv tools, other "
            "labhq_annot tools, shell commands) is not available in this run. Do not try it; say what you "
            "could not check instead.")


def read_only_profile(agent: AgentSpec) -> AgentSpec:
    """The only spec a read-only task runs with: built from the staff member's identity, never from the sender.

    An AgentSpec field that is neither kept nor set here refuses the build, so a new capability is off until
    someone decides it is safe for a read-only run.
    """
    unclassified = set(AgentSpec.model_fields) - set(READ_ONLY_KEEPS) - set(READ_ONLY_FIELDS)
    if unclassified:
        raise RuntimeError(f"read-only profile does not classify AgentSpec fields: {sorted(unclassified)}")
    kept = {name: copy.deepcopy(getattr(agent, name)) for name in READ_ONLY_KEEPS}
    fields = copy.deepcopy(READ_ONLY_FIELDS)
    # Skill tools and plugins stay off. Preserve the safe writing guidance as plain role instructions instead.
    from ..staff_skills import bundled_read_only_rules, bundled_requirements
    kept["system_prompt"] = kept["system_prompt"].rstrip() + bundled_read_only_rules(bundled_requirements(agent))
    if agent.engine == "claude_code":
        fields["builtin_mcp"] = ["approval"]
    elif agent.engine == "codex":
        fields["tools"] = [t for t in agent.tools if t in {"WebSearch", "WebFetch"}]
    if annot_allowlist(agent):
        # The one MCP server a read-only run may keep (#1055): labhq_annot, and only the tools that are both named by
        # the staff file and in READ_ONLY_ANNOT_TOOLS. They are public read-only lookups that labhq counts, limits in
        # time and records; the server offers nothing outside the list. A file without an explicit list gets nothing.
        fields["builtin_mcp"] = [*fields["builtin_mcp"], "annot"]
        fields["annot_tools"] = read_only_annot_tools(agent)
    if agent.id == "facilities":
        # Diagnosis is supplied as context; this staff member needs no execution or write tools.
        fields.update(sandbox="read-only", builtin_tools="", builtin_mcp=[], tools=[])
        kept["project_dirs"] = []
    kept["system_prompt"] = with_tool_notice(kept["system_prompt"], fields, kept["disallowed_tools"])
    return AgentSpec(**kept, **fields)


# The labhq_annot tools a read-only run may ever keep. A tool is added here on purpose, after someone decided that a
# review or follow-up may call it: the staff file's own `annot_tools` list can only narrow this, never widen it (#1055).
READ_ONLY_ANNOT_TOOLS = ("pdb", "chembl")


def read_only_annot_tools(agent: AgentSpec) -> list[str]:
    """What a read-only run keeps of the staff file's `annot_tools`: its explicit names that are also in
    READ_ONLY_ANNOT_TOOLS. Nothing (the server stays off) for a file without the annot server or without a list."""
    if "annot" not in agent.builtin_mcp or not agent.annot_tools:
        return []
    return [tool for tool in agent.annot_tools if tool in READ_ONLY_ANNOT_TOOLS]


def annot_allowlist(agent: AgentSpec) -> bool:
    return bool(read_only_annot_tools(agent))


def _read_only_server_ok(agent: AgentSpec, server) -> bool:
    if (agent.engine == "claude_code" and server.name == "labhq_approval"
            and server.args == ["-m", "labhq.tools.approval_mcp"]):
        return True
    return (annot_allowlist(agent) and server.name == "labhq_annot" and server.args == ["-m", "labhq.tools.annot_mcp"]
            and (server.env or {}).get("LABHQ_ANNOT_TOOLS") == ",".join(read_only_annot_tools(agent)))


def read_only_mismatch(agent: AgentSpec, mcp_servers: list) -> str | None:
    """Refuse a non-profile agent or any MCP server except the runner's scoped Claude permission gate and, for a staff
    file with an explicit `annot_tools` list, labhq_annot offering exactly those tools."""
    if any(not _read_only_server_ok(agent, s) for s in mcp_servers) or agent != read_only_profile(agent):
        return "read-only run refused: the agent is not the read-only profile"
    return None


def read_only_engine_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The part of `engines.<engine>.env` a read-only run keeps, and the names it leaves out (never the values)."""
    kept = {key: value for key, value in env.items()
            if key.upper() in READ_ONLY_ENV_KEEP or key.upper().startswith(READ_ONLY_ENV_KEEP_PREFIXES)}
    return kept, sorted(set(env) - set(kept))


def _tree(root: Path, follow: bool) -> dict[str, bytes] | None:
    """Every file below `root` by relative name and content; None when `follow` is off and a link is found."""
    files: dict[str, bytes] = {}
    pending = [Path(root)]
    while pending:
        folder = pending.pop()
        for entry in os.scandir(folder):
            path = Path(entry.path)
            if not follow and _is_link(path):
                return None
            if path.is_dir():
                pending.append(path)
            else:
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def installed_skill_matches(source: Path, copy: Path) -> bool:
    """Whether `copy` holds exactly what TaskWorkspace.install_skill copies from `source` (#165).

    install_skill copies nothing when the source has no SKILL.md, and an earlier run may have written its own skill
    under the same name; only a copy equal to the source, with no link in it, is labhq's.
    """
    try:
        if not (Path(source) / "SKILL.md").is_file() or not os.path.lexists(copy) or _is_link(copy):
            return False
        return _tree(Path(source), follow=True) == _tree(Path(copy), follow=False)
    except OSError:
        return False


def labhq_workspace_paths(agent: AgentSpec, engine: str, workdir: Path | None = None) -> list[str]:
    """Instruction paths labhq replaces immediately before this engine starts.

    With `workdir`, a contract skill copy counts only while it still equals its source (#165).
    """
    skill = agent.contract.skill_dir if agent.contract else None
    paths = [f"{base}/{PurePath(skill).name}" for base in SKILL_DIRS] if skill else []
    if skill and workdir is not None:
        paths = [path for path in paths if installed_skill_matches(Path(skill), Path(workdir) / path)]
    from ..staff_skills import bundled_skill_source
    for name in agent.required_skills:
        source = bundled_skill_source(name)
        if source is None:
            continue
        for base in SKILL_DIRS:
            relative = f"{base}/{name}"
            if workdir is None or installed_skill_matches(source, Path(workdir) / relative):
                paths.append(relative)
    if engine == "codex":
        paths.append("AGENTS.md")
    return paths


def workspace_instruction_action(engine: str, relative: PurePath, case_sensitive: bool | None = None) -> str | None:
    """Return `exclude` or `refuse` for one engine-read workspace path, independent of depth."""
    rules = WORKSPACE_INSTRUCTION_RULES.get(engine, {})
    parts = [_folded(part, case_sensitive) for part in relative.parts]
    name = parts[-1] if parts else ""
    for action in ("refuse", "exclude"):
        if any(part in {_folded(d, case_sensitive) for d in rules.get(f"{action}_dirs", ())} for part in parts):
            return action
        if any(fnmatch.fnmatchcase(name, _folded(pattern, case_sensitive))
               for pattern in rules.get(f"{action}_files", ())):
            return action
    return None


def workspace_instruction_paths(engine: str, workdir: Path, owned: list[str], action: str) -> list[str]:
    """Find matching paths at every depth without following symlinks or Windows junctions."""
    found: list[str] = []
    case_sensitive = case_sensitive_directory(workdir)
    owned = [_folded(mine, case_sensitive) for mine in owned]
    pending = [Path(workdir)]
    while pending:
        path = pending.pop()
        try:
            entries = sorted(path.iterdir(), reverse=True)
        except OSError:
            continue
        for entry in entries:
            relative = entry.relative_to(workdir).as_posix()
            try:
                linked = _is_link(entry)
            except OSError:
                linked = True
            key = _folded(relative, case_sensitive)
            if any(key == mine or key.startswith(mine + "/") for mine in owned):
                continue
            owns_below = any(mine.startswith(key + "/") for mine in owned)
            matches = workspace_instruction_action(engine, PurePath(relative), case_sensitive) == action
            is_dir = False if linked else entry.is_dir()
            if matches and (not is_dir or action == "refuse") and (not owns_below or linked):
                found.append(relative)
                continue
            if is_dir:
                pending.append(entry)
    return found


def read_only_workspace_error(engine: str, workdir: Path, owned: list[str]) -> str | None:
    """Why a read-only run must not start in this workspace: it holds a file its engine would read as instructions."""
    found = workspace_instruction_paths(engine, Path(workdir), owned, "refuse")
    if found:
        return (f"read-only run refused: the workspace holds {found[0]}, which {engine} would read as instructions "
                "or configuration; a read-only run takes those only from labhq. Move it out of the workspace or "
                "ask in a new request")
    return None


def read_only_launch_error(engine: str, prefix_args: list[str]) -> str | None:
    """Why a read-only run must not start with these `engines.<engine>.prefix_args` (already expanded).

    prefix_args are for what an interpreter runs (`bin: node`, `prefix_args: [cli.js]`) and land ahead of every
    agent argument, so an option there reaches the CLI as extra_args would. Probed on codex-cli 0.159.2:
    `--dangerously-bypass-approvals-and-sandbox` ahead of `exec` wrote a file under `-s read-only`. A read-only run
    drops extra_args; an option in prefix_args refuses it, since only the PI can tell which options are safe.
    """
    if any(arg.startswith("-") for arg in prefix_args):
        return (f"read-only run refused: engines.{engine}.prefix_args holds a CLI option; keep only the script "
                "path there and put options in extra_args, which a read-only run leaves out")
    return None
