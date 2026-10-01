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


@dataclass(frozen=True)
class _PathTextScan:
    candidates: tuple[str, ...]
    separator_starts: tuple[int, ...]
    drive_starts: tuple[int, ...]
    network_ranges: tuple[tuple[int, int], ...]


_URI_PREFIX = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
_TOKEN_BREAKS = frozenset(" \t\r\n'\"`|;&<>")
_CHUNK_BREAKS = frozenset("=<>(),")
_SCHEME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+.-")


def _file_uri_path(uri: str) -> str | None:
    """Return the local/UNC path named by a file URI."""
    parts = urlsplit(uri)
    host, path = parts.netloc, unquote(parts.path)
    if re.fullmatch(r"[A-Za-z]:", host):  # file://C:/x (non-standard but seen)
        host, path = "", host + path
    elif host and host.casefold() != "localhost":
        path = "//" + host + path  # a remote host is a UNC path
    if re.match(r"^/[A-Za-z]:[/\\]", path):  # file:///C:/x → C:/x
        path = path[1:]
    return path or None


def _token_candidates(token: str) -> Iterator[str]:
    """Split one lexical token without retrying at every delimiter in a run."""
    chunk_start = 0
    length = len(token)
    while chunk_start < length:
        while chunk_start < length and token[chunk_start] in _CHUNK_BREAKS:
            chunk_start += 1
        if chunk_start >= length:
            break
        chunk_end = chunk_start
        while chunk_end < length and token[chunk_end] not in _CHUNK_BREAKS:
            chunk_end += 1
        left, right = chunk_start, chunk_end
        while left < right and token[left] in "[]{}":
            left += 1
        while right > left and token[right - 1] in "[]{}":
            right -= 1
        part_start = left
        while part_start < right:
            while part_start < right and token[part_start] in "[]{}":
                part_start += 1
            if part_start >= right:
                break
            uri = _URI_PREFIX.match(token, part_start, right)
            if uri and len(uri.group(1)) > 1:
                if uri.group(1).casefold() == "file":
                    path = _file_uri_path(token[part_start:right])
                    if path:
                        yield path
                break
            colon = part_start
            while colon < right:
                if token[colon] == ":" and not (
                        colon == part_start + 1 and token[part_start].isalpha()):
                    break
                colon += 1
            if colon == right:
                yield token[part_start:right]
                break
            if colon > part_start:
                yield token[part_start:colon]
            part_start = colon + 1
            while part_start < right and token[part_start] == ":":
                part_start += 1
        chunk_start = chunk_end + 1


def _network_url_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Locate non-file URL tokens without retrying a greedy scheme at every character."""
    ranges: list[tuple[int, int]] = []
    search_from = 0
    while (slashes := text.find("://", search_from)) >= 0:
        start = slashes
        while start and text[start - 1] in _SCHEME_CHARS:
            start -= 1
        scheme = text[start:slashes]
        if (scheme and scheme[0].isalpha() and (start == 0 or text[start - 1] not in _SCHEME_CHARS)
                and scheme.casefold() != "file"):
            end = slashes + 3
            while end < len(text) and not text[end].isspace():
                end += 1
            ranges.append((start, end))
            search_from = end
        else:
            search_from = slashes + 3
    return tuple(ranges)


def _scan_path_text(s: str) -> _PathTextScan:
    """Scan path-like text in linear time; separator runs have one start only."""
    separator_starts: list[int] = []
    drive_starts: list[int] = []
    for i, char in enumerate(s):
        if char in "/\\" and (i == 0 or s[i - 1] not in "/\\"):
            separator_starts.append(i)
        if (char.isalpha() and i + 2 < len(s) and s[i + 1] == ":"
                and s[i + 2] in "/\\"):
            drive_starts.append(i)

    candidates: list[str] = []
    i = 0
    while i < len(s):
        char = s[i]
        if char in "'\"":
            end = s.find(char, i + 1)
            if end < 0:
                i += 1
                continue
            candidates.extend(_token_candidates(s[i + 1:end]))
            i = end + 1
            continue
        if char in _TOKEN_BREAKS:
            i += 1
            continue
        start = i
        escaped: list[str] | None = None
        while i < len(s) and s[i] not in _TOKEN_BREAKS:
            if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in " \t":
                if escaped is None:
                    escaped = []
                escaped.append(s[start:i])
                escaped.append(s[i + 1])
                i += 2
                start = i
                continue
            i += 1
        if escaped is None:
            token = s[start:i]
        else:
            escaped.append(s[start:i])
            token = "".join(escaped)
        candidates.extend(_token_candidates(token))
    network_ranges = _network_url_ranges(s)
    return _PathTextScan(tuple(candidates), tuple(separator_starts), tuple(drive_starts), network_ranges)


def _candidate_paths(s: str) -> Iterator[str]:
    yield from _scan_path_text(s).candidates


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


def _scan_mentions_zone(text: str, zones: Iterable[str], scan: _PathTextScan) -> bool:
    folded = [z.casefold().rstrip("/") or "/" for z in zones if z]
    for token in scan.candidates:
        if _drive_relative(token):
            if any(re.match(r"^[a-z]:/", z) and token[0].casefold() == z[0] for z in folded):
                return True
            continue
        if _absolute(token) and any(_inside(_norm(token).casefold(), z) for z in folded):
            return True
    return any(_raw_spaced_zone(text.casefold(), z) for z in folded)


def mentions_zone(text: str, zones: Iterable[str]) -> bool:
    """The publish guard's view of `touches`: same candidates and lexical normalization, case-folded.

    Over-matching is safe when deciding what not to publish, so case is ignored on every host.
    """
    return _scan_mentions_zone(text, zones, _scan_path_text(text))


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
        for token in (s,) if path_field else _scan_path_text(s).candidates:
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


class Unresolved(str):
    """`touches_resolved` gave up before every candidate was resolved: unknown, not clear (#131).

    `path_field` says whether the cap was reached among structured path fields (always an access) or only
    in free text, which for file tools is content or a search pattern rather than a path being opened.
    """

    path_field: bool = False


def _real(p: str) -> str | None:
    try:
        return _norm(os.path.realpath(os.path.expandvars(os.path.expanduser(p))))
    except (OSError, ValueError):
        return None


def touches_resolved(obj: Any, paths: Iterable[str], workdir: str | None = None) -> str | None:
    """`touches` on this host's real filesystem: symlinks and junctions inside a candidate are followed.

    `reference/link/raw.tsv` names no zone, yet reads one when `link` points there. This only means something
    where the files are (the runner's approval gate) and only for calls the gate sees. Structured path fields
    are checked first, then distinct other candidates. Past MAX_RESOLVED_CANDIDATES it returns `Unresolved`
    rather than None: 257 decoy paths must not make the 258th, a link into a zone, pass (#131).
    """
    zones = [(p, zone) for p in paths if p for zone in {_norm(p), _real(p)} if zone]
    if not zones:
        return None
    strings = sorted(_strings(obj), key=lambda item: not item[1])  # path fields first, e.g. Write before content
    seen: set[str] = set()
    for s, path_field in strings:
        for token in (s,) if path_field else _scan_path_text(s).candidates:
            if not token or _drive_relative(token):
                continue
            if not _absolute(token):
                if not workdir:
                    continue
                token = os.path.join(workdir, token)
            if token in seen:
                continue  # a repeated path costs nothing more and proves nothing new
            seen.add(token)
            if len(seen) > MAX_RESOLVED_CANDIDATES:
                unresolved = Unresolved(f"more than {MAX_RESOLVED_CANDIDATES} path candidates")
                unresolved.path_field = path_field
                return unresolved
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


def claude_deny_links(settings: dict, links: Iterable[str]) -> dict:
    """Read/Edit/Write deny rules for link paths inside an open folder that lead into a zone (#132).

    Claude compares rules with the path as written, so the link itself (a file) and anything below it (a
    directory) are both named; the zone rules only cover the zone's own spelling.
    """
    rules = [f"{tool}(/{claude_rule_path(link)}{tail})" for link in links
             for tool in ("Read", "Edit", "Write") for tail in ("", "/**")]
    if not rules:
        return settings
    permissions = dict(settings.get("permissions") or {})
    permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []), *rules]))
    return {**settings, "permissions": permissions}


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
    if isinstance(hit, Unresolved):
        # Free text opens paths only in a shell command, an MCP call or a Glob pattern; elsewhere it is file
        # content, a search pattern or a prompt, and the structured path fields were all resolved.
        if not (hit.path_field or tool_name in {"Bash", "PowerShell", "Glob"} or tool_name.startswith("mcp__")):
            hit = None
        else:
            shown = str(tool_input.get("command") or tool_input)[:200]
            return Decision("ask", f"{tool_name} names {hit}; links among them were not all resolved, so a "
                                   f"restricted zone may be reached: `{shown}`")

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
