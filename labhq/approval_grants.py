"""Request-scoped approval classification.

Only the runner may supply the policy context used here. A classification is
advisory until the gateway records a person's explicit grant for the request.
"""

from __future__ import annotations

import copy
import os
import asyncio
from weakref import WeakValueDictionary
import re
import posixpath
from typing import Any

from .models import RunnerUnavailable
from .request_status import is_active_request
from .policy import (SHELL_TOOLS, Unresolved, _absolute, _candidate_paths, _evaluate_tool,
                     _blank_non_syntax, _inside, _norm, _private_decision, _real, _word_value, _zone_hit, evaluate_tool,
                     _shell_write_targets, git_bash_command, restricted_paths, shell_segments, touches,
                     touches_resolved)
from .private_paths import (_CD, claude_project_slug, credential_path, open_read_allowed, registry_access,
                            shell_cd_bases)
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
            files = _perl_in_place_files(words[1:])
            unknown = unknown or files is None
            targets.extend(files or [])
    # A word the shell joins from quoted pieces (`"/tmp"/out`) is not decoded here: not read either.
    unknown = unknown or any(re.search(r"[\"']" if powershell else r"[\"'\\]", target) for target in targets)
    # Every mention must be a parsed mover command (`mv a b; xargs mv` leaves one unparsed).
    return targets, unknown or mentioned > parsed


def _plain_mover_option(word: str, powershell: bool) -> bool:
    """An option word that names no path itself; its value, if any, is the next word."""
    if powershell:
        return bool(re.fullmatch(r"-[A-Za-z]+", word))  # parameter names; `-Name:value` is not plain
    return (bool(re.fullmatch(r"-[bfinuvTZt]+", word)) and ("t" not in word or word.endswith("t"))
            or word in {"--", "--force", "--interactive", "--no-clobber", "--verbose", "--update", "--backup",
                        "--no-target-directory", "--strip-trailing-slashes", "--target-directory"})


def _perl_in_place_files(words: list[str]) -> list[str] | None:
    """File operands of `perl -i` (`-pi -e CODE FILE...`, `-i.bak SCRIPT FILE...`); [] without -i.

    Only switches that take no value, `-i[extension]` and a separate `-e CODE` are read. Any other
    option shape (`-e'code'`, `-I dir`, `-Mmod`) could shift which word is a file: None, unresolved."""
    in_place = code_given = skip = ended = unknown = False
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
                extension = letters[letters.index("i") + 1:]
                letters = letters[:letters.index("i")]  # the rest is the backup extension
                unknown = unknown or not re.fullmatch(r"[\w.~-]*", extension)
            if not re.fullmatch(r"[plnaswtTWX]*[eE]?", letters):
                unknown = True
            elif letters.endswith(("e", "E")):
                code_given = skip = True
            continue
        operands.append(word)
    if not in_place:
        # An unread option may have taken a value (`-I lib`), hiding a later `-i` among the operands.
        return None if unknown and any(re.match(r"-\w*i", word) for word in words) else []
    return None if unknown else (operands if code_given else operands[1:])


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


# Plain file commands with no interpreter: every operand they touch is visible on the command line.
_FILE_COMMANDS = {"cd", "pwd", "ls", "cat", "head", "tail", "grep", "wc", "sort", "uniq", "cut", "echo", "printf",
                  "mkdir", "touch", "cp", "mv", "sed", "tee", "true"}
_POWERSHELL_FILE_COMMANDS = {"new-item", "ni", "set-content", "add-content", "out-file", "copy-item", "move-item",
                             "get-content", "get-childitem", "test-path", "mkdir", "md"}
_REDIRECTS = {">", ">>", "<", "2>", "2>>", "&>", "1>"}


def _plain_sed(args: list[str]) -> bool:
    """A sed call whose script is one substitution with safe flags or a line print: no e, r, w or other file names.

    The first non-option word, or each `-e` value, is the script; the rest are input files."""
    scripts, files, expect = [], [], False
    for word in args:
        if expect:
            scripts.append(word)
            expect = False
        elif word in {"-e", "--expression"}:
            expect = True
        elif word.startswith("-") and len(word) > 1:
            continue
        else:
            files.append(word)
    if not scripts and files:
        scripts.append(files.pop(0))
    substitution = r"s([^\w\s\\])(?:(?!\1)[^\\]|\\.)*\1(?:(?!\1)[^\\]|\\.)*\1[gIp0-9]*"
    return bool(scripts) and all(re.fullmatch(substitution, script) or re.fullmatch(r"[0-9]+(?:,[0-9]+)?p", script)
                                 for script in scripts)


def _task_local(command: str, parts: list[list[str]] | None, bases: list[str], workdir: str,
                powershell: bool = False) -> bool:
    """Whether a plain file command only names paths that really resolve inside the task folder."""
    root = _real(workdir)
    names = _POWERSHELL_FILE_COMMANDS if powershell else _FILE_COMMANDS
    if not root or parts is None or not all(words and words[0].casefold() in names for words in parts):
        return False
    operands = []
    for words in parts:
        for word in words[1:]:
            if word in _REDIRECTS:
                continue
            if powershell and re.search(r"[,@()]", word):
                return False  # a PowerShell array, splat or subexpression can name more paths
            if not (word.startswith("-") and len(word) > 1):
                operands.append(word)
            elif not powershell and word.startswith("--"):
                # A long option may name a file or a list of files (`--output=F`, `--files0-from=F`): never proven.
                return False
            elif not re.fullmatch(r"-[A-Za-z][A-Za-z-]*[0-9]*", word):
                return False  # an attached value (`-o/tmp/out`, `-i.bak`) is not read here
            elif words[0] == "sed" and "f" in word[1:]:
                return False  # a sed script file can write anywhere (`w FILE`)
        if words[0] == "sed" and not _plain_sed(words[1:]):
            return False
    for word in operands:
        for base in bases:
            path = word if _absolute(word) else posixpath.join(_norm(base), word)
            resolved = _real(path)
            if not resolved or not _inside(resolved, root):
                return False
    return True


def _inner_private_hit(command: str, targets: list[str], bases: list[str], work: str,
                       private: list[str], open_reads: tuple[str, ...]) -> bool:
    """A private item registered inside the task folder: written to, or read outside its open reads."""
    inner = [p for p in private if _inside(_norm(p), work) and _norm(p) != work]
    if not inner:
        return False
    zones = [(zone, [r for r in open_reads if _inside(_norm(r), _norm(p))])
             for p in inner for zone in {_norm(p), _real(p)} if zone]

    def spellings(word: str) -> list[tuple[str, str]]:
        found = []
        for base in bases:
            path = word if _absolute(word) else posixpath.join(_norm(base), word)
            found.extend((path, spelled) for spelled in (_norm(path), _real(path)) if spelled)
        return found

    for target in targets:
        if any(_inside(spelled, zone) for _, spelled in spellings(target) for zone, _ in zones):
            return True
    for token in set(_candidate_paths(command)):
        for path, spelled in spellings(token):
            for zone, reads in zones:
                if _inside(spelled, zone) and not open_read_allowed(path.replace("/", os.sep), reads):
                    return True
    return False


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
        # Whole operands only: a longer path that merely starts with this text keeps its own spelling.
        command = re.sub(r"(?<![^\s\"'=;|&<>()])" + re.escape(token) + r"(?![^\s\"';|&<>()])",
                         lambda _: "tool-results-" + posixpath.basename(norm), command)
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
        if re.search(r"[$`*?%\"']", target):  # unexpanded or partly quoted: not read here
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
    # The exemptions above cover the whole task folder. A private item registered inside it (inputs/)
    # stays individual: any write there, and any read outside the open reads below it.
    if _inner_private_hit(masked, targets, [workdir, *(bases or [])], work, private, private_open_reads):
        return excluded("other_private_path")
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
    # The private-path evidence may be unresolved or a resolved match only because the task folder lies below
    # a private parent (the runs root). A plain file command whose every path really resolves inside the task
    # folder is task-local; the policy decision itself is unchanged and still asks.
    # Judged on the masked text: the own session's tool-results file counts as task-local (r20).
    local = _task_local(masked, literal_commands(masked, powershell=tool == "PowerShell")[0],
                        [workdir, *(bases or [])], workdir, powershell=tool == "PowerShell")
    if (unknown_write or isinstance(internal, Unresolved) or isinstance(resolved_closed, Unresolved)
            or (decision.private_kind in {"resolved_match", "unresolved"} and not local)
            or (other and other.private_kind == "unresolved" and not local)
            or re.search(r"\$|`|\*|\?|\b(?:for|while)\b", command, re.I)):
        return "unresolved_path"
    return "workspace"


# Gateway grant state and durable delivery. Classification above is unchanged.
def automatic_audit(decision: dict, store) -> dict:
    """Reuse only the card already visible to the requester, including its command and reason."""
    if decision.get("automatic") is not True:
        return {}
    card = copy.deepcopy(decision.get("approval") or {})
    task = store.get("task", card.get("task_id")) or {}
    return {"approval": card, "step_id": task.get("step_id")}


def _grant_lock(self, rid: str):
    if not hasattr(self, "_grant_locks"):
        self._grant_locks = WeakValueDictionary()
    return self._grant_locks.setdefault(rid, asyncio.Lock())

def _grant_category(self, entry: dict) -> str | None:
    card = entry.get("approval") or {}
    detail = card.get("detail") or {}
    rid, tid, origin = card.get("request_id"), card.get("task_id"), entry.get("origin")
    category = detail.get("grant_category")
    request = self.requests.get(rid) or {}
    task = self.store.get("task", tid) if tid else None
    process = self.runner_processes.get(origin)
    if (card.get("kind") != "tool_permission" or category not in {"workspace", "unresolved_path", "internal_read"}
            or not origin or not process or detail.get("grant_process") != process
            or entry.get("origin_process") != process
            or self.runner_capabilities.get(origin, {}).get("approval_grants") != 1
            or not is_active_request(request.get("status")) or request.get("cancelling")
            or not task or task.get("request_id") != rid or task.get("runner_id") != origin
            or (task.get("payload") or {}).get("agent_id") != card.get("agent_id")
            or task.get("completed") or task.get("abandoned") or self.store.get("task_cancel", tid)):
        return None
    return category

def _grant_ready(self, rid: str, category: str) -> bool:
    return self._grant_key(rid, category) is not None

def _grant_key(self, rid: str, category: str) -> str | None:
    """The confirmed grant that covers `category`: its own, or for a proven task-folder write the broader
    unresolved-path grant (#1445). A task-folder grant never covers an unresolved path."""
    grants = self.store.get("approval_grants", rid) or {}
    for key in ((category, "unresolved_path") if category == "workspace" else (category,)):
        decision = self.store.get("approval_decision", grants[key]) if grants.get(key) else None
        if decision and decision.get("approved") and not decision.get("grant_pending"):
            return key
    return None

def _confirmed_decisions(store, rid: str) -> dict:
    return {aid: d for aid, d in store.all("approval_decision").items()
            if (d.get("approval") or {}).get("request_id") == rid
            and d.get("approved") is True and not d.get("grant_pending")}


def automatic_approvals(store, rid: str) -> dict:
    confirmed = _confirmed_decisions(store, rid)
    automatic = [(aid, decision) for aid, decision in confirmed.items() if decision.get("automatic") is True]
    automatic.sort(key=lambda row: (row[1].get("decided_at") or 0, row[0]), reverse=True)
    rows = []
    for aid, decision in automatic:
        card = decision["approval"]
        detail = card.get("detail") or {}
        tool_input = detail.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        task_id = card.get("task_id")
        task = (store.get("task", task_id) or {}) if task_id else {}
        rows.append({
            "approval_id": aid,
            "command": command if isinstance(command, str) else None,
            "agent_id": card.get("agent_id"),
            "step_id": task.get("step_id") if task.get("request_id") == rid else None,
            # The command's classification can differ from the grant that covered it.
            "category": detail.get("grant_category"),
        })
    return {"request_id": rid, "approvals": rows}


def approval_summary(self, rid: str) -> dict:
    confirmed = _confirmed_decisions(self.store, rid)
    granted = {d["grant_category"] for aid, d in confirmed.items()
               if d.get("automatic") is False and d.get("grant_source") == aid
               and isinstance(d.get("grant_category"), str)}
    request = self.store.get("request", rid) or {}
    active = []
    if is_active_request(request.get("status")) and not request.get("cancelling"):
        for category, source in (self.store.get("approval_grants", rid) or {}).items():
            decision = confirmed.get(source) or {}
            if (category in granted and decision.get("grant_category") == category
                    and decision.get("automatic") is False and decision.get("grant_source") == source):
                active.append(category)
    return {"request_id": rid,
            "automatic_approved_count": sum(d.get("automatic") is True for d in confirmed.values()),
            "granted_categories": sorted(granted), "active_grant_categories": sorted(active)}



async def commit_grant(self, approval_id, entry, category, decision, delivery, chosen,
                       grant_for_request, grant_auto, approved, note):
    rid = entry["approval"]["request_id"]
    async with self._grant_lock(rid):
        if self._grant_category(entry) != category:
            raise ValueError("request or runner is no longer eligible")
        key = category if grant_for_request else (self._grant_key(rid, category) or category)
        source = await self.store.acommit_grant_approval(
            approval_id, decision, delivery, rid, key, create=grant_for_request)
        chosen.update(grant_category=key, grant_source=source, automatic=grant_auto)
        eligible = self._grant_category(entry) == category
        if eligible:
            try:
                await self.store.afinalize_grant_approval(approval_id, rid)
            except ValueError:
                eligible = False
        if not eligible or self._grant_category(entry) != category:
            # Cancel may have marked the request while the durable writer was busy.
            # Do not release the just-recorded yes to its runner.
            approved, note = False, "request ended"
            await self.store.areject_grant_approval(approval_id,
                {**decision, **chosen, "approved": False, "note": note},
                {**delivery, "approved": False, "note": note},
                rid, category, revoke=grant_for_request)
        self.approvals.pop(approval_id, None)
        if entry["origin"] in self.runners:
            try:
                sent_approved = await self._send_decision(entry["origin"], approval_id)
                if approved and sent_approved is False:
                    approved, note = False, "request ended"
                    await self.store.areject_grant_approval(approval_id,
                        {**decision, **chosen, "approved": False, "note": note},
                        {**delivery, "approved": False, "note": note},
                        rid, category, revoke=grant_for_request)
            except RunnerUnavailable:
                pass
    return approved, note


# trusted_local (#1482): a card is allowed unless one of these named reasons applies. Internal-zone reads keep
# their once-per-request card. Commands the classification cannot settle are allowed when no reason applies.
_TRUSTED_DENY = {
    "credential": "credential 경로", "restricted_zone": "제한 구역", "registry": "개인 설정(레지스트리)",
    "home_spelling": "홈 폴더 경로", "other_private_path": "개인 경로", "other_private_or_registry": "개인 경로",
    "resolved_private_path": "링크로 이어진 개인 경로", "private_scan_capped": "개인 경로를 다 확인하지 못함",
    "outside_write_target": "허용 폴더 밖 쓰기", "shell_startup_environment": "셸 시작 환경 변경",
}
_PATH_REASONS = {"credential", "other_private_path", "other_private_or_registry", "resolved_private_path",
                 "outside_write_target"}


def _path_evidence(command: str, tool: str, workdir: str, roots: list[str], kw: dict, reason: str) -> bool:
    """Whether a path-shaped word (a separator in it) backs a private, credential or outside-write reason."""
    bases = [workdir, *(shell_cd_bases(command, workdir, environ=kw.get("environ")) or [])]

    def located(word: str) -> list[str]:
        if not re.search(r"[/\\]", word) or ":" in re.sub(r"^[A-Za-z]:(?=[/\\])", "", word):
            return []  # a bare word, or a colon no file name has (`d:`, `x>1:` in inline code)
        return [p for p in ([_real(word)] if _absolute(word) else
                            [_real(posixpath.join(_norm(base), word)) for base in bases]) if p]

    if reason == "outside_write_target":
        powershell = tool == "PowerShell"
        targets = [*_shell_write_targets(command, powershell=powershell), *_move_edit_targets(command, powershell)[0]]
        return any(not any(_inside(path, _real(r) or _norm(r)) for r in roots)
                   for t in targets for path in located(t if re.search(r"[/\\]", t) else "./" + t))
    zones = [z for p in kw.get("private_paths") or () for z in {_norm(p), _real(p)} if z]
    if any(credential_path(t) or credential_path(path) or any(_inside(path, z) for z in zones)
           for t in _candidate_paths(command) for path in located(t)):
        return True
    # A bare credential name counts when that file exists (`cat .env`), not as a word in code (`token`).
    return reason == "credential" and any(
        credential_path(t) and os.path.lexists(posixpath.join(_norm(base), t))
        for t in _candidate_paths(command) for base in bases)


_DELETERS = {"rm", "rmdir", "unlink", "shred", "del", "erase", "rd", "ri", "remove-item"}
_OUTSIDE_WRITE = ("write outside", "shell write outside allowed roots", "write path has no known workdir",
                  "drive-relative", "the gate cannot expand")


def trusted_verdict(category: str | None, trace: list[str], tool: str, tool_input: dict[str, Any], policy: Any, *,
                    workdir: str, extra_dirs: tuple[str, ...] = (), policy_reason: str = "",
                    workspace_root: str = "", **kw: Any) -> tuple[str, str]:
    """("allow" | "deny" | "card", reason) for a tool card under trusted_local, from `classify`'s result.

    The workspace root and the task folders in it (`inputs/` links to earlier steps) are labhq's own data, not the
    PI's personal files: a private-path reason that only they explain is judged again without them."""
    from .settings import SCHEDULER_JOB_COMMANDS

    if category == "internal_read" or (trace and trace[0] == "unproven_internal_read"):
        return "card", ""
    if category:
        return "allow", ""
    private = tuple(kw.get("private_paths") or ())
    own = [p for p in private if workspace_root and _inside(_norm(p), _norm(workspace_root))]
    if own:
        kw = {**kw, "private_paths": tuple(p for p in private if p not in own)}
        if trace and trace[0] in _TRUSTED_DENY and trace[0] not in {"credential", "restricted_zone", "registry",
                                                                    "outside_write_target"}:
            trace = []
            category = classify(tool, tool_input, policy, workdir=workdir, extra_dirs=extra_dirs, trace=trace, **kw)
            return trusted_verdict(category, trace, tool, tool_input, policy, workdir=workdir, extra_dirs=extra_dirs,
                                   policy_reason=policy_reason, **kw)
    roots = [workdir, *extra_dirs]
    command = str((tool_input or {}).get("command") or "")
    if trace and trace[0] in _TRUSTED_DENY:
        if trace[0] not in _PATH_REASONS or _path_evidence(command, tool, workdir, roots, kw, trace[0]):
            return "deny", _TRUSTED_DENY[trace[0]]
        return "allow", ""  # only loose words in inline code (`d:`, `token`, `x>1:`) matched
    decision = evaluate_tool(tool, tool_input, policy, allowed_roots=roots, workdir=workdir, **kw)
    reason = decision.reason
    if decision.action == "allow":
        # The runner's policy allows it, so the card came from the disk check on a download.
        return "deny", "디스크 여유 부족: " + (policy_reason or "내려받을 공간이 모자랍니다")
    if decision.action == "deny":
        return "deny", reason
    if "is neither a shell, file nor MCP tool" in reason:
        return "card", ""  # a tool labhq does not check (Monitor runs its own commands) keeps its card
    if "internal data zone read requires approval" in reason:
        return "card", ""
    if decision.private_kind == "matched" and _path_evidence(command, tool, workdir, roots, kw, "other_private_path"):
        return "deny", "개인 경로"
    if "restricted zone" in reason:
        return "deny", "제한 구역"
    if "위험 명령 확인 필요" in reason:
        if re.search(SCHEDULER_JOB_COMMANDS, command, re.I):
            return "deny", "스케줄러 직접 호출: HPC 도구(hpc_submit)로 제출하세요"
        return "deny", "관리자 권한·위험 명령"
    if any(text in reason for text in _OUTSIDE_WRITE):
        return "deny", "허용 폴더 밖 쓰기"
    powershell = tool == "PowerShell"
    deletes = [word for words in shell_segments(command, powershell) for word in [_word_value(w) for w in words][1:]
               if words and _word_value(words[0]).casefold() in _DELETERS and not word.startswith("-")]
    if "재귀 삭제 확인 필요" in reason or deletes:
        # Every operand of a delete is resolved through links: `rm -rf link/file` follows `link` out of the folder.
        for token in (_candidate_paths(command) if "재귀 삭제 확인 필요" in reason else deletes):
            if ":" in re.sub(r"^[A-Za-z]:(?=[/\\])", "", token):
                continue  # not a file name (`d:` in inline code)
            path = token if _absolute(token) else posixpath.join(_norm(workdir), token)
            real = _real(path)
            if not real or not any(_inside(real, _real(root) or _norm(root)) for root in roots):
                return "deny", "허용 폴더 밖 삭제"
    return "allow", ""
