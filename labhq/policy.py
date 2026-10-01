"""Guardrails shared by the permission-prompt MCP tool, the HPC tool and the adapters.

Honest scope note: these rules stop *accidental* leaks and costly mistakes by well-behaved
agents. They are not a sandbox. The real guarantee for controlled-access data is procedural:
raw records are only touched inside HPC jobs whose outputs are aggregates.
"""

from __future__ import annotations

import os
import ntpath
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit

from .settings import SCHEDULER_JOB_COMMANDS, PolicySettings

READ_LIKE = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITE_LIKE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Lexical prompts for common PowerShell hazards; this is not a command sandbox.
POWERSHELL_ASK_PATTERNS = (
    # Remove-Item and all its aliases; PowerShell accepts any prefix of -Recurse (-r, -re, …) and unix-style -rf.
    r"\b(?:Remove-Item|rm|ri|del|erase|rd|rmdir)\b[^;|\n]*\s-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|rf|fr)\b",
    r"\b(?:Invoke-Expression|iex)\b[^;\n]*(?:Invoke-WebRequest|iwr|DownloadString|https?://)",
    r"\b(?:Invoke-WebRequest|iwr)\b[^|\n]*\|\s*(?:Invoke-Expression|iex)\b",
    r"\bSet-ExecutionPolicy\b", r"\bStart-Process\b[^;\n]*\s-Verb\s+RunAs\b",
    r"\bFormat-Volume\b", SCHEDULER_JOB_COMMANDS, r"\bsudo\b",
    r"\bgit\s+push\b[^;\n]*--force\b",
)

_SHELL_WORD = re.compile(r'''"[^"]*"|'[^']*'|[^\s|;&<>]+''')
_REDIRECT = re.compile(r'''>{1,2}\s*("[^"]*"|'[^']*'|[^\s|;&<>]+)''')


NULL_DEVICES = frozenset({"/dev/null", "nul", "nul:", "$null", "\\\\.\\nul", "//./nul"})


def _shell_write_targets(command: str) -> Iterator[str]:
    """Find obvious literal write destinations; expansions and aliases are not parsed.

    Null devices (`2>/dev/null`, `> $null`, `> NUL`) discard output and are not writes.
    """
    for match in _REDIRECT.finditer(command):
        target = match.group(1).strip("\"'")
        if target.casefold() not in NULL_DEVICES:
            yield target
    for segment in re.split(r"[|;&\n]", command):
        words = [m.group().strip("\"'") for m in _SHELL_WORD.finditer(segment)]
        if not words:
            continue
        name = words[0].casefold()
        if name not in {"set-content", "out-file", "add-content", "new-item",
                        "copy-item", "move-item", "cp", "mv"}:
            continue
        for flag in ("-literalpath", "-path", "-filepath", "-destination"):
            for i, word in enumerate(words[:-1]):
                if word.casefold() == flag and (flag == "-destination" or name not in {"copy-item", "move-item"}):
                    yield words[i + 1]
        if name in {"copy-item", "move-item", "cp", "mv"}:
            yield words[-1]
        elif len(words) > 1 and not words[1].startswith("-"):
            yield words[1]


@dataclass
class Decision:
    action: str  # allow | deny | ask
    reason: str = ""


def _norm(p: str) -> str:
    """Lexically normalize both POSIX and Windows paths, regardless of the host OS."""
    p = os.path.expandvars(os.path.expanduser(p))
    if re.match(r"^[A-Za-z]:[/\\]", p) or p.startswith(("\\\\", "//")):
        return ntpath.normpath(p).replace("\\", "/").casefold()
    normalized = posixpath.normpath(p.replace("\\", "/"))
    return normalized.casefold() if os.name == "nt" else normalized


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _absolute(p: str) -> bool:
    return p.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", p))


def _drive_relative(p: str) -> bool:
    """A drive-qualified path without a slash uses that drive's unknown current directory."""
    return bool(re.match(r"^[A-Za-z]:(?![/\\])", p))


def _candidate_paths(s: str) -> Iterator[str]:
    # Keep quoted and backslash-escaped whitespace intact; start embedded paths
    # only after explicit separators.
    for match in re.finditer(r'''"([^"]*)"|'([^']*)'|((?:\\[ \t]|[^\s'"`|;&<>])+)''', s):
        token = next((v for v in match.groups() if v is not None), "")
        if match.group(3) is not None:
            token = re.sub(r"\\([ \t])", r"\1", token)
        separator_pattern = r"[=<>(),]"
        for chunk in re.split(separator_pattern, token):
            while chunk:
                chunk = chunk.strip("[]{}")
                if not chunk:
                    break
                uri = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", chunk)
                if uri and len(uri.group(1)) > 1:
                    if uri.group(1).casefold() == "file":
                        # RFC 8089: parse the URI, keep only the path component (not ?query or #fragment),
                        # percent-decode it, and treat "localhost" or an empty authority as this machine.
                        parts = urlsplit(chunk)
                        host, path = parts.netloc, unquote(parts.path)
                        if re.fullmatch(r"[A-Za-z]:", host):  # file://C:/x (non-standard but seen)
                            host, path = "", host + path
                        elif host and host.casefold() != "localhost":
                            path = "//" + host + path  # a remote host is a UNC path
                        if re.match(r"^/[A-Za-z]:[/\\]", path):  # file:///C:/x → C:/x
                            path = path[1:]
                        if path:
                            yield path
                    break
                separator = next((i for i, char in enumerate(chunk)
                                  if char == ":" and not (i == 1 and chunk[0].isalpha())), None)
                if separator is None:
                    yield chunk
                    break
                if separator:
                    yield chunk[:separator]
                chunk = chunk[separator + 1:]


def restricted_paths(policy: PolicySettings) -> list[str]:
    return [_norm(z.path) for z in policy.data_zones if z.level == "restricted"]


def _strings(obj: Any, path_field: bool = False) -> Iterator[tuple[str, bool]]:
    if isinstance(obj, str):
        yield obj, path_field
    elif isinstance(obj, dict):
        for key, v in obj.items():
            is_path = isinstance(key, str) and (key in {"path", "paths", "directory", "cwd", "workdir"}
                                                or key.endswith("_path"))
            yield from _strings(v, path_field or is_path)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, path_field)


def _raw_spaced_zone(s: str, zone: str) -> bool:
    """Conservatively find a spaced absolute zone in shell text at explicit boundaries."""
    if " " not in zone:
        return False
    raw = re.sub(r"\\([ \t])", r"\1", s)
    if re.match(r"^[A-Za-z]:/|^//", zone):
        raw = raw.replace("\\", "/")
    if os.name == "nt" or re.match(r"^[A-Za-z]:/|^//", zone):
        raw = raw.casefold()
    start = 0
    while (start := raw.find(zone, start)) != -1:
        end = start + len(zone)
        before = start == 0 or raw[start - 1] in " \t\r\n'\"=:<>(),;|&" or raw[:start].endswith("file://")
        after = end == len(raw) or raw[end] in "/ \t\r\n'\"=:<>(),;|&"
        if before and after:
            return True
        start += 1
    return False


def mentions_zone(text: str, zones: Iterable[str]) -> bool:
    """The publish guard's view of `touches`: same candidates and lexical normalization, case-folded.

    Over-matching is safe when deciding what not to publish, so case is ignored on every host.
    """
    folded = [z.casefold().rstrip("/") or "/" for z in zones if z]
    for token in _candidate_paths(text):
        if _drive_relative(token):
            if any(re.match(r"^[a-z]:/", z) and token[0].casefold() == z[0] for z in folded):
                return True
            continue
        if _absolute(token) and any(_inside(_norm(token).casefold(), z) for z in folded):
            return True
    return any(_raw_spaced_zone(text.casefold(), z) for z in folded)


def touches(obj: Any, paths: Iterable[str], workdir: str | None = None) -> str | None:
    """Find lexical path references, preserving structured, quoted and escaped spaces.

    Drive-relative paths on a restricted zone's drive are treated as touching it,
    since that drive's current directory is unknown. Unquoted shell text also
    gets a boundary check for zones with spaces. This is not a shell parser:
    paths concatenated without a recognized separator, or produced by variables,
    globs, substitutions, symlinks or other runtime expansion may be missed.
    """
    paths = [(p, _norm(p)) for p in paths if p]
    for s, path_field in _strings(obj):
        for token in (s,) if path_field else _candidate_paths(s):
            if _drive_relative(token):
                for original, zone in paths:
                    if re.match(r"^[A-Za-z]:/", zone) and token[0].casefold() == zone[0].casefold():
                        return original
                continue
            if not _absolute(token) and workdir:
                token = _norm(posixpath.join(_norm(workdir), token))
            else:
                token = _norm(token)
            for original, zone in paths:
                if _inside(token, zone):
                    return original
        if not path_field:
            for original, zone in paths:
                if _raw_spaced_zone(s, zone):
                    return original
    return None


MAX_RESOLVED_CANDIDATES = 256


def _real(p: str) -> str | None:
    try:
        return _norm(os.path.realpath(os.path.expandvars(os.path.expanduser(p))))
    except (OSError, ValueError):
        return None


def touches_resolved(obj: Any, paths: Iterable[str], workdir: str | None = None) -> str | None:
    """`touches` on this host's real filesystem: symlinks and junctions inside a candidate are followed.

    `reference/link/raw.tsv` names no zone, yet reads one when `link` points there. This only means something
    where the files are (the runner's approval gate) and only for calls the gate sees. Structured path fields
    are checked first, then up to MAX_RESOLVED_CANDIDATES other candidates; the lexical check still covers the rest.
    """
    zones = [(p, zone) for p in paths if p for zone in {_norm(p), _real(p)} if zone]
    if not zones:
        return None
    strings = sorted(_strings(obj), key=lambda item: not item[1])  # path fields first, e.g. Write before content
    checked = 0
    for s, path_field in strings:
        for token in (s,) if path_field else _candidate_paths(s):
            if not token or _drive_relative(token):
                continue
            if not _absolute(token):
                if not workdir:
                    continue
                token = os.path.join(workdir, token)
            checked += 1
            if checked > MAX_RESOLVED_CANDIDATES:
                return None
            real = _real(token)
            for original, zone in zones:
                if real and _inside(real, zone):
                    return original
    return None


def claude_rule_path(p: str) -> str:
    """Path as Claude Code matches it in permission rules.

    Claude normalises Windows paths to POSIX form before matching (`C:\\Users\\x` → `/c/Users/x`).
    Verified on Claude 2.1.282 / Windows 11: only `Read(//c/Users/x/**)` blocked the read;
    `Read(/C:\\Users\\x/**)` and `Read(//C:/Users/x/**)` did not (tests/fixtures/real/claude_code).
    """
    if p.startswith(("\\\\", "//")):
        raise ValueError("Claude UNC deny syntax is unverified; runner must reject this zone")
    s = p.replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):(?:/(.*))?$", s)
    if m:
        return f"/{m.group(1).lower()}" + (f"/{m.group(2)}" if m.group(2) else "")
    return s


def claude_settings(policy: PolicySettings) -> dict:
    """Claude Code settings: deny file tools on restricted zones.

    Absolute paths in permission rules take a leading `//` (e.g. `Read(//data/cohort/**)`).
    """
    deny: list[str] = []
    for p in restricted_paths(policy):
        for tool in ("Read", "Edit", "Write"):
            deny.append(f"{tool}(/{claude_rule_path(p)}/**)")
    return {"permissions": {"deny": deny}} if deny else {}


def claude_read_only(settings: dict, directories: Iterable[str]) -> dict:
    """Add Edit/Write deny rules for directories a task may only read (reference paths, #36)."""
    rules = [f"{tool}(/{claude_rule_path(d)}/**)" for d in directories for tool in ("Edit", "Write")]
    if not rules:
        return settings
    permissions = dict(settings.get("permissions") or {})
    permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []), *rules]))
    return {**settings, "permissions": permissions}


def evaluate_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: PolicySettings,
    allowed_roots: Iterable[str] = (),
    workdir: str | None = None,
) -> Decision:
    if tool_name in WRITE_LIKE:
        write_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if write_path and _drive_relative(write_path):
            return Decision("ask", f"drive-relative write path has no known base: {write_path}")
    rp = restricted_paths(policy)
    # Lexical first, then the real path: a link below an allowed folder can lead into a zone (#36).
    hit = touches(tool_input, rp, workdir=workdir) or touches_resolved(
        tool_input, [z.path for z in policy.data_zones if z.level == "restricted"], workdir=workdir)

    if hit and tool_name in READ_LIKE | WRITE_LIKE:
        return Decision(
            "deny",
            f"'{hit}' is a restricted data zone. Do not read raw records into the conversation; "
            "submit an HPC job (hpc_submit) that writes aggregate/QC summaries and read those instead.",
        )

    if tool_name in {"Bash", "PowerShell"}:
        cmd = str(tool_input.get("command", ""))
        if hit:
            return Decision("ask", f"{tool_name} touches restricted zone {hit}: `{cmd[:200]}`")
        patterns = (policy.approvals.bash_ask_patterns if tool_name == "Bash"
                    else POWERSHELL_ASK_PATTERNS)
        for pat in patterns:
            if re.search(pat, cmd, re.IGNORECASE):
                return Decision("ask", f"risky command (/{pat}/): `{cmd[:200]}`")
        roots = [_norm(r) for r in allowed_roots if r]
        for target in _shell_write_targets(cmd):
            if _drive_relative(target):
                return Decision("ask", f"drive-relative shell write destination: {target}")
            if _absolute(target) and not any(_inside(_norm(target), root) for root in roots):
                return Decision("ask", f"shell write outside allowed roots: {target}")
        return Decision("allow")

    if tool_name.startswith("mcp__"):
        if hit:
            return Decision("ask", f"MCP tool {tool_name} references restricted zone {hit}")
        return Decision("allow")

    roots = [_norm(r) for r in allowed_roots if r]
    if tool_name in WRITE_LIKE:
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if fp:
            if not _absolute(fp):
                if not workdir:
                    return Decision("ask", f"write path has no known workdir: {fp}")
                fp = posixpath.join(_norm(workdir), fp)
            if roots and not any(_inside(_norm(fp), r) for r in roots):
                return Decision("ask", f"write outside workspace/project dirs: {fp}")

    return Decision("allow")


def walltime_hours(walltime: str) -> float:
    """'HH:MM:SS', 'HH:MM', 'D-HH:MM:SS' or plain hours → hours."""
    w = walltime.strip()
    days = 0.0
    if "-" in w:
        d, w = w.split("-", 1)
        days = float(d)
    parts = [float(x) for x in w.split(":")]
    if len(parts) == 1:
        h = parts[0]
    elif len(parts) == 2:
        h = parts[0] + parts[1] / 60
    else:
        h = parts[0] + parts[1] / 60 + parts[2] / 3600
    return days * 24 + h


def core_hours(cores: int, walltime: str) -> float:
    return max(1, int(cores)) * walltime_hours(walltime)


def hpc_needs_approval(cores: int, walltime: str, policy: PolicySettings) -> bool:
    thr = policy.approvals.hpc_core_hours_threshold
    return thr <= 0 or core_hours(cores, walltime) > thr
