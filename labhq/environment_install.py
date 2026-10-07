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
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_POWERSHELLS = frozenset({"pwsh", "powershell"})
_NESTED_SHELL_DEPTH = 3


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
        if _dynamic_installer_syntax(word, words[i + 1:]):
            return True
        if _python_install(executable, words[i + 1:]):
            return True
        if executable in {"conda", "mamba", "micromamba"} and "create" in words[i + 1:]:
            return True
        if executable in _R and _R_CALL.search(segment):
            return True
    # An unknown executable/wrapper with an install operand cannot receive an unscoped exception.
    return _has_install(words) or not valid and bool(re.search(r"\binstall\b", segment, re.IGNORECASE))


def _nonliteral_word(word: str) -> bool:
    """Whether a shell may replace any part of a word before execution."""
    return "$" in word or "`" in word or bool(re.search(r"%[^%]+%", word))


def _first_operand(args: list[str]) -> str:
    return next((arg for arg in args if not arg.startswith("-")), "")


def _dynamic_installer_syntax(token: str, args: list[str]) -> bool:
    """Fail closed when an installer selector is computed instead of literal."""
    executable = executable_basename(token)
    folded = [arg.casefold() for arg in args]
    install_like = any(arg in {"install", "create"} for arg in folded)
    if _nonliteral_word(token):
        return install_like
    if _PIP.fullmatch(executable) or executable in {"conda", "mamba", "micromamba"}:
        return _nonliteral_word(_first_operand(args))
    if _PYTHON.fullmatch(executable):
        for i, arg in enumerate(folded):
            if arg != "-m":
                continue
            if i + 1 == len(args) or _nonliteral_word(args[i + 1]):
                return True
            if _PIP.fullmatch(executable_basename(args[i + 1])):
                return _nonliteral_word(_first_operand(args[i + 2:]))
        return False
    if executable == "uv":
        try:
            pip_index = next(i for i, arg in enumerate(folded) if arg == "pip")
        except StopIteration:
            return _nonliteral_word(_first_operand(args))
        return _nonliteral_word(_first_operand(args[pip_index + 1:]))
    if executable == "r":
        selectors = [arg for arg in args if not arg.startswith("-")][:2]
        return any(_nonliteral_word(arg) for arg in selectors)
    if executable == "rscript":
        for i, arg in enumerate(folded):
            if arg in {"-e", "--expression"}:
                return i + 1 == len(args) or _nonliteral_word(args[i + 1])
    return False


def _installer_name_visible(text: str) -> bool:
    names = r"python(?:\d+(?:\.\d+)*)?|pythonw(?:\d+(?:\.\d+)*)?|py|pip(?:\d+(?:\.\d+)*)?|uv|conda|mamba|micromamba|rscript|r"
    return bool(re.search(rf"(?<![A-Za-z0-9_])(?:{names})(?:\.exe)?(?![A-Za-z0-9_])", text, re.IGNORECASE))


def _nested_shell_body(executable: str, args: list[str], valid: bool) -> tuple[str, str, bool, bool] | None:
    """Return body, parser kind, extractability, and unconditional denial for nested shells."""
    option_index: int | None = None
    if executable in _POSIX_SHELLS:
        option_index = next((i for i, arg in enumerate(args)
                             if arg == "-c" or arg.startswith("-") and "c" in arg[1:]), None)
        nested_tool = "Bash"
        joins_remainder = False
    elif executable in _POWERSHELLS:
        encoded = next((i for i, arg in enumerate(args)
                        if arg.casefold() in {"-encodedcommand", "-enc", "-e"}), None)
        if encoded is not None:
            body = args[encoded + 1] if encoded + 1 < len(args) else ""
            return body, "PowerShell", False, True
        option_index = next((i for i, arg in enumerate(args)
                             if arg.casefold() in {"-c", "-command"}), None)
        nested_tool = "PowerShell"
        joins_remainder = True
    elif executable == "cmd":
        option_index = next((i for i, arg in enumerate(args) if arg.casefold() in {"/c", "/k"}), None)
        nested_tool = "Bash"
        joins_remainder = True
    else:
        return None
    if option_index is None:
        return None
    if option_index + 1 >= len(args):
        return "", nested_tool, False, False
    body = " ".join(args[option_index + 1:]) if joins_remainder else args[option_index + 1]
    return body, nested_tool, valid and not _nonliteral_word(body), False


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
    """Split shell commands and lexical command groups outside quoted strings."""
    start = 0
    quote = ""
    escaped = False
    groups: list[str] = []
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
            elif char in "({":
                prefix = command[start:i].strip().casefold()
                previous = command[i - 1] if i else ""
                if (not previous or previous not in "$<>") and (
                        not prefix or prefix.split()[-1] in _SHELL_PREFIXES | {"do"}):
                    if command[start:i].strip():
                        yield command[start:i]
                    yield char
                    groups.append(")" if char == "(" else "}")
                    i += 1
                    start = i
                    continue
            elif char in ")}" and groups and char == groups[-1]:
                if command[start:i].strip():
                    yield command[start:i]
                yield char
                groups.pop()
                i += 1
                start = i
                continue
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


def _r_cmd_install(executable: str, args: list[str]) -> list[str] | None:
    if executable == "r":
        for i in range(len(args) - 1):
            if [arg.casefold() for arg in args[i:i + 2]] == ["cmd", "install"]:
                return args[i + 2:]
    return None


def _r_cmd_libraries(args: list[str]) -> list[str] | None:
    """R INSTALL accepts -l LIB and --library=LIB, not attached/separated variants."""
    libraries: list[str] = []
    for i, arg in enumerate(args):
        if arg == "-l":
            if i + 1 == len(args) or args[i + 1].startswith("-"):
                return None
            libraries.append(args[i + 1])
        elif arg.startswith("--library="):
            libraries.append(arg[len("--library="):])
        elif arg.casefold().startswith("--library") or (arg.casefold().startswith("-l") and not arg.startswith("--")):
            return None  # R may ignore these unsupported flags and retain its default shared library.
    return libraries


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
    return _shared_environment_install_denial(
        tool_name, str(tool_input.get("command") or ""), environment_step=environment_step,
        workdir=workdir, cwd=Path(workdir).resolve(), depth=0)


def _shared_environment_install_denial(tool_name: str, command: str, *, environment_step: bool,
                                       workdir: str, cwd: Path | None, depth: int) -> str | None:
    cwd_stack: list[Path | None] = []
    group_stack: list[tuple[str, Path | None, int]] = []
    install_environment_override = False
    pending_r_environment: list[str] = []
    segments = list(_segments(command))
    control_flow = any(re.match(r"\s*[({]*\s*(?:if|elif|else|then|while|until|for|do|case|switch|foreach)\b", part)
                       for part in segments)
    branch_cwd = control_flow and any(
        executable_basename(word.strip("({)}")) in _LOCATION_COMMANDS for part in segments for word in _words(part))
    for raw_segment in segments:
        segment = raw_segment.strip()
        if not segment:
            continue
        if segment in {"(", "{"}:
            group_stack.append((")" if segment == "(" else "}", cwd, len(cwd_stack)))
            continue
        if segment in {")", "}"}:
            if not group_stack or group_stack[-1][0] != segment:
                if _installer_name_visible(command):
                    return _ENVIRONMENT_DENIAL
                continue
            _, cwd, stack_length = group_stack.pop()
            del cwd_stack[stack_length:]
            continue
        # A data command can still execute substitutions; literal single-quoted text remains data.
        if (_shell_expands(segment, tool_name) and
                re.search(r"\b(?:install|create)\b", segment, re.IGNORECASE)):
            return _ENVIRONMENT_DENIAL
        # PowerShell assignments and shell exports can redirect a later command in the same tool call.
        install_environment_override |= bool(_INSTALL_REDIRECT_ENV.search(segment))
        token, args, understood = _shell_invocation(segment)
        executable = executable_basename(token)
        if _dynamic_installer_syntax(token, args):
            return _ENVIRONMENT_DENIAL
        nested = _nested_shell_body(executable, args, understood)
        if nested is not None:
            body, nested_tool, extractable, unconditional = nested
            if unconditional:
                return _ENVIRONMENT_DENIAL
            if not extractable or depth >= _NESTED_SHELL_DEPTH:
                if _installer_name_visible(body or segment):
                    return _ENVIRONMENT_DENIAL
                continue
            denial = _shared_environment_install_denial(
                nested_tool, body, environment_step=environment_step, workdir=workdir, cwd=cwd, depth=depth + 1)
            if denial:
                return denial
            continue
        detected_install = _python_install(executable, args)
        r_cmd_args = _r_cmd_install(executable, args)
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
        if r_cmd_args is not None:
            libraries = _r_cmd_libraries(r_cmd_args)
            if environment_step:
                if not libraries or not all(_local_path(path, workdir, cwd) for path in libraries):
                    return _ENVIRONMENT_DENIAL
            elif not libraries or cwd is None or not all(_task_library(path, ".rlib", workdir, cwd) for path in libraries):
                return ("Only the environment step may modify the shared R library. Use "
                        "`R CMD INSTALL -l ./.rlib ...` and record that library's package table "
                        "in outputs/env/<step>.txt.")
            pending_r_environment = []
            continue
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
    if group_stack and _installer_name_visible(command):
        return _ENVIRONMENT_DENIAL
    return None
