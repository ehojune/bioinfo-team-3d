"""Detect package installs that could mutate a request's shared environment."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path, PureWindowsPath
from typing import Iterator


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
_ENVIRONMENT_DENIAL = (
    "The environment step may install only into its own workspace. Create a local .venv and use its "
    "interpreter explicitly, or use a workspace-local --target, conda --prefix, or uv pip --python path.")
_INSTALL_REDIRECT_ENV = re.compile(
    r"\b(?:PIP_(?:TARGET|PREFIX|ROOT|USER|PYTHON|CONFIG_FILE)|UV_(?:PROJECT_ENVIRONMENT|PYTHON|SYSTEM_PYTHON)|"
    r"CONDA_PREFIX|PYTHONHOME|PYTHONPATH|VIRTUAL_ENV)\s*=", re.IGNORECASE)
_SHELL_PREFIXES = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!"})
_LOCATION_COMMANDS = frozenset({"cd", "chdir", "set-location", "sl", "pushd", "push-location", "popd", "pop-location"})
_DATA_COMMANDS = frozenset({"echo", "printf", "write-output", "write-host", "cat", "get-content", "rg", "grep", "ls", "dir"})


def executable_basename(token: str) -> str:
    """Normalize an executable token across POSIX and Windows spellings."""
    value = token.strip().strip("\"'")
    name = re.split(r"[\\/]", value)[-1].casefold()
    return name[:-4] if name.endswith(".exe") else name


def _literal_words(segment: str) -> tuple[list[str], bool]:
    """Tokenize literal words, including quoted pieces within one executable/option."""
    words: list[str] = []
    token = ""
    quote = ""
    started = False
    escaped = False
    for char in segment:
        if escaped:
            token += char
            escaped = False
        elif quote and char == "`":
            escaped = True
        elif char in "\"'":
            if not quote:
                quote = char
                started = True
            elif quote == char:
                quote = ""
            else:
                token += char
        elif char.isspace() and not quote:
            if started:
                words.append(token)
                token, started = "", False
        else:
            token += char
            started = True
    if started:
        words.append(token)
    return words, not quote and not escaped


def _words(segment: str) -> list[str]:
    return _literal_words(segment)[0]


def _executable_words(segment: str) -> tuple[str, list[str]]:
    words = _words(segment)
    while words and (words[0] == "&" or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
        words.pop(0)
    return (executable_basename(words[0]), words[1:]) if words else ("", [])


def _shell_invocation(segment: str) -> tuple[str, list[str], bool]:
    """Unwrap a bounded shell grammar; unknown wrapper options remain untrusted."""
    words, valid = _literal_words(segment.strip().lstrip("({ ").rstrip(")} "))
    while words:
        token = words[0].casefold()
        if token in _SHELL_PREFIXES or token in {"&", "."} or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
            words.pop(0)
            continue
        if token not in {"command", "env", "exec"}:
            break
        words.pop(0)
        while words and words[0].startswith("-"):
            option = words.pop(0)
            if option == "--":
                break
            if token == "command" and option == "-p" or token == "env" and option in {"-i", "--ignore-environment"}:
                continue
            if token == "env" and option in {"-u", "--unset"} or token == "exec" and option == "-a":
                if not words:
                    return "", [], False
                words.pop(0)
                continue
            if token == "env" and option.startswith("--unset=") or token == "exec" and option in {"-c", "-l"}:
                continue
            return token, words, False
    return (words[0], words[1:], valid) if words else ("", [], valid)


def _installation_syntax(segment: str) -> bool:
    """Find direct installer operands even when a wrapper/control form was not understood."""
    words, valid = _literal_words(segment)
    if words and executable_basename(words[0]) in _DATA_COMMANDS:
        return False  # printed text and filename arguments are data, not shell invocations
    for i, word in enumerate(words):
        executable = executable_basename(word.strip("({)}"))
        if _python_install(executable, words[i + 1:]):
            return True
        if executable in {"conda", "mamba", "micromamba"} and "create" in words[i + 1:]:
            return True
        if executable in _R and _R_CALL.search(segment):
            return True
    # An unknown executable/wrapper with an install operand cannot receive an unscoped exception.
    return _has_install(words) or not valid and bool(re.search(r"\binstall\b", segment, re.IGNORECASE))


def _foreign_windows_path(value: str) -> bool:
    """A Windows drive/UNC must never become a relative child of a POSIX workspace."""
    windows = PureWindowsPath(value)
    return bool(windows.drive) and (not windows.is_absolute() or not Path(value).drive)


def _shell_expands(segment: str, tool_name: str) -> bool:
    """Identify execution substitutions outside literal single-quoted text."""
    quote = ""
    i = 0
    escape = "`" if tool_name == "PowerShell" else "\\"
    while i < len(segment):
        char = segment[i]
        if char == escape and quote != "'":
            i += 2
            continue
        if char in "\"'":
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
        elif quote != "'" and (
                segment.startswith(("$(", "<(", ">("), i) or char == "`" and tool_name == "Bash"):
            return True
        i += 1
    return False


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
            elif char in ";|\r\n" or (char == "&" and command[start:i].strip() and
                                       (i == 0 or command[i - 1] not in "><")):
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


def _task_library(path: str, name: str, workdir: str, cwd: Path | None = None) -> bool:
    value = path.strip().strip("\"'")
    if _foreign_windows_path(value) or any(mark in value for mark in ("$", "%", "`")):
        return False
    try:
        root = Path(workdir).resolve()
        candidate = Path(value.replace("\\", "/"))
        candidate = candidate if candidate.is_absolute() else (cwd or root) / candidate
        if candidate.drive.casefold() != root.drive.casefold():
            return False
        expected = root / name
        return candidate.resolve() == expected.resolve() and root in expected.resolve().parents
    except (OSError, RuntimeError, ValueError):
        return False


def _local_path(path: str, workdir: str, cwd: Path | None, *, interpreter: bool = False) -> Path | None:
    """Resolve a literal destination below the task root, including directory links."""
    value = path.strip().strip("\"'")
    if (not value or _foreign_windows_path(value) or
            any(mark in value for mark in ("$", "%", "`", "~", "*", "?", "[", "]", "(", ")"))):
        return None
    value = value.replace("\\", "/")
    try:
        root = Path(workdir).resolve()
        candidate = Path(value)
        if not candidate.is_absolute():
            if cwd is None:
                return None
            candidate = cwd / candidate
        if candidate.drive.casefold() != root.drive.casefold():
            return None
        # POSIX venv Python commonly links to the system interpreter. Resolve its environment directory,
        # not that final link; a linked .venv/bin or .venv itself must still stay below the task root.
        if (interpreter and _PYTHON.fullmatch(executable_basename(value)) and
                candidate.parent.name.casefold() in {"bin", "scripts"} and
                (candidate.parent.parent / "pyvenv.cfg").is_file()):
            resolved = candidate.parent.resolve() / candidate.name
        else:
            resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _option_paths(args: list[str], long: str, short: str | None = None, *,
                  short_equals: bool = False) -> list[str] | None:
    """Read every occurrence so a second conflicting destination cannot hide the first."""
    values: list[str] = []
    for i, arg in enumerate(args):
        folded = arg.casefold()
        if folded == long or short and folded == short:
            if i + 1 == len(args) or args[i + 1].startswith("-"):
                return None
            values.append(args[i + 1])
        elif folded.startswith(long + "="):
            values.append(arg[len(long) + 1:])
        elif short and folded.startswith(short) and len(arg) > len(short):
            value = arg[len(short):]
            values.append(value[1:] if short_equals and value.startswith("=") else value)
    return values


def _environment_python_allowed(token: str, executable: str, args: list[str], workdir: str,
                                cwd: Path | None) -> bool:
    if any(arg.casefold().split("=", 1)[0] in {"--user", "--system", "--root", "-n", "--name"}
           for arg in args):
        return False
    if executable in {"conda", "mamba", "micromamba"}:
        if any(arg.casefold().startswith("-n") and not arg.startswith("--") for arg in args):
            return False
        prefixes = _option_paths(args, "--prefix", "-p", short_equals=True)
        # --prefix is the supported conda destination, unlike pip's --prefix.
        return bool(prefixes) and all(_local_path(path, workdir, cwd) for path in prefixes)
    if any(arg.casefold().split("=", 1)[0] == "--prefix" for arg in args):
        return False
    directories = _option_paths(args, "--directory") if executable == "uv" else []
    if directories is None:
        return False
    for directory in directories:
        cwd = _local_path(directory, workdir, cwd)
        if cwd is None:
            return False
    targets = _option_paths(args, "--target", "-t", short_equals=executable == "uv")
    interpreters = _option_paths(args, "--python")
    if targets is None or interpreters is None:
        return False
    if targets and not all(_local_path(path, workdir, cwd) for path in targets):
        return False
    if interpreters and not all(
            ("/" in path or "\\" in path or path.startswith(".")) and
            _local_path(path, workdir, cwd, interpreter=True) for path in interpreters):
        return False
    if targets or interpreters:
        return True
    if executable == "uv":
        return False  # uv's automatic environment discovery can select a parent environment.
    return ("/" in token or "\\" in token) and bool(_local_path(token, workdir, cwd, interpreter=True))


def _changed_workdir(executable: str, args: list[str], cwd: Path | None) -> Path | None:
    if executable not in _LOCATION_COMMANDS - {"popd", "pop-location"}:
        return cwd
    paths = [arg for arg in args if arg.casefold() not in {"-literalpath", "-path", "/d", "--"}]
    if (len(paths) != 1 or _foreign_windows_path(paths[0]) or
            any(mark in paths[0] for mark in ("$", "%", "`", "~", "*", "?", "[", "]"))):
        return None
    try:
        candidate = Path(paths[0].replace("\\", "/"))
        return candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve() if cwd else None
    except (OSError, RuntimeError, ValueError):
        return None


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
    """Keep the environment owner's installs and consumers' extra libraries in their own workspace."""
    if not protected or tool_name not in {"Bash", "PowerShell"}:
        return None
    cwd: Path | None = Path(workdir).resolve()
    cwd_stack: list[Path | None] = []
    install_environment_override = False
    pending_r_environment: list[str] = []
    segments = list(_segments(str(tool_input.get("command") or "")))
    control_flow = any(re.match(r"\s*[({]*\s*(?:if|elif|else|then|while|until|for|do|case|switch|foreach)\b", part)
                       for part in segments)
    branch_cwd = control_flow and any(
        executable_basename(word.strip("({)}")) in _LOCATION_COMMANDS for part in segments for word in _words(part))
    for raw_segment in segments:
        segment = raw_segment.strip()
        if not segment:
            continue
        # A data command can still execute substitutions; literal single-quoted text remains data.
        if (_shell_expands(segment, tool_name) and
                re.search(r"\b(?:install|create)\b", segment, re.IGNORECASE)):
            return _ENVIRONMENT_DENIAL
        # PowerShell assignments and shell exports can redirect a later command in the same tool call.
        install_environment_override |= bool(_INSTALL_REDIRECT_ENV.search(segment))
        token, args, understood = _shell_invocation(segment)
        executable = executable_basename(token)
        detected_install = _python_install(executable, args)
        manager_create = environment_step and executable in {"conda", "mamba", "micromamba"} and "create" in args
        if ((not understood or not detected_install and not manager_create and executable not in _R) and
                not (understood and executable in _DATA_COMMANDS | _LOCATION_COMMANDS)):
            if _installation_syntax(segment):
                return _ENVIRONMENT_DENIAL
        if executable in {"pushd", "push-location"}:
            cwd_stack.append(cwd)
        if executable in {"popd", "pop-location"}:
            cwd = cwd_stack.pop() if cwd_stack else None
        else:
            cwd = _changed_workdir(executable, args, cwd)
        if branch_cwd:
            cwd = None  # branch/loop directory changes cannot be represented as one sequential cwd
        if detected_install or manager_create:
            if environment_step:
                if install_environment_override or not _environment_python_allowed(token, executable, args, workdir, cwd):
                    return _ENVIRONMENT_DENIAL
                continue
            targets = _PIP_TARGET.findall(segment)
            if not targets or cwd is None or not all(_task_library(target, ".pylib", workdir, cwd) for target in targets):
                return ("Only the environment step may modify the shared Python environment. Install the extra "
                        "package in this step with `python -m pip install --target ./.pylib ...` and record "
                        "`python -m pip freeze --path ./.pylib` in outputs/env/<step>.txt.")
        calls = list(_r_calls(segment)) if executable in _R else []
        current_environment = _R_ENV_LIBRARY.findall(segment)
        if calls:
            environment = [*pending_r_environment, *current_environment]
            for call in calls:
                explicit_libraries = _R_CALL_LIBRARY.findall(call)
                libraries = explicit_libraries or environment
                if environment_step:
                    if (explicit_libraries and any(path[0] not in "\"'" for path in explicit_libraries) or
                            not libraries or not all(_local_path(path, workdir, cwd) for path in libraries)):
                        return _ENVIRONMENT_DENIAL
                elif not libraries or cwd is None or not all(_task_library(path, ".rlib", workdir, cwd) for path in libraries):
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
