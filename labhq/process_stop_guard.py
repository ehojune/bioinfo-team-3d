"""Static name-based process-stop guard; never inspects or terminates a process."""

from __future__ import annotations

import re


DENIAL = (
    "이름으로 여러 프로세스를 종료하는 명령은 승인 카드 없이 거부합니다. "
    "자기 작업 폴더 경로가 명령줄에 있는지 확인한 PID만 지정해 종료하세요. "
    "PID와 시작 시각을 다시 확인하고 다른 요청이나 gateway·runner는 종료하지 마세요."
)

# Keep adjacent quoted/escaped fragments in one shell word. Redirections are
# distinct tokens so their targets are never mistaken for executed commands.
_REDIRECTION = re.compile(r"(?:\d*(?:<<<|<<-?|>>|<>|>&|<&|>\||[<>])|&>>?)")
_ANSI_QUOTE = re.compile(r"\$'(?:\\[\s\S]|[^'\\])*'")
_WORDS = re.compile(
    _REDIRECTION.pattern + r'|&&|\|\||[;&|(){}\n]|(?:' + _ANSI_QUOTE.pattern
    + r'''|"(?:`[\s\S]|\\[\s\S]|[^"\\`])*"|'''
    r"'[^']*'|\\[\s\S]|`[\s\S]|[^\s;&|(){}<>\"'\\`])+"
)
_SEPARATOR = re.compile(r"^[;&|(){}\n]+$")
_KNOWN_COMMANDS = {
    "pkill", "killall", "taskkill", "get-process", "gps", "ps", "stop-process", "spps", "kill",
    "sudo", "env", "command", "exec", "nohup", "builtin", "if", "elif", "while", "until", "time",
    "sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh", "xargs",
    "coproc", "eval", "trap", "timeout", "nice", "setsid", "find", "pgrep",
}
_XARGS_ARGUMENT_OPTIONS = {
    "--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars", "--process-slot-var",
}
_XARGS_OPTIONS = _XARGS_ARGUMENT_OPTIONS | {
    "--eof", "--replace", "--max-lines", "--null", "--open-tty", "--interactive",
    "--no-run-if-empty", "--verbose", "--exit", "--show-limits",
}

# sudo options with required operands; --preserve-env's optional value uses '='.
_SUDO_ARGUMENT_OPTIONS = {
    "--auth-type", "--close-from", "--login-class", "--chdir", "--group", "--host",
    "--other-user", "--prompt", "--chroot", "--role", "--command-timeout", "--type", "--user",
}

_EXEC_OPTIONS = {
    # long argument options, long switches, short argument flags, short switches
    "timeout": ({"--signal", "--kill-after"}, {"--foreground", "--preserve-status", "--verbose"}, "sk", "fpv"),
    "nice": ({"--adjustment"}, set(), "n", ""),
    "setsid": (set(), {"--ctty", "--fork", "--wait"}, "", "cfw"),
}
_FIND_ARGUMENTS = dict.fromkeys((
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
    "-type", "-xtype", "-user", "-uid", "-group", "-gid", "-size", "-perm", "-links", "-inum",
    "-samefile", "-fstype", "-newer", "-anewer", "-cnewer", "-mtime", "-mmin", "-atime", "-amin",
    "-ctime", "-cmin", "-used", "-context", "-printf", "-fprint", "-fprint0", "-fls", "-regextype",
    "-maxdepth", "-mindepth", "-files0-from", "-D",
), 1)
_FIND_ARGUMENTS["-fprintf"] = 2


def _ansi_literal(text: str) -> str:
    escapes = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
               "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}

    def decode(match: re.Match) -> str:
        value = match.group()[1:]
        if value in escapes:
            return escapes[value]
        if value.startswith("c"):
            return chr(127 if value[1] == "?" else ord(value[1]) & 31)
        try:
            if value[0] in "xuU":
                return chr(int(value[1:], 16))
            return chr(int(value, 8) % 256)
        except (ValueError, OverflowError):
            return match.group()

    decoded = re.sub(r"\\(?:[abefnrtvE\\'\"?]|[0-7]{1,3}|x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|c[\s\S])",
                     decode, text)
    return decoded.split("\x00", 1)[0]  # Bash words cannot retain a NUL inside this fragment.


def _literal_word(word: str) -> str:
    """Remove literal shell quoting/escapes without expanding variables or running code."""
    result: list[str] = []
    quote = None
    index = 0
    while index < len(word):
        char = word[index]
        ansi = _ANSI_QUOTE.match(word, index) if quote is None else None
        if ansi:
            result.append(_ansi_literal(ansi.group()[2:-1]))
            index = ansi.end()
            continue
        if quote == "'":
            if char == "'":
                quote = None
            else:
                result.append(char)
        elif char in "\"'" and (quote is None or quote == char):
            quote = None if quote else char
        elif char in "\\`" and index + 1 < len(word):
            following = word[index + 1]
            if following != "\n":
                if char == "\\" and quote == '"' and following not in '$`"\\':
                    result.append(char)
                result.append(following)
            index += 1
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _backquote_payloads(command: str):
    """Find literal Bash substitutions, respecting single quotes and escaped delimiters."""
    quote = None
    index = 0
    while index < len(command):
        char = command[index]
        ansi = _ANSI_QUOTE.match(command, index) if quote is None else None
        if ansi:
            index = ansi.end()
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            index += 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote else '"'
        elif char == "`":
            end = index + 1
            while end < len(command):
                if command[end] == "\\":
                    end += 2
                elif command[end] == "`":
                    break
                else:
                    end += 1
            if end < len(command):
                # Old-style substitutions remove these backslashes before parsing.
                yield re.sub(r"\\([$`\\])", r"\1", command[index + 1:end])
                index = end
        index += 1


def _substitution_end(command: str, start: int, depth: int = 0) -> int | None:
    """Locate a balanced $() without evaluating it; inner quoting is independent."""
    if depth > 8:
        raise ValueError("excessive substitution nesting")
    quote = None
    parentheses = 1
    index = start + 2
    while index < len(command):
        char = command[index]
        ansi = _ANSI_QUOTE.match(command, index) if quote is None else None
        if ansi:
            index = ansi.end()
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            index += 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote else '"'
        elif char == "`":
            backquote = re.match(r"`(?:\\[\s\S]|[^`\\])*`", command[index:])
            if backquote:
                index += len(backquote.group())
                continue
        elif command.startswith("$(", index):
            end = _substitution_end(command, index, depth + 1)
            if end is None:
                return None
            index = end
            continue
        elif quote is None:
            if char == "(":
                parentheses += 1
            elif char == ")":
                parentheses -= 1
                if not parentheses:
                    return index + 1
        index += 1
    return None


def _substitution_spans(command: str):
    """Yield outer executed $() spans; quoted/escaped data and backquotes stay separate."""
    quote = None
    index = 0
    while index < len(command):
        char = command[index]
        ansi = _ANSI_QUOTE.match(command, index) if quote is None else None
        if ansi:
            index = ansi.end()
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            index += 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote else '"'
        elif char == "`":
            backquote = re.match(r"`(?:\\[\s\S]|[^`\\])*`", command[index:])
            if backquote:
                index += len(backquote.group())
                continue
        elif command.startswith("$(", index):
            end = _substitution_end(command, index)
            if end is not None:
                yield index, end, command[index + 2:end - 1]
                index = end
                continue
        index += 1


def _bare(word: str) -> str:
    return _literal_word(word).casefold()


def _command_name(word: str) -> str:
    # Also retain Windows path semantics: a backslash there is a separator,
    # whereas Bash removes an unquoted backslash before comparing the name.
    names = [re.split(r"[/\\]", value.casefold())[-1].removesuffix(".exe")
             for value in (_literal_word(word), word.strip("\"'"))]
    return next((name for name in names if name in _KNOWN_COMMANDS), names[0])


def _without_redirections(words: list[str]) -> list[str]:
    result = []
    index = 0
    while index < len(words):
        if _REDIRECTION.fullmatch(words[index]):
            index += 2  # the operator and its literal filename/fd are not argv
        else:
            result.append(words[index])
            index += 1
    return result


def _sudo_command_start(words: list[str], start: int) -> int:
    """Skip literal sudo options/operands and assignments, without executing anything."""
    while start < len(words):
        option = _literal_word(words[start])  # -H and -h have different operand rules.
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", option):
            start += 1
            continue
        if option == "--":
            return start + 1
        if not option.startswith("-") or option == "-":
            break
        start += 1
        if option.startswith("--"):
            # getopt_long also accepts unambiguous abbreviations of these options.
            if "=" not in option and any(name.startswith(option) for name in _SUDO_ARGUMENT_OPTIONS):
                start += 1
        else:
            for index, flag in enumerate(option[1:], start=2):
                if flag in "aCcDghpRrTtUu":
                    if index == len(option):  # an attached value consumes the rest of the same token
                        start += 1
                    break
    return start


def _env_command_start(words: list[str], start: int) -> tuple[int, str | None]:
    """Locate env's command, or its literal split-string payload (including -vS)."""
    while start < len(words):
        option = _literal_word(words[start])
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", option):
            start += 1
            continue
        if option == "--":
            return start + 1, None
        if not option.startswith("-"):
            break
        start += 1
        split = False
        value = None
        if option.startswith("--"):
            key, attached, value = option.partition("=")
            split = key == "--split-string"
            takes_value = split or key in {"--unset", "--chdir", "--argv0"}
            value = value if attached else None
        else:
            takes_value = False
            for index, flag in enumerate(option[1:], start=2):
                if flag in "SuCa":
                    takes_value = True
                    split = flag == "S"
                    value = option[index:] if index < len(option) else None
                    break
        if takes_value and value is None and start < len(words):
            value = _literal_word(words[start])
            start += 1
        if split:
            payload = (value or "") + " " + " ".join(words[start:])
            return len(words), payload
    return start, None


def _wrapper_command_start(words: list[str], start: int, name: str) -> int:
    start += 1
    while start < len(words):
        option = _literal_word(words[start])
        if option == "--":
            return start + 1
        if name in {"command", "time"} and re.fullmatch(r"-p+", option):
            start += 1
        elif name == "exec" and re.fullmatch(r"-[cla]+.*", option):
            start += 1
            for index, flag in enumerate(option[1:], start=2):
                if flag == "a":
                    if index == len(option):
                        start += 1
                    break
        else:
            break
    return start


def _xargs_command_start(words: list[str], start: int) -> int:
    """Locate xargs' literal command; optional -e/-i/-l values must be attached."""
    while start < len(words):
        option = _literal_word(words[start])
        if option == "--":
            return start + 1
        if not option.startswith("-") or option == "-":
            break
        if option.startswith("--"):
            key, attached, _value = option.partition("=")
            matches = [key] if key in _XARGS_OPTIONS else [
                name for name in _XARGS_OPTIONS if name.startswith(key)]
            if len(matches) != 1:
                break  # includes --help/--version, which do not execute a command
            start += 1
            if matches[0] in _XARGS_ARGUMENT_OPTIONS and not attached:
                start += 1
        else:
            option_index = start
            start += 1
            for index, flag in enumerate(option[1:], start=2):
                if flag in "adEILnPs":
                    if index == len(option):
                        start += 1
                    break
                if flag in "eil":  # any remaining characters are the optional value
                    break
                if flag not in "0oprtx":
                    return option_index
    return start


def _execution_command_start(words: list[str], start: int, name: str) -> int:
    """Locate the command of timeout/nice/setsid; their option values are data."""
    arguments, switches, short_arguments, short_switches = _EXEC_OPTIONS[name]
    options = arguments | switches | {"--help", "--version"}
    start += 1
    while start < len(words):
        option = _literal_word(words[start])
        if option == "--":
            start += 1
            break
        if not option.startswith("-") or option == "-":
            break
        start += 1
        if name == "nice" and re.fullmatch(r"--?\d+", option):
            continue  # obsolete -ADJUSTMENT syntax, including a negative adjustment
        if option.startswith("--"):
            key, attached, _value = option.partition("=")
            matches = [key] if key in options else [value for value in options if value.startswith(key)]
            if len(matches) != 1 or matches[0] in {"--help", "--version"}:
                return len(words)
            if matches[0] in arguments and not attached:
                start += 1
        else:
            for index, flag in enumerate(option[1:], start=2):
                if flag in short_arguments:
                    if index == len(option):
                        start += 1
                    break
                if flag not in short_switches:
                    return len(words)  # includes help/version, which never run COMMAND
    return start + (name == "timeout")  # DURATION precedes timeout's COMMAND


def _find_payloads(words: list[str]):
    """Find literal -exec/-execdir/-ok/-okdir actions, not predicate/format operands."""
    index = 0
    payloads = []
    while index < len(words):
        word = _literal_word(words[index])
        if word in {"-help", "--help", "-version", "--version"}:
            return  # find exits during expression parsing, before executing any action
        if word in {"-exec", "-execdir", "-ok", "-okdir"}:
            start = index + 1
            index = start
            while index < len(words):
                value = _literal_word(words[index])
                if value == ";" or (value == "+" and index > start and _literal_word(words[index - 1]) == "{}"):
                    payloads.append(" ".join(words[start:index]))
                    break
                index += 1
            if index == len(words):
                # The existing group lexer can split an unquoted {} placeholder.
                # Inspect the action's command prefix without changing that lexer.
                payloads.append(" ".join(words[start:index]))
            index += 1
        else:
            arity = _FIND_ARGUMENTS.get(word, 1 if re.fullmatch(r"-newer[a-zA-Z]{2}", word) else 0)
            index += 1 + arity
    yield from payloads


def broad_process_stop(command: str, *, _depth: int = 0) -> bool:
    """Recognize literal shell stop commands, not prose or PID-based cleanup.

    This is a conservative lexical guard, not a shell sandbox. Dynamically constructed
    executables still need the existing interpreter/unknown-command approval policy.
    """
    if _depth > 8:
        return True  # excessive literal nesting must not turn a denied command into allow
    if any(broad_process_stop(payload, _depth=_depth + 1) for payload in _backquote_payloads(command)):
        return True
    selected_variables: set[str] = set()
    substitution_references: dict[str, set[str]] = {}
    fragments = []
    previous = 0
    try:
        for index, (start, end, payload) in enumerate(_substitution_spans(command)):
            if broad_process_stop(payload, _depth=_depth + 1):
                return True
            marker = f"__labhq_selection_{index}"
            substitution_references[marker] = set(re.findall(
                r"\$([a-z_][a-z_0-9:]*)", payload.casefold()))
            # Reuse the existing InputObject producer analysis, never a shell.
            if broad_process_stop(f"Stop-Process -InputObject ({payload})", _depth=_depth + 1):
                selected_variables.add(marker)
            fragments.extend((command[previous:start], "$" + marker))
            previous = end
    except ValueError:
        return True  # excessive parsing depth must fail closed
    command = "".join(fragments) + command[previous:]
    words = _WORDS.findall(command)
    groups: list[list[str]] = [[]]
    for word in words:
        if _SEPARATOR.fullmatch(word):
            groups.extend([[word], []])
        else:
            groups[-1].append(word)
    process_pipeline = False
    pipe_continuation = False
    block_pipeline: list[bool] = []
    input_expression: list[bool] = []
    input_pending = False
    assignment: tuple[str, int] | None = None
    assignment_waiting = False
    for group in groups:
        if not group:
            continue
        if _SEPARATOR.fullmatch(group[0]):
            separator = group[0]
            if separator in {"(", "{"}:
                input_expression.append(input_pending or bool(input_expression and input_expression[-1]))
                input_pending = False
                if separator == "{":
                    block_pipeline.append(process_pipeline)
                else:
                    process_pipeline = False
            elif separator in {")", "}"}:
                if input_expression:
                    input_expression.pop()
                if separator == "}" and block_pipeline:
                    process_pipeline = process_pipeline or block_pipeline[-1]
                    block_pipeline.pop()
                    if block_pipeline:
                        block_pipeline[-1] |= process_pipeline
            elif separator == "|":
                pipe_continuation = True
                input_pending = False
            elif separator != "\n" or not pipe_continuation:
                process_pipeline = False
                pipe_continuation = False
                input_pending = False
            if (separator in {";", "\n"} and assignment and len(input_expression) <= assignment[1]
                    and (separator != "\n" or not assignment_waiting)):
                assignment = None
            continue
        pipe_continuation = False
        group = _without_redirections(group)
        if not group:
            continue
        assignment_waiting = False
        assigned = re.fullmatch(r"\$([A-Za-z_][A-Za-z_0-9:]*)=(.*)", group[0])
        if not assigned and len(group) > 1:
            assigned = re.fullmatch(r"\$([A-Za-z_][A-Za-z_0-9:]*)=(.*)", group[0] + group[1])
            if assigned:
                group = [group[0] + group[1]] + group[2:]
        if assigned:
            assignment = (assigned.group(1).casefold(), len(input_expression))
            group = ([assigned.group(2)] if assigned.group(2) else []) + group[1:]
            assignment_waiting = not group
        references = set(re.findall(r"\$([a-z_][a-z_0-9:]*)", " ".join(group).casefold()))
        for reference in tuple(references):
            references.update(substitution_references.get(reference, ()))
        selected_reference = bool(references & selected_variables)
        if selected_reference:
            if assignment:
                selected_variables.add(assignment[0])
            if input_expression and input_expression[-1]:
                return True
            if group and _bare(group[0]).startswith("$"):
                process_pipeline = True
        start = 0
        pipeline_arguments = False
        while start < len(group):
            word = _bare(group[start])
            name = _command_name(group[start])
            if name == "sudo":
                start = _sudo_command_start(group, start + 1)
                continue
            if name == "env":
                start, payload = _env_command_start(group, start + 1)
                if payload is not None and broad_process_stop(payload, _depth=_depth + 1):
                    return True
                continue
            if name == "xargs":
                start = _xargs_command_start(group, start + 1)
                pipeline_arguments = pipeline_arguments or process_pipeline
                continue
            if name in _EXEC_OPTIONS:
                start = _execution_command_start(group, start, name)
                continue
            if name in {"command", "exec", "nohup", "time"}:
                start = _wrapper_command_start(group, start, name)
                continue
            if word in {"&", "!", "builtin", "call", "then", "else", "do", "if", "elif", "while", "until", "coproc"} or re.match(
                    r"^[A-Za-z_][A-Za-z_0-9]*=", group[start]):
                start += 1
                continue
            break
        if start >= len(group):
            continue
        name = _command_name(group[start])
        args = [_bare(word) for word in group[start + 1:]]
        if name == "eval":
            payload_words = group[start + 1:]
            if payload_words and _literal_word(payload_words[0]) == "--":
                payload_words = payload_words[1:]
            payload = " ".join(_literal_word(word) for word in payload_words)
            if broad_process_stop(payload, _depth=_depth + 1):
                return True
        if name == "trap":
            handlers = group[start + 1:]
            end_options = bool(handlers and _literal_word(handlers[0]) == "--")
            if end_options:
                handlers = handlers[1:]
            if (len(handlers) > 1 and (end_options or not _literal_word(handlers[0]).startswith("-"))
                    and broad_process_stop(_literal_word(handlers[0]), _depth=_depth + 1)):
                return True
        if name == "find" and any(broad_process_stop(payload, _depth=_depth + 1)
                                  for payload in _find_payloads(group[start + 1:])):
            return True
        if name in {"pkill", "killall"}:
            return True
        if name == "taskkill" and any(re.match(r"^/{1,2}im(?::|$)", arg) for arg in args):
            return True
        if name in {"get-process", "gps", "ps", "pgrep"}:
            # Explicit -Id is PID-based. Otherwise the pipeline carries a name-selected set.
            process_pipeline = name == "pgrep" or not any(re.match(r"^-(?:id|pid)(?::|$)", arg) for arg in args)
            if process_pipeline and assignment:
                # Retain taint across statements/overwrites: branch execution is not proven.
                selected_variables.add(assignment[0])
            if process_pipeline and block_pipeline:
                # A block can emit process objects before its final statement.
                block_pipeline[-1] = True
            if process_pipeline and input_expression and input_expression[-1]:
                return True
        if name in {"stop-process", "spps", "kill"}:
            if selected_reference:
                return True
            input_pending = any(arg.startswith("-") and len(arg.strip("-:")) >= 2
                                and "inputobject".startswith(arg.strip("-:")) for arg in args)
            # Bash kill -n selects a signal, not a process name.
            name_option = r"^-name(?::|$)" if name == "kill" else r"^-n(?:a(?:m(?:e)?)?)?(?::|$)"
            if any(re.match(name_option, arg) for arg in args):
                return True
            if name == "kill":
                for i, arg in enumerate(args):
                    abbreviated = re.fullmatch(r"-(n|na|nam)(?::(.*))?", arg)
                    if abbreviated:
                        value = abbreviated.group(2)
                        if value is None:
                            value = args[i + 1] if i + 1 < len(args) else ""
                        # PowerShell kill aliases Stop-Process; only Bash's numeric
                        # -n SIGNAL is exempt from its literal Name abbreviations.
                        if abbreviated.group(1) != "n" or not value.isdigit():
                            return True
            pid_target = (any(re.match(r"^-id(?::|$)", arg) for arg in args)
                          or name == "kill" and any(arg.isdigit() or arg.startswith("$") for arg in args))
            positional_name = (args and not args[0].startswith(("-", "$", "@"))
                               and not args[0].isdigit())
            selected_processes = process_pipeline or bool(block_pipeline and block_pipeline[-1])
            if selected_processes and (not pid_target or pipeline_arguments) or positional_name:
                return True
        if name in {"sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh"}:
            for i, arg in enumerate(args[:-1]):
                shell_cluster = name in {"sh", "bash", "zsh", "dash"} and re.fullmatch(r"-[^-]*c[^-]*", arg)
                if shell_cluster or arg in {"-c", "/c", "/k", "-command", "-comm", "-c:"}:
                    payload_words = group[start + i + 2:]
                    # cmd /c and PowerShell -Command consume the remaining command
                    # string; Bash -c consumes one argument (the rest are $0, $1, ...).
                    payload = _literal_word(payload_words[0])
                    if name not in {"sh", "bash", "zsh", "dash"}:
                        payload += " " + " ".join(payload_words[1:])
                    if broad_process_stop(payload, _depth=_depth + 1):
                        return True
    return False
