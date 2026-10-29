"""Experimental Claude Bash sandbox policy; not an enabled runner capability.

Claude's filesystem and network sandbox covers shell tools, not Read/Edit/Write,
MCP, hooks, the engine process, or snapshot immutability. No production caller
may relax the existing approval gate based on this module.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


class SandboxUnavailable(ValueError):
    """The proposed policy or launch cannot be proved safe."""


# A deliberately offline baseline. Working directory and Claude's own temp
# directory are writable by default; neither implies a sealed snapshot.
_POLICY: dict[str, Any] = {
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": False,
        "excludedCommands": [],
        "enableWeakerNestedSandbox": False,
        "enableWeakerNetworkIsolation": False,
        "filesystem": {"disabled": False},
        "network": {"allowedDomains": [], "strictAllowlist": True, "allowUnixSockets": [],
                    "allowAllUnixSockets": False, "allowLocalBinding": False},
    },
    "permissions": {"blockReadsOutsideWorkingDirectories": True},
}


def baseline_policy() -> dict[str, Any]:
    """Return a fresh policy document, not a claim about effective settings."""
    return deepcopy(_POLICY)


def validate_policy(*, managed: Mapping[str, Any], user: Mapping[str, Any],
                    project: Mapping[str, Any], local: Mapping[str, Any],
                    cli: Mapping[str, Any]) -> None:
    """Check a closed, isolated source set; callers must establish its provenance.

    This intentionally rejects even apparently harmless unknown keys and empty
    overrides. Claude merges arrays and has additional settings sources and
    permission rules, so a permissive hand-rolled merge is not evidence of the
    policy Claude will apply. Exact type checking also rejects 0/1 for booleans.
    """
    if not _exact(managed, _POLICY):
        raise SandboxUnavailable("managed policy differs from offline baseline")
    for name, source in (("user", user), ("project", project),
                         ("local", local), ("cli", cli)):
        if type(source) is not dict or source:
            raise SandboxUnavailable(f"unisolated {name} settings")


def _exact(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _exact(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def build_launch() -> None:
    """Fail closed until effective settings and WSL enforcement are attested.

    There is currently no trusted mechanism binding Claude's loaded settings,
    executable, setting sources, and sandbox availability to a launch. A caller
    cannot supply a boolean or mock assertion to turn this into a capability.
    """
    raise SandboxUnavailable("Claude sandbox launch is unsupported")
