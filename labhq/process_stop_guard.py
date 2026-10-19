"""Static name-based process-stop guard; never inspects or terminates a process."""

from __future__ import annotations

import re


DENIAL = (
    "이름으로 여러 프로세스를 종료하는 명령은 승인 카드 없이 거부합니다. "
    "자기 작업 폴더 경로가 명령줄에 있는지 확인한 PID만 지정해 종료하세요. "
    "PID와 시작 시각을 다시 확인하고 다른 요청이나 gateway·runner는 종료하지 마세요."
)

# Keep quoted arguments whole, and inspect every connected command (including fallback branches).
_WORDS = re.compile(r'''"(?:`.|\\.|[^"\\])*"|'(?:''|[^'])*'|&&|\|\||[;&|(){}\n]|[^\s;&|(){}'"\n]+''')
_SEPARATOR = re.compile(r"^[;&|(){}\n]+$")

# sudo options with required operands; --preserve-env's optional value uses '='.
_SUDO_ARGUMENT_OPTIONS = {
    "--auth-type", "--close-from", "--login-class", "--chdir", "--group", "--host",
    "--other-user", "--prompt", "--chroot", "--role", "--command-timeout", "--type", "--user",
}


def _bare(word: str) -> str:
    return word.strip("\"'").casefold()


def _sudo_command_start(words: list[str], start: int) -> int:
    """Skip literal sudo options/operands and assignments, without executing anything."""
    while start < len(words):
        option = words[start].strip("\"'")  # -H and -h have different operand rules.
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
    input_expression: list[bool] = []
    input_pending = False
    for group in groups:
        if not group:
            continue
        if _SEPARATOR.fullmatch(group[0]):
            separator = group[0]
            if separator == "(":
                input_expression.append(input_pending or bool(input_expression and input_expression[-1]))
                input_pending = False
                process_pipeline = False
            elif separator == ")":
                if input_expression:
                    input_expression.pop()
            elif separator == "|":
                pipe_continuation = True
                input_pending = False
            elif separator != "\n" or not pipe_continuation:
                process_pipeline = False
                pipe_continuation = False
                input_pending = False
            continue
        pipe_continuation = False
        start = 0
        while start < len(group):
            word = _bare(group[start])
            if re.split(r"[/\\]", word)[-1].removesuffix(".exe") == "sudo":
                start = _sudo_command_start(group, start + 1)
                continue
            if word == "env":
                start += 1
                while start < len(group) and _bare(group[start]).startswith("-"):
                    option = _bare(group[start])
                    start += 1
                    if option == "--":
                        break
                    if option in {"-s", "--split-string"} and start < len(group):
                        payload = group[start].strip("\"'") + " " + " ".join(group[start + 1:])
                        if broad_process_stop(payload, _depth=_depth + 1):
                            return True
                        start = len(group)
                    elif option in {"-u", "--unset", "-c", "--chdir"}:
                        start += 1
                continue
            if word in {"&", "!", "nohup", "command", "exec", "call", "then", "do"} or re.match(
                    r"^[A-Za-z_][A-Za-z_0-9]*=", group[start]):
                start += 1
                continue
            break
        if start >= len(group):
            continue
        name = re.split(r"[/\\]", _bare(group[start]))[-1]
        name = name.removesuffix(".exe")
        args = [_bare(word) for word in group[start + 1:]]
        if name in {"pkill", "killall"}:
            return True
        if name == "taskkill" and any(re.match(r"^/{1,2}im(?::|$)", arg) for arg in args):
            return True
        if name in {"get-process", "gps", "ps"}:
            # Explicit -Id is PID-based. Otherwise the pipeline carries a name-selected set.
            process_pipeline = not any(re.match(r"^-(?:id|pid)(?::|$)", arg) for arg in args)
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
            if process_pipeline and not pid_target or positional_name:
                return True
        if name in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}:
            for i, arg in enumerate(args[:-1]):
                if arg in {"-c", "/c", "-command", "-comm", "-c:"}:
                    payload_words = group[start + i + 2:]
                    # cmd /c and PowerShell -Command consume the remaining command
                    # string; Bash -c consumes one argument (the rest are $0, $1, ...).
                    payload = (payload_words[0].strip("\"'") if len(payload_words) == 1
                               or name in {"sh", "bash", "zsh"} else " ".join(payload_words))
                    if broad_process_stop(payload, _depth=_depth + 1):
                        return True
    return False
