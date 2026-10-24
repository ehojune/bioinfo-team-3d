"""Shell commands that detach a process from the step that started it (#675).

A process started with `nohup … &`, `setsid`, `disown` or PowerShell `Start-Process` outlives its step: labhq cannot stop
it when the step is cancelled or the runner restarts, its result may land after the step is done, and the agent then polls it
with one approval card per wait. The approval gate refuses such a command without a card and names the job tool instead.
"""
from __future__ import annotations

import re

DENIAL = (
    "떼어 내는 명령(nohup·뒤쪽 &·setsid·disown·Start-Process 등)은 거절합니다. 단계가 끝나거나 취소돼도 labhq가 모르는 "
    "프로세스가 남기 때문입니다. 10분을 넘는 계산은 labhq_job의 job_submit(명령·예상 시간)으로 내고, 기다리며 반복 "
    "확인하지 말고 상황을 요약해 턴을 끝내세요. 끝나면 이어서 깨웁니다. 짧은 명령은 끝까지 기다리세요."
)

_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_REDIRECT_AMP = re.compile(r"[0-9]*>>?&[0-9-]*|&>>?|\|&|<&[0-9-]*")
_WORD_DETACH = re.compile(r"(?<![\w.\-/\\])(nohup|setsid|disown)(?![\w\-.])", re.I)
_BACKGROUND_AMP = re.compile(r"(?<![&>|<])&(?![&>])")
_START_PROCESS = re.compile(r"(?<![\w\-])Start-Process(?![\w\-])", re.I)
_START_JOB = re.compile(r"(?<![\w\-])(Start-Job|Start-ThreadJob)(?![\w\-])", re.I)
_WAIT = re.compile(r"(?<![\w\-])-(?:Wait|Verb)(?![\w\-])", re.I)  # `-Verb RunAs` is an elevation the gate asks about
_SHELLS = {"Bash", "PowerShell"}


def _code(command: str) -> str:
    """The shell text itself: heredoc bodies, quoted strings and redirections never count as detaching."""
    text = _HEREDOC.sub("\n", command)
    text = _QUOTED.sub('""', text)
    return _REDIRECT_AMP.sub(" ", text)


def detached(tool_name: str, command: object) -> bool:
    """True when a shell command starts a process it does not wait for."""
    if tool_name not in _SHELLS or not isinstance(command, str) or not command.strip():
        return False
    text = _code(command)
    if _WORD_DETACH.search(text):
        return True
    if tool_name == "Bash":
        return bool(_BACKGROUND_AMP.search(text))
    return bool(_START_JOB.search(text) or (_START_PROCESS.search(text) and not _WAIT.search(text)))
