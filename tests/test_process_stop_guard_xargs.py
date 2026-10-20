"""xargs review reproductions; commands are never executed or read as files."""

import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


@pytest.mark.parametrize("command", [
    r"printf '%s\n' python | xargs pkill",
    'xargs pkill python',
    'xargs -0r pkill python',
    'xargs -n1 -P2 pkill python',
    'xargs -n 1 -P 2 pkill python',
    'xargs -I TARGET pkill TARGET',
    "xargs -I '{}' pkill '{}'",
    'xargs -rI TARGET pkill TARGET',
    'xargs --replace=TARGET pkill TARGET',
    'xargs -i pkill python',
    'xargs -iTARGET pkill TARGET',
    'xargs -l pkill python',
    'xargs --max-lines pkill python',
    'xargs --arg-file input.txt --max-args=1 pkill python',
    'xargs -a input.txt -d : -E END -s 1024 -L1 pkill python',
    'xargs --delimiter=: --process-slot-var=SLOT --max-procs=2 pkill python',
    'xargs --max-a 1 pkill python',
    'xargs -- pkill python',
    '/usr/bin/xargs -- killall python',
    'xargs taskkill /IM python.exe',
    'xargs -- pwsh -Command "Stop-Process -Name python"',
    'xargs bash -lc "pkill python"',
    'env -i sudo -u nobody xargs -r pkill python',
    'xargs xargs -0r pkill python',
    r'xa\rgs -r pk\ill python',
    r'"C:\bin\xargs.exe" -r pkill python',
    'xargs -e pkill python',
    'xargs --eof pkill python',
    'xargs -eEND pkill python',
])
@pytest.mark.asyncio
async def test_xargs_literal_stop_is_denied_before_policy_or_card(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny" and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail("xargs stop must never reach a card/event/submit handler")

    for detail in ({"command": command}, {"tool_input": {"command": command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind="tool_permission", summary="literal xargs regression", detail=detail))
        assert result == {"approved": False, "note": DENIAL, "state": "denied"}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize("command", [
    r"printf '%s\n' pkill | xargs",
    'xargs echo pkill python',
    'xargs printf "%s" "pkill python"',
    'xargs -n1 echo "pkill python"',
    'xargs -I pkill echo text',
    'xargs --replace=pkill echo text',
    'xargs -i echo pkill',
    'xargs -ipkill echo text',
    'xargs -l echo pkill',
    'xargs --max-lines echo pkill',
    'xargs -a pkill echo text',
    'xargs -d k echo pkill',
    'xargs --arg-file=pkill echo text',
    'xargs --help pkill',
    'xargs --version pkill',
    'xargs kill -TERM 1234',
    'xargs taskkill /PID 1234 /F',
    "xargs sh -c 'echo safe' pkill",
    'xargs --replace=TARGET echo "Stop-Process -Name TARGET"',
])
def test_xargs_data_option_operands_and_explicit_pids_are_not_name_stop(command):
    assert not broad_process_stop(command)
