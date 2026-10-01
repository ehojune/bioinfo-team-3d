"""What a read-only task (consult, follow-up) runs with, decided in one place (#36)."""

from __future__ import annotations

import copy
from pathlib import Path, PurePath

from ..models import AgentSpec

# Consults and follow-ups answer from existing work; they never write or submit. Their run is an allowlist, not
# the staff spec minus a list of risky fields: MCP servers, plugins (and their hooks), pre-approved tools and CLI
# templates all run outside the engine's sandbox or plan mode, and a field added to AgentSpec later would be a new
# channel nobody removed (#36). `read_only_profile` keeps who the staff member is and switches everything else off.
READ_ONLY_FIELDS = {"sandbox": "read-only", "permission_mode": "plan", "builtin_tools": "Read,Glob,Grep",
                    "tools": [], "builtin_mcp": [], "mcp": [], "plugin_dirs": [],
                    "allow_skills": False, "required_skills": [], "cli": None, "can_orchestrate": False}
# Identity, persona, limits and denials: none of these lets the CLI run anything. project_dirs stay readable;
# disallowed_tools (e.g. a Read deny on a data folder) only take away, so a read-only run keeps them.
READ_ONLY_KEEPS = ("id", "name", "role", "character", "engine", "model", "system_prompt", "project_dirs",
                   "disallowed_tools", "max_turns", "max_budget_usd", "employment", "contract", "tags")
# The marker consults and follow-ups carry in task meta. The runner never applies it: it rebuilds the profile.
READ_ONLY_OVERRIDES = copy.deepcopy(READ_ONLY_FIELDS)
# What a read-only run takes from `engines.<engine>.env` (#145): where the CLI keeps its login and settings, the
# credentials it signs in with, and how it reaches the API. The rest is left out, since a variable can change what
# the CLI loads: on Claude 2.1.282 CLAUDE_CODE_PLUGIN_DIRS loaded a plugin whose hooks and MCP stayed off only because
# of disableAllHooks and --strict-mcp-config, and CLAUDE_CODE_MANAGED_SETTINGS_PATH, CLAUDE_CODE_SYNC_PLUGINS,
# NODE_OPTIONS or a CODEX_* variable mean whatever the next CLI release makes them mean. The parent session's own
# CLAUDE_*/CODEX_* are already gone (util.merge_staff_env). Names compare case-insensitively, as Windows does.
READ_ONLY_ENV_KEEP = frozenset({
    "PATH", "HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_REGION", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "CODEX_CA_CERTIFICATE",
})
# What an engine reads from its working folder as instructions or configuration and no read-only flag switches off
# (#147). A reused workspace can hold any of them from an earlier writable run, and the follow-up would take its
# instructions from there. A read-only run refuses rather than guess whether the CLI loads them; only what labhq
# itself puts there (a contract skill, `labhq_workspace_paths`) is exempt. Handled elsewhere, so not listed:
# Claude's .claude/settings*.json (--setting-sources ""), .mcp.json (--strict-mcp-config), CLAUDE.md files
# (claudeMdExcludes); Codex's AGENTS.md (labhq rewrites it) and AGENTS.override.md (refused for every Codex run).
READ_ONLY_WORKSPACE_REFUSED: dict[str, tuple[str, ...]] = {
    "claude_code": ("AGENTS.md", "AGENTS.override.md"),  # the built-in agents-md plugin; not measured
    "codex": (".agents",),  # project skills ($CWD/.agents/skills)
}
# Where TaskWorkspace.install_skill copies a contract staff member's paper skill.
SKILL_DIRS = (".claude/skills", ".agents/skills")


def is_read_only_task(meta: dict | None) -> bool:
    meta = meta or {}
    return (meta.get("kind") in {"consult", "followup"}
            or (meta.get("agent_overrides") or {}).get("sandbox") == "read-only")


def read_only_profile(agent: AgentSpec) -> AgentSpec:
    """The only spec a read-only task runs with: built from the staff member's identity, never from the sender.

    An AgentSpec field that is neither kept nor set here refuses the build, so a new capability is off until
    someone decides it is safe for a read-only run.
    """
    unclassified = set(AgentSpec.model_fields) - set(READ_ONLY_KEEPS) - set(READ_ONLY_FIELDS)
    if unclassified:
        raise RuntimeError(f"read-only profile does not classify AgentSpec fields: {sorted(unclassified)}")
    kept = {name: copy.deepcopy(getattr(agent, name)) for name in READ_ONLY_KEEPS}
    return AgentSpec(**kept, **copy.deepcopy(READ_ONLY_FIELDS))


def read_only_mismatch(agent: AgentSpec, mcp_servers: list) -> str | None:
    """Why a run marked read-only must not start: its agent is not exactly the profile, or MCP servers are wired."""
    if mcp_servers or agent != read_only_profile(agent):
        return "read-only run refused: the agent is not the read-only profile"
    return None


def read_only_engine_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The part of `engines.<engine>.env` a read-only run keeps, and the names it leaves out (never the values)."""
    kept = {key: value for key, value in env.items() if key.upper() in READ_ONLY_ENV_KEEP}
    return kept, sorted(set(env) - set(kept))


def labhq_workspace_paths(agent: AgentSpec) -> list[str]:
    """Workspace paths (relative, POSIX) that labhq itself writes for this staff member: its contract skill."""
    skill = agent.contract.skill_dir if agent.contract else None
    return [f"{base}/{PurePath(skill).name}" for base in SKILL_DIRS] if skill else []


def _foreign_entry(workdir: Path, name: str, owned: list[str]) -> str | None:
    """The first entry at or under `workdir/name` that labhq did not write, without following links."""
    pending = [workdir / name]
    while pending:
        path = pending.pop()
        if not (path.exists() or path.is_symlink()):
            continue
        relative = path.relative_to(workdir).as_posix()
        if any(relative == mine or relative.startswith(mine + "/") for mine in owned):
            continue  # labhq's own copy
        if path.is_symlink() or not path.is_dir() or not any(mine.startswith(relative + "/") for mine in owned):
            return relative
        try:
            pending.extend(sorted(path.iterdir(), reverse=True))
        except OSError:
            return relative  # unreadable: cannot tell, so it counts
    return None


def read_only_workspace_error(engine: str, workdir: Path, owned: list[str]) -> str | None:
    """Why a read-only run must not start in this workspace: it holds a file its engine would read as instructions."""
    for name in READ_ONLY_WORKSPACE_REFUSED.get(engine, ()):
        found = _foreign_entry(Path(workdir), name, owned)
        if found:
            return (f"read-only run refused: the workspace holds {found}, which {engine} would read as instructions "
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
