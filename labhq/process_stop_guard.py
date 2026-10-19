"""Static name-based process-stop guard; never inspects or terminates a process."""

from __future__ import annotations

import re


DENIAL = (
    "이름으로 여러 프로세스를 종료하는 명령은 승인 카드 없이 거부합니다. "
    "자기 작업 폴더 경로가 명령줄에 있는지 확인한 PID만 지정해 종료하세요. "
    "PID와 시작 시각을 다시 확인하고 다른 요청이나 gateway·runner는 종료하지 마세요."
)

# Keep quoted arguments whole, and inspect every connected command (including fallback branches).
_WORDS = re.compile(r'''"(?:`.|\\.|[^"\\])*"|'(?:''|[^'])*'|[;&|()\n]+|[^\s;&|()'"\n]+''')
_SEPARATOR = re.compile(r"^[;&|()\n]+$")


def _bare(word: str) -> str:
    return word.strip("\"'").casefold()


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
    for group in groups:
        if not group:
            continue
        if _SEPARATOR.fullmatch(group[0]):
            if group[0] != "|":
                process_pipeline = False
            continue
        start = 0
        while start < len(group) and (_bare(group[start]) in {"&", "!", "sudo", "env", "nohup", "command", "exec", "call", "then", "do"}
                                     or re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", group[start])):
            start += 1
        if start == len(group):
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
            process_pipeline = not any(arg in {"-id", "-pid"} for arg in args)
        if name in {"stop-process", "spps", "kill"}:
            if any(re.match(r"^-n(?:a(?:m(?:e)?)?)?(?::|$)", arg) for arg in args):
                return True
            positional_name = (args and not args[0].startswith(("-", "$", "@"))
                               and not args[0].isdigit())
            if process_pipeline or positional_name:
                return True
        if name in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}:
            for i, arg in enumerate(args[:-1]):
                if arg in {"-c", "/c", "-command", "-comm", "-c:"}:
                    if broad_process_stop(group[start + i + 2].strip("\"'"), _depth=_depth + 1):
                        return True
    return False
