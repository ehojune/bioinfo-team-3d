"""Literal review reproductions: every shell command is data, with a fake broker."""

import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param("bash -lc 'pkill python'", id="shell-bash-lc"),
    pytest.param("sh -ec 'killall python'", id="shell-sh-ec"),
    pytest.param("zsh -lc 'pkill python'", id="shell-zsh-lc"),
    pytest.param("bash --noprofile -lc 'pkill python'", id="shell-long-before-cluster"),
    pytest.param('cmd /k taskkill /IM python.exe', id="shell-cmd-k"),
    pytest.param('cmd.exe /d /s /K "taskkill //IM python.exe"', id="shell-cmd-k-quoted"),
    pytest.param("env -i bash -lc 'pkill python'", id="shell-nested"),
    pytest.param('command -p pkill python', id="command-p"),
    pytest.param('command -- pkill python', id="command-end-options"),
    pytest.param('command -p -- /usr/bin/pkill python', id="command-p-path"),
    pytest.param('exec -a cleanup pkill python', id="command-exec-operand"),
    pytest.param('exec -cl -- pkill python', id="command-exec-flags"),
    pytest.param('nohup -- pkill python', id="command-nohup"),
    pytest.param('builtin command -p pkill python', id="command-builtin"),
    pytest.param('if pkill python; then :; fi', id="conditional-if"),
    pytest.param('if false; then :; elif pkill python; then :; fi', id="conditional-elif"),
    pytest.param('while pkill python; do :; done', id="conditional-while"),
    pytest.param('until pkill python; do :; done', id="conditional-until"),
    pytest.param('time pkill python', id="conditional-time"),
    pytest.param('time -p pkill python', id="conditional-time-p"),
    pytest.param('time -- pkill python', id="conditional-time-end-options"),
    pytest.param('2>/dev/null pkill python', id="redirect-attached"),
    pytest.param('2> /dev/null pkill python', id="redirect-separated"),
    pytest.param('>out.log pkill python', id="redirect-output"),
    pytest.param('<input.txt pkill python', id="redirect-input"),
    pytest.param('2>&1 pkill python', id="redirect-fd-duplication"),
    pytest.param('&>out.log pkill python', id="redirect-both-streams"),
    pytest.param('LABEL=cleanup 2>>out.log command -p pkill python', id="redirect-assignment"),
    pytest.param('sudo -u 2>out.log nobody pkill python', id="redirect-within-wrapper"),
    pytest.param("env -vS'pkill python'", id="env-cluster-attached"),
    pytest.param("env -S'pkill python'", id="env-attached"),
    pytest.param("env --split-string='pkill python'", id="env-long-attached"),
    pytest.param("env -viS'pkill python'", id="env-multiple-flags"),
    pytest.param("env -uEXAMPLE -vS'pkill python'", id="env-attached-unset"),
    pytest.param("env -C/tmp -vS'pkill python'", id="env-attached-chdir"),
    pytest.param("env -vS'command -p pkill python'", id="env-nested-prefix"),
    pytest.param(r'pk\ill python', id="escape-pkill"),
    pytest.param(r'taskk\ill /IM python.exe', id="escape-taskkill"),
    pytest.param(r'k\illall python', id="escape-killall"),
    pytest.param(r'/usr/bin/pk\ill python', id="escape-absolute-path"),
    pytest.param("p'k'ill python", id="escape-single-quote-concatenation"),
    pytest.param('"p"k"ill" python', id="escape-double-quote-concatenation"),
    pytest.param('pk\\\nill python', id="escape-line-continuation"),
    pytest.param(r'taskkill /I\M python.exe', id="escape-option"),
    pytest.param('Get-Process python | ForEach-Object {\n Stop-Process -InputObject $_\n }',
                 id="foreach-newline"),
    pytest.param('Get-Process python | ForEach-Object { echo ready; Stop-Process -InputObject $_ }',
                 id="foreach-earlier-statement"),
]


@pytest.mark.parametrize("command", CASES)
@pytest.mark.asyncio
async def test_review_literal_prefixes_always_deny(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny" and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail("a broad stop must never reach a card/event/submit handler")

    for detail in ({"command": command}, {"tool_input": {"command": command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind="tool_permission", summary="literal regression", detail=detail))
        assert result == {"approved": False, "note": DENIAL, "state": "denied"}
        assert not broker.pending and not broker.pending_requests


def test_review_foreach_original_is_already_denied():
    command = 'Get-Process python | ForEach-Object { Stop-Process -InputObject $_ }'
    assert broad_process_stop(command)
    decision = evaluate_tool("PowerShell", {"command": command}, PolicySettings(), workdir=".")
    assert decision.action == "deny" and decision.reason == DENIAL


@pytest.mark.parametrize("command", [
    "bash -lc 'kill -TERM 1234'",
    'bash -lc "echo safe" "pkill python"',
    'cmd /k taskkill /PID 1234 /F',
    'command -p kill -TERM 1234',
    'command -v pkill', 'command -V pkill', 'command -pv pkill',
    'command -p echo "pkill python"',
    'exec -a pkill echo python',
    'nohup -- echo "pkill python"',
    'if echo "pkill python"; then :; fi',
    'while kill -0 1234; do :; done',
    '>pkill echo python', '2>pkill echo python',
    '2>&1 echo "pkill python"',
    "env -vS'echo pkill python'",
    "env --split-string='echo pkill python'",
    r'echo pk\ill python',
    r'pk\ill-not-a-command python',
    'Get-Process -Id $ownedPid | ForEach-Object {\n Stop-Process -InputObject $_\n }',
])
def test_review_pid_and_nonexecuting_text_stay_outside_name_stop(command):
    assert not broad_process_stop(command)
