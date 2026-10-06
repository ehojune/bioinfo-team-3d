"""Detect package installs that could mutate a request's shared environment."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterator


_WORD = re.compile(r'''"[^"]*"|'[^']*'|[^\s]+''')
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?|pythonw(?:\d+(?:\.\d+)*)?|py", re.IGNORECASE)
_PIP = re.compile(r"pip(?:\d+(?:\.\d+)*)?", re.IGNORECASE)
_MANAGERS = frozenset({"uv", "conda", "mamba", "micromamba"})
_R = frozenset({"r", "rscript"})
_INSTALL_EXECUTABLES = (
    "python", "python3", "python3.12", "pythonw", "py",
    "pip", "pip3", "pip3.12", "uv", "conda", "mamba", "micromamba", "r", "rscript",
)
_PIP_TARGET = re.compile(r"(?:--target(?:=|\s+)|-t\s+)(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.IGNORECASE)
_R_CALL = re.compile(r"\b(?:install\.packages|BiocManager::install|remotes::install_[A-Za-z0-9_.]+)\s*\(",
                     re.IGNORECASE)
_R_CALL_LIBRARY = re.compile(r"\blib\s*=\s*(\"[^\"]+\"|'[^']+'|[^\s,;)]+)", re.IGNORECASE)
_R_ENV_LIBRARY = re.compile(
    r"(?:\bR_LIBS_USER\b\s*=|\$env:R_LIBS_USER\s*=)\s*(\"[^\"]+\"|'[^']+'|[^\s,;)]+)",
    re.IGNORECASE,
)
_R_ENV_ONLY = re.compile(
    r"\s*(?:\$env:)?R_LIBS_USER\s*=\s*(?:\"[^\"]+\"|'[^']+'|[^\s,;)]+)\s*", re.IGNORECASE)


def executable_basename(token: str) -> str:
    """Normalize an executable token across POSIX and Windows spellings."""
    value = token.strip().strip("\"'")
    name = re.split(r"[\\/]", value)[-1].casefold()
    return name[:-4] if name.endswith(".exe") else name


def _words(segment: str) -> list[str]:
    return [match.group().strip("\"'") for match in _WORD.finditer(segment)]


def _executable_words(segment: str) -> tuple[str, list[str]]:
    words = _words(segment)
    while words and (words[0] == "&" or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
        words.pop(0)
    return (executable_basename(words[0]), words[1:]) if words else ("", [])


def _segments(command: str) -> Iterator[str]:
    """Split shell commands at control operators outside quoted strings."""
    start = 0
    quote = ""
    escaped = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if quote and char in {"\\", "`"}:
            escaped = True
            i += 1
            continue
        if char in "\"'":
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            i += 1
            continue
        separator = ""
        if not quote:
            if command.startswith(("&&", "||"), i):
                separator = command[i:i + 2]
            elif char in ";|\r\n":
                separator = char
        if separator:
            yield command[start:i]
            i += len(separator)
            start = i
        else:
            i += 1
    yield command[start:]


def _has_install(args: list[str]) -> bool:
    return any(arg.casefold() == "install" for arg in args)


def _python_install(executable: str, args: list[str]) -> bool:
    if _PIP.fullmatch(executable):
        return _has_install(args)
    if _PYTHON.fullmatch(executable):
        folded = [arg.casefold() for arg in args]
        return any(arg == "-m" and i + 1 < len(folded) and _PIP.fullmatch(folded[i + 1])
                   and _has_install(folded[i + 2:]) for i, arg in enumerate(folded))
    if executable == "uv":
        folded = [arg.casefold() for arg in args]
        return "pip" in folded and _has_install(folded[folded.index("pip") + 1:])
    return executable in _MANAGERS and _has_install(args)


def _r_calls(segment: str) -> Iterator[str]:
    for match in _R_CALL.finditer(segment):
        depth = 1
        quote = ""
        escaped = False
        i = match.end()
        while i < len(segment) and depth:
            char = segment[i]
            if escaped:
                escaped = False
            elif quote and char == "\\":
                escaped = True
            elif char in "\"'":
                if not quote:
                    quote = char
                elif quote == char:
                    quote = ""
            elif not quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
            i += 1
        yield segment[match.start():i]


def _task_library(path: str, name: str, workdir: str) -> bool:
    value = path.strip().strip("\"'")
    if any(mark in value for mark in ("$", "%", "`")):
        return False
    normalized = value.replace("\\", "/").rstrip("/")
    if normalized in {name, f"./{name}"}:
        return True
    try:
        return Path(value).is_absolute() and Path(value).resolve() == (Path(workdir) / name).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def installation_capable_shell_rule(rule: str) -> bool:
    """Whether a Claude shell allow-rule can start an installer without reaching the gate."""
    tool, separator, body = rule.partition("(")
    if tool.strip() not in {"Bash", "PowerShell"}:
        return False
    if not separator:
        return True  # bare shell access covers every installer
    body = body[:-1] if body.endswith(")") else body
    executable, _ = _executable_words(body)
    if executable in _R or executable in _MANAGERS or _PYTHON.fullmatch(executable) or _PIP.fullmatch(executable):
        return True
    return any(mark in executable for mark in "*?") and any(
        fnmatchcase(candidate, executable) for candidate in _INSTALL_EXECUTABLES)


def shared_environment_install_denial(tool_name: str, tool_input: dict, *, protected: bool,
                                      environment_step: bool, workdir: str) -> str | None:
    """Reject shared installs while allowing the environment owner and task-local libraries."""
    if not protected or environment_step or tool_name not in {"Bash", "PowerShell"}:
        return None
    pending_r_environment: list[str] = []
    for raw_segment in _segments(str(tool_input.get("command") or "")):
        segment = raw_segment.strip()
        if not segment:
            continue
        executable, args = _executable_words(segment)
        if _python_install(executable, args):
            targets = _PIP_TARGET.findall(segment)
            if not targets or not all(_task_library(target, ".pylib", workdir) for target in targets):
                return ("Only the environment step may modify the shared Python environment. Install the extra "
                        "package in this step with `python -m pip install --target ./.pylib ...` and record "
                        "`python -m pip freeze --path ./.pylib` in outputs/env/<step>.txt.")
        calls = list(_r_calls(segment)) if executable in _R else []
        current_environment = _R_ENV_LIBRARY.findall(segment)
        if calls:
            environment = [*pending_r_environment, *current_environment]
            for call in calls:
                libraries = _R_CALL_LIBRARY.findall(call) or environment
                if not libraries or not all(_task_library(path, ".rlib", workdir) for path in libraries):
                    return ("Only the environment step may modify the shared R library. Install the extra package "
                            "with `lib='./.rlib'` (or task-local R_LIBS_USER) and record that library's package "
                            "table in outputs/env/<step>.txt.")
            pending_r_environment = []
        elif current_environment and _R_ENV_ONLY.fullmatch(segment):
            # PowerShell commonly assigns $env:R_LIBS_USER in the immediately preceding statement.
            pending_r_environment = current_environment
        else:
            pending_r_environment = []
    return None
