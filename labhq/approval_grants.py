"""Request-scoped approval classification.

Only the runner may supply the policy context used here. A classification is
advisory until the gateway records a person's explicit grant for the request.
"""

from __future__ import annotations

import re
import posixpath
from typing import Any

from .policy import (SHELL_TOOLS, Unresolved, _absolute, _candidate_paths, _evaluate_tool,
                     _inside, _norm, _private_decision, _real, _zone_hit, evaluate_tool,
                     _shell_write_targets, git_bash_command, restricted_paths, touches, touches_resolved)
from .private_paths import _CD, credential_path, registry_access, shell_cd_bases
from .workspace_commands import literal_commands, workspace_command


_EXCLUDED_COMMAND = re.compile(
    r"\b(?:curl|wget|ssh|scp|sftp|rsync|ftp|nc|ncat|netcat|"
    r"invoke-webrequest|invoke-restmethod|irm|iwr|"
    r"rm|rmdir|del|erase|remove-item|rd)\b", re.I)


def classify(tool: str, tool_input: dict[str, Any], policy: Any, *, workdir: str,
             extra_dirs: tuple[str, ...] = (), private_paths: tuple[str, ...] = (),
             private_enabled: bool = False, private_open_reads: tuple[str, ...] = (),
             read_only: bool = False, trace: list[str] | None = None) -> str | None:
    """Return a grant category for a shell ask, or None for an individual card.

    Decisions are recomputed using runner-owned settings, not the caller's
    reason. Mixed commands are checked independently of the early internal
    zone decision. Dynamic code is not an OS sandbox.
    """
    def excluded(reason: str) -> None:
        if trace is not None:
            trace.append(reason)
        return None

    if tool not in SHELL_TOOLS or not workdir or not isinstance(tool_input, dict):
        return excluded("unsupported")
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return excluded("missing_command")
    roots = [workdir, *extra_dirs]
    decision = evaluate_tool(tool, tool_input, policy, allowed_roots=roots, workdir=workdir,
                             private_paths=private_paths, private_enabled=private_enabled,
                             private_open_reads=private_open_reads, read_only=read_only)
    if decision.action != "ask":
        return excluded("policy_" + decision.action)
    checked = git_bash_command(tool, tool_input, roots)
    command = str(checked.get("command") or "")
    # Grant only a small, literal, read-only grammar. Executable and operand
    # checks are shared with the existing workspace permission proof; unknown
    # scripts, shell syntax and writes retain an individual decision.
    parts, _ = literal_commands(command)
    safe = {"cd", "pwd", "cat", "ls", "head", "tail", "grep", "sed",
            "find", "wc", "od", "sha256sum"}
    if (tool != "Bash" or parts is None
            or any(words[0] not in safe for words in parts)
            or any(word in {">", ">>", "<"} for words in parts for word in words)
            or not workspace_command(command, workdir,
                    [p for p in private_paths if not _inside(_norm(workdir), _norm(p))],
                    read_roots=[*extra_dirs, *(z.path for z in policy.data_zones if z.level == "internal")],
                    forbidden=restricted_paths(policy))):
        return excluded("unproven_literal_read")
    if _EXCLUDED_COMMAND.search(command):
        return excluded("network_or_delete_mention")
    if registry_access(command):
        return excluded("registry")
    if re.search(r"(?:^|[\s\"'=])~[/\\]|\$(?:\{(?:env:)?(?:home|userprofile)\}|(?:env:)?(?:home|userprofile)\b)|%(?:home|userprofile)%", command, re.I):
        return excluded("home_spelling")  # do not mistake a home path for a task folder
    targets = list(_shell_write_targets(command, powershell=tool == "PowerShell"))
    changes = list(re.finditer(r"\b(?:cd|chdir|pushd|set-location|sl)\b", command, re.I))
    changed_dir = bool(changes)
    bases = shell_cd_bases(command, workdir) if changed_dir else []
    # A shell can branch, change folders repeatedly, or compute the destination.
    # Check every known base; unknown bases remain a separate grant category.
    unknown_write = changed_dir and (bases is None or len(bases) != len(changes)
                                     or len(list(_CD.finditer(command))) != len(changes))
    for target in targets:
        if re.search(r"[$`*?%]", target):
            unknown_write = True
            continue
        for base in ([workdir, *(bases or [])] if not _absolute(target) else [workdir]):
            path = target if _absolute(target) else posixpath.join(_norm(base), target)
            if not _inside(_norm(path), _norm(workdir)):
                return excluded("outside_write_target")
            resolved = _real(path)
            root = _real(workdir)
            if not resolved or not root:
                unknown_write = True
            elif not _inside(resolved, root):
                return excluded("outside_write_target")
    if any(credential_path(path) for path in _candidate_paths(command)):
        return excluded("credential")
    closed = restricted_paths(policy)
    closed_hit = touches(checked, closed, workdir=workdir)
    resolved_closed = touches_resolved(checked, closed, workdir=workdir)
    if closed_hit or (resolved_closed and not isinstance(resolved_closed, Unresolved)):
        return excluded("restricted_zone")

    # Internal-zone reads return early in the regular policy. Remove only those
    # zones for a second pass so scheduler and write checks still run.
    rest = policy.model_copy(update={"data_zones": [z for z in policy.data_zones if z.level != "internal"]})
    if _evaluate_tool(tool, checked, rest, roots, workdir).action != "allow":
        return excluded("other_policy_ask_or_deny")

    # A private parent of this workdir does not itself identify another person's
    # path. An explicitly named sibling and any protected child stay excluded.
    work = _norm(workdir)
    private = list(private_paths)
    if touches(checked, private, workdir=workdir, exempt=work):
        return excluded("other_private_path")
    protected = [p for p in private if not _inside(work, _norm(p))]
    resolved_private = touches_resolved(checked, private, workdir=workdir, exempt=_real(workdir))
    if resolved_private and not isinstance(resolved_private, Unresolved):
        return excluded("resolved_private_path")
    other = _private_decision(tool, checked, protected, workdir, None, None,
                              list(private_open_reads), closed)
    if other and ("closed or credential" in other.reason or "names a PI personal" in other.reason
                  or "registry" in other.reason):
        return excluded("other_private_or_registry")
    internal = _zone_hit(checked, policy, {"internal"}, workdir, own_workdir=True)
    if internal and not isinstance(internal, Unresolved):
        return "internal_read"
    if (unknown_write or isinstance(internal, Unresolved) or isinstance(resolved_closed, Unresolved)
            or isinstance(resolved_private, Unresolved)
            or "not all resolved" in decision.reason
            or (other and "not all resolved" in other.reason)
            or re.search(r"\$|`|\*|\?|\b(?:for|while)\b", command, re.I)):
        return "unresolved_path"
    return "workspace"
