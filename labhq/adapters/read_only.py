"""What a read-only task (consult, follow-up) runs with, decided in one place (#36)."""

from __future__ import annotations

import copy

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
