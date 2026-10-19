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
_WORDS = re.compile(
    _REDIRECTION.pattern + r'''|&&|\|\||[;&|(){}\n]|(?:"(?:`[\s\S]|\\[\s\S]|[^"\\`])*"|'''
    r"'[^']*'|\\[\s\S]|`[\s\S]|[^\s;&|(){}<>\"'\\`])+"
)
_SEPARATOR = re.compile(r"^[;&|(){}\n]+$")
_KNOWN_COMMANDS = {
    "pkill", "killall", "taskkill", "get-process", "gps", "ps", "stop-process", "spps", "kill",
    "sudo", "env", "command", "exec", "nohup", "builtin", "if", "elif", "while", "until", "time",
    "sh", "bash", "zsh", "cmd", "powershell", "pwsh", "xargs",
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


def _literal_word(word: str) -> str:
    """Remove literal shell quoting/escapes without expanding variables or running code."""
    result: list[str] = []
    quote = None
    index = 0
    while index < len(word):
        char = word[index]
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


def broad_process_stop(command: str, *, _depth: int = 0) -> bool:
    """Recognize literal shell stop commands, not prose or PID-based cleanup.

    This is a conservative lexical guard, not a shell sandbox. Dynamically constructed
    executables still need the existing interpreter/unknown-command approval policy.
    """
    if _depth > 8:
        return False
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
            continue
        pipe_continuation = False
        group = _without_redirections(group)
        start = 0
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
                continue
            if name in {"command", "exec", "nohup", "time"}:
                start = _wrapper_command_start(group, start, name)
                continue
            if word in {"&", "!", "builtin", "call", "then", "do", "if", "elif", "while", "until"} or re.match(
                    r"^[A-Za-z_][A-Za-z_0-9]*=", group[start]):
                start += 1
                continue
            break
        if start >= len(group):
            continue
        name = _command_name(group[start])
        args = [_bare(word) for word in group[start + 1:]]
        if name in {"pkill", "killall"}:
            return True
        if name == "taskkill" and any(re.match(r"^/{1,2}im(?::|$)", arg) for arg in args):
            return True
        if name in {"get-process", "gps", "ps"}:
            # Explicit -Id is PID-based. Otherwise the pipeline carries a name-selected set.
            process_pipeline = not any(re.match(r"^-(?:id|pid)(?::|$)", arg) for arg in args)
            if process_pipeline and block_pipeline:
                # A block can emit process objects before its final statement.
                block_pipeline[-1] = True
            if process_pipeline and input_expression and input_expression[-1]:
                return True
        if name in {"stop-process", "spps", "kill"}:
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
            if selected_processes and not pid_target or positional_name:
                return True
        if name in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}:
            for i, arg in enumerate(args[:-1]):
                shell_cluster = name in {"sh", "bash", "zsh"} and re.fullmatch(r"-[^-]*c[^-]*", arg)
                if shell_cluster or arg in {"-c", "/c", "/k", "-command", "-comm", "-c:"}:
                    payload_words = group[start + i + 2:]
                    # cmd /c and PowerShell -Command consume the remaining command
                    # string; Bash -c consumes one argument (the rest are $0, $1, ...).
                    payload = _literal_word(payload_words[0])
                    if name not in {"sh", "bash", "zsh"}:
                        payload += " " + " ".join(payload_words[1:])
                    if broad_process_stop(payload, _depth=_depth + 1):
                        return True
    return False
