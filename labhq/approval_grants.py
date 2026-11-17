"""Request-scoped approval classification.

Only the runner may supply the policy context used here. A classification is
advisory until the gateway records a person's explicit grant for the request.
"""

from __future__ import annotations

import os
import re
import posixpath
from typing import Any, Iterator

from .policy import (SHELL_TOOLS, Unresolved, _absolute, _candidate_paths, _evaluate_tool,
                     _blank_non_syntax, _inside, _norm, _private_decision, _real, _word_value, _zone_hit, evaluate_tool,
                     _shell_write_targets, git_bash_command, restricted_paths, shell_segments, touches,
                     touches_resolved)
from .private_paths import _CD, claude_project_slug, credential_path, registry_access, shell_cd_bases
from .workspace_commands import literal_commands, workspace_command


# Network access and deletion stay individual cards, wherever they are mentioned.
_EXCLUDED_COMMAND = re.compile(
    r"\b(?:curl|wget|gh|git|openssl|ssh|scp|sftp|rsync|ftp|nc|ncat|netcat|"
    r"invoke-webrequest|invoke-restmethod|irm|iwr|requests|urllib|socket|httpx|aiohttp|"
    r"ftplib|smtplib|imaplib|poplib|telnetlib|paramiko|http\.(?:client|server)|"
    r"rm|rmdir|remove|removedirs|unlink|rmtree|shred|del|delete|clean|truncate|erase|remove-item|rd|"
    r"os\.remove|os\.unlink|shutil\.rmtree|path\.unlink)\b", re.I)
# A move or rename writes every operand (the source disappears). It is grouped by where those operands
# resolve; a mention that is not a plain mover command, such as `xargs mv` or `shutil.move`, is unresolved.
_MOVERS = {"mv", "move", "move-item", "rename", "rename-item", "ren", "rni", "mi"}
_MOVE_MENTION = re.compile(r"\b(?:" + "|".join(sorted(_MOVERS, key=len, reverse=True)) + r")\b", re.I)
_DYNAMIC_QUOTE = re.compile(r"\$'((?:[^'\\]|\\.)*)'|\$\"((?:[^\"\\]|\\.)*)\"", re.S)
_ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|c.?|[0-7]{1,3}|.)", re.S)
_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _decode_dynamic_quotes(command: str) -> str:
    """Bash's $'...' escapes and $"..." text, decoded so the checks read what the shell will run."""
    def escape(m: re.Match[str]) -> str:
        code = m.group(1)
        if code[0] in "xuU":
            value = int(code[1:], 16)
            return chr(value) if value < 0x110000 else ""
        if code[0] == "c":
            return chr(ord(code[1:2] or "@") & 0x1F)
        if code[0] in "01234567":
            return chr(int(code, 8) & 0xFF)
        return _ESCAPES.get(code, "\\" + code)
    return _DYNAMIC_QUOTE.sub(lambda m: _ESCAPE.sub(escape, m.group(1)) if m.group(1) is not None
                              else m.group(2), command)


def _move_edit_targets(command: str, powershell: bool) -> tuple[list[str], bool]:
    """Operands a move, rename or in-place Perl edit writes, and whether a mover was mentioned but not parsed.

    `sed -i` inputs already come from the shared write-target parser."""
    targets: list[str] = []
    parsed = 0
    unknown = False
    mentioned = len(_MOVE_MENTION.findall(re.sub(r"[\\\"'`]", "", command)))
    if _blank_non_syntax(command, powershell) is None:
        # Quoting could not be split, so operands would be guessed from raw text: leave them unresolved.
        return [], bool(mentioned or re.search(r"\bperl\b", command, re.I))
    for raw in shell_segments(command, powershell):
        words = [_word_value(word) for word in raw]
        if not words:
            continue
        name = words[0].casefold()
        name = name[:-4] if name.endswith(".exe") else name
        if name in _MOVERS:
            parsed += 1
            for word in words[1:]:
                if not word.startswith("-") or word == "-":
                    targets.append(word)
                elif word.startswith("--") and "=" in word:
                    targets.append(word.split("=", 1)[1])
                elif not _plain_mover_option(word, powershell):
                    # An option may carry its own path (`-t../out`, `-Destination:..`): not read here.
                    unknown = True
        elif name == "perl":
            targets.extend(_perl_in_place_files(words[1:]))
    # Every mention must be a parsed mover command (`mv a b; xargs mv` leaves one unparsed).
    return targets, unknown or mentioned > parsed


def _plain_mover_option(word: str, powershell: bool) -> bool:
    """An option word that names no path itself; its value, if any, is the next word."""
    if powershell:
        return bool(re.fullmatch(r"-[A-Za-z]+", word))  # parameter names; `-Name:value` is not plain
    return (bool(re.fullmatch(r"-[bfinuvTZt]+", word)) and ("t" not in word or word.endswith("t"))
            or word in {"--", "--force", "--interactive", "--no-clobber", "--verbose", "--update", "--backup",
                        "--no-target-directory", "--strip-trailing-slashes", "--target-directory"})


def _perl_in_place_files(words: list[str]) -> Iterator[str]:
    """File operands of `perl -i` (`-pi -e CODE FILE...`, `-i.bak SCRIPT FILE...`)."""
    in_place = code_given = skip = ended = False
    operands: list[str] = []
    for word in words:
        if skip:
            skip = False
            continue
        if not operands and not ended and word == "--":
            ended = True
            continue
        if not operands and not ended and word.startswith("-") and len(word) > 1:
            letters = word[1:]
            if "i" in letters:
                in_place = True
                letters = letters[:letters.index("i")]  # the rest is the backup extension
            if letters.endswith(("e", "E")):
                code_given = skip = True
            continue
        operands.append(word)
    if in_place:
        yield from (operands if code_given else operands[1:])


def _own_tool_results(workdir: str, open_reads: tuple[str, ...]) -> list[str]:
    """`tool-results` of the staff's current Claude session for this task folder.

    The runner opens <staff config>/projects/<slug of the task folder> for reads. Its newest session
    transcript is taken as the running session; other sessions and other config files are not included."""
    slugs = {claude_project_slug(os.path.abspath(workdir)).casefold()}
    try:
        slugs.add(claude_project_slug(os.path.realpath(workdir)).casefold())
    except (OSError, ValueError):
        pass
    found = []
    for folder in open_reads:
        norm = _norm(folder).rstrip("/")
        if (posixpath.basename(norm).casefold() not in slugs
                or posixpath.basename(posixpath.dirname(norm)).casefold() != "projects"):
            continue
        try:
            sessions = [entry for entry in os.scandir(folder)
                        if entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=False)]
            newest = max(sessions, key=lambda entry: entry.stat(follow_symlinks=False).st_mtime_ns, default=None)
        except OSError:
            continue
        if newest and re.fullmatch(r"[0-9A-Za-z-]+", newest.name[:-len(".jsonl")]):
            found.append(_norm(posixpath.join(norm, newest.name[:-len(".jsonl")], "tool-results")))
    return found


def _mask_own_results(command: str, own: list[str]) -> str:
    """Replace each literal reference to a file directly in `own` with a task-local name, for the private checks.

    A reference that also resolves elsewhere through a link is left as written and judged as before."""
    if not own:
        return command
    real_own = [_real(folder) for folder in own]
    for token in sorted(set(_candidate_paths(command)), key=len, reverse=True):
        if re.search(r"[$`*?%]", token) or ".." in token.replace("\\", "/").split("/") or token not in command:
            continue
        norm = _norm(token)
        index = next((i for i, folder in enumerate(own) if posixpath.dirname(norm) == folder), None)
        if index is None:
            continue
        resolved = _real(token)
        if not resolved or not real_own[index] or posixpath.dirname(resolved) != real_own[index]:
            continue
        command = command.replace(token, "tool-results-" + posixpath.basename(norm))
    return command


def capture_staff_env(adapter: Any) -> None:
    """Remember the environment actually built by the adapter for each spawn."""
    build = getattr(adapter, "staff_env", None)
    if not callable(build):
        return  # Other adapters continue normally; no snapshot means no grant.
    def captured(ctx: Any) -> dict[str, str]:
        env = build(ctx)
        ctx._approval_grant_env = dict(env)
        return env
    adapter.staff_env = captured


def classify(tool: str, tool_input: dict[str, Any], policy: Any, *, workdir: str,
             extra_dirs: tuple[str, ...] = (), private_paths: tuple[str, ...] = (),
             private_enabled: bool = False, private_open_reads: tuple[str, ...] = (),
             read_only: bool = False, trace: list[str] | None = None,
             environ: dict[str, str] | None = None, _decoded: bool = False) -> str | None:
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
    if environ and any((key.casefold() in {"bash_env", "env", "cdpath"} and value)
                       or key.casefold().startswith("bash_func_")
                       or (value and (key.casefold().startswith(("ld_", "dyld_"))
                                      or key.casefold() in {"libpath", "shlib_path", "gconv_path", "locpath"}))
                       for key, value in environ.items()):
        return excluded("shell_startup_environment")
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return excluded("missing_command")
    # $'...' and $"..." can spell any name. Every check below also reads the decoded text, and the
    # command can then only join the unresolved-path category (the final `$` test).
    decoded = _decode_dynamic_quotes(command)
    # Collapse shell quote/escape characters for detection; this intentionally
    # overmatches text when a protected assignment could be executed.
    assignment_text = re.sub(r"[\\\"'`]", "", command if decoded == command else command + "\n" + decoded)
    if re.search(r"\b(?:LD_\w*|DYLD_\w*|BASH_FUNC_\w*|BASH_ENV|ENV|CDPATH|"
                 r"LIBPATH|SHLIB_PATH|GCONV_PATH|LOCPATH)\s*\+?=", assignment_text, re.I):
        return excluded("shell_startup_environment")
    if decoded != command and not _decoded:
        # Judge the decoded text as its own command too, so every path check (closed zones, private paths,
        # write targets) sees the name the shell will use. Only a reason found there excludes this command;
        # the decoded text failing a literal-read proof or needing no card at all says nothing about it.
        inner: list[str] = []
        if classify(tool, {**tool_input, "command": decoded}, policy, workdir=workdir, extra_dirs=extra_dirs,
                    private_paths=private_paths, private_enabled=private_enabled,
                    private_open_reads=private_open_reads, read_only=read_only, trace=inner,
                    environ=environ, _decoded=True) is None and inner and inner[0] not in {
                        "policy_allow", "unproven_literal_read", "unproven_internal_read"}:
            return excluded(inner[0])
    roots = [workdir, *extra_dirs]
    decision = evaluate_tool(tool, tool_input, policy, allowed_roots=roots, workdir=workdir,
                             private_paths=private_paths, private_enabled=private_enabled,
                             private_open_reads=private_open_reads, read_only=read_only, environ=environ)
    if decision.action != "ask":
        return excluded("policy_" + decision.action)
    checked = git_bash_command(tool, tool_input, roots)
    command = str(checked.get("command") or "")
    scanned = command if decoded == tool_input.get("command") else command + "\n" + decoded
    # The staff may read its own session's saved long output (Claude's tool-results file).
    own_results = _own_tool_results(workdir, private_open_reads)
    # Literal reads retain the executable and operand proof. Analysis scripts
    # and shell expressions are included by explicit request-scoped consent;
    # this classification cannot inspect their runtime behavior.
    parts, _ = literal_commands(command)
    safe = {"cd", "pwd", "cat", "ls", "head", "tail", "grep", "sed",
            "find", "wc", "od", "sha256sum"}
    literal_read = (tool == "Bash" and parts is not None
                    and all(words[0] in safe for words in parts)
                    and not any(word in {">", ">>", "<"} for words in parts for word in words)
                    # `sed -i` edits its inputs: a write, grouped by where those inputs resolve.
                    and not any(words[0] == "sed" and any(re.match(r"-[^-]*i|--in-place", word) for word in words[1:])
                                for words in parts))
    if (literal_read and not workspace_command(command, workdir,
                    [p for p in private_paths if not _inside(_norm(workdir), _norm(p))],
                    read_roots=[*extra_dirs, *(z.path for z in policy.data_zones if z.level == "internal"),
                                *own_results],
                    forbidden=restricted_paths(policy), environ=environ)):
        return excluded("unproven_literal_read")
    if _EXCLUDED_COMMAND.search(assignment_text):
        return excluded("network_or_delete_mention")
    if registry_access(scanned):
        return excluded("registry")
    if re.search(r"(?:^|[\s\"'=])~[/\\]|\$(?:\{(?:env:)?(?:home|userprofile)\}|(?:env:)?(?:home|userprofile)\b)|%(?:home|userprofile)%", scanned, re.I):
        return excluded("home_spelling")  # do not mistake a home path for a task folder
    moved, unparsed_move = _move_edit_targets(command, tool == "PowerShell")
    targets = [*_shell_write_targets(command, powershell=tool == "PowerShell"), *moved]
    if re.search(r"\b(?:popd|pop-location)\b", command, re.I):
        return excluded("unresolved_directory_change")
    changes = list(re.finditer(r"\b(?:cd|chdir|pushd|set-location|sl|push-location)\b", command, re.I))
    changed_dir = bool(changes)
    bases = shell_cd_bases(command, workdir, environ=environ) if changed_dir else []
    # A shell can branch, change folders repeatedly, or compute the destination.
    # Check every known base; unknown bases remain a separate grant category.
    unknown_write = changed_dir and (bases is None or len(bases) != len(changes)
                                     or len(list(_CD.finditer(command))) != len(changes))
    if unknown_write:
        return excluded("unresolved_directory_change")
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
    # A write whose destination cannot be read (or a mover that was not parsed) is not refused here:
    # every check below still applies, and the command can then only be an unresolved path.
    unknown_write = unknown_write or unparsed_move
    if any(credential_path(path) for path in _candidate_paths(scanned)):
        return excluded("credential")
    closed = restricted_paths(policy)
    closed_hit = touches(checked, closed, workdir=workdir)
    resolved_closed = touches_resolved(checked, closed, workdir=workdir)
    if closed_hit or (resolved_closed and not isinstance(resolved_closed, Unresolved)):
        return excluded("restricted_zone")

    # Internal-zone reads return early in the regular policy. Remove only those
    # zones for a second pass so scheduler and write checks still run.
    rest = policy.model_copy(update={"data_zones": [z for z in policy.data_zones if z.level != "internal"]})
    if _evaluate_tool(tool, checked, rest, roots, workdir, environ=environ).action != "allow":
        return excluded("other_policy_ask_or_deny")

    # A private parent of this workdir does not itself identify another person's
    # path. An explicitly named sibling and any protected child stay excluded.
    work = _norm(workdir)
    private = list(private_paths)
    # The own session's tool-results files are task-local for these checks; any other path in the
    # staff config folder is still judged as written.
    masked = _mask_own_results(command, own_results)
    private_checked = checked if masked == command else {**checked, "command": masked}
    if masked != command:
        decision = evaluate_tool(tool, {**tool_input, "command": masked}, policy, allowed_roots=roots,
                                 workdir=workdir, private_paths=private_paths, private_enabled=private_enabled,
                                 private_open_reads=private_open_reads, read_only=read_only, environ=environ)
        if decision.action not in {"ask", "allow"}:
            return excluded("policy_" + decision.action)
    # The aggregate policy result can retain a different ask reason. Recheck
    # private evidence with the same staff environment, including parent paths.
    private_evidence = (_private_decision(tool, private_checked, private, workdir, None, environ,
                                         list(private_open_reads), closed)
                        if private_open_reads and (private_enabled or private) else None)
    if private_evidence and private_evidence.private_kind == "matched":
        return excluded("other_private_or_registry")
    if touches(private_checked, private, workdir=workdir, exempt=work):
        return excluded("other_private_path")
    protected = [p for p in private if not _inside(work, _norm(p))]
    resolved_private = touches_resolved(private_checked, private, workdir=workdir, exempt=_real(workdir))
    if resolved_private:
        # touches_resolved is Unresolved only when it stopped at MAX_RESOLVED_CANDIDATES: the operands it never
        # resolved may still reach a private directory through a link, so a request grant must not cover it.
        return excluded("private_scan_capped" if isinstance(resolved_private, Unresolved)
                        else "resolved_private_path")
    other = _private_decision(tool, private_checked, protected, workdir, None, environ,
                              list(private_open_reads), closed)
    if other and other.private_kind != "unresolved":
        return excluded("other_private_or_registry")
    # Check every known cd base as well: a relative operand after cd may name
    # an internal file even when it does not do so relative to the workdir.
    internal_hits = [_zone_hit(checked, policy, {"internal"}, workdir, own_workdir=True)]
    internal_hits.extend(_zone_hit(checked, policy, {"internal"}, base,
                                   own_workdir=_inside(_norm(base), work))
                         for base in (bases or []))
    internal = next((hit for hit in internal_hits if hit and not isinstance(hit, Unresolved)), None)
    if internal:
        # Never infer a read from an empty list of known write targets. Require
        # the complete command to pass the supported literal read proof above.
        return "internal_read" if literal_read else excluded("unproven_internal_read")
    internal = next((hit for hit in internal_hits if isinstance(hit, Unresolved)), None)
    if (unknown_write or isinstance(internal, Unresolved) or isinstance(resolved_closed, Unresolved)
            or decision.private_kind in {"resolved_match", "unresolved"}
            or (other and other.private_kind == "unresolved")
            or re.search(r"\$|`|\*|\?|\b(?:for|while)\b", command, re.I)):
        return "unresolved_path"
    return "workspace"
