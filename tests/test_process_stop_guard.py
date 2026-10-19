"""All commands below are data: no subprocess, process lookup or termination."""

import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


@pytest.mark.parametrize("command", [
    'taskkill /IM python.exe',
    'taskkill /F /IM python.exe /FI "WINDOWTITLE eq *"',
    'pkill -f primary.py || taskkill //F //IM python.exe //FI "WINDOWTITLE eq *" >/dev/null 2>&1',
    'false && taskkill.exe /im:python.exe',
    'C:\\Windows\\System32\\taskkill.exe /IM python.exe',
    '"C:\\Windows\\System32\\taskkill.exe" //IM python.exe',
    'pkill python', 'sudo pkill -f python', 'killall python',
    'Stop-Process -Name python', 'Stop-Process -Name:python -Force',
    'Get-Process python | Stop-Process', 'gps python | spps',
    'Get-Process | Where-Object Name -eq python | Stop-Process',
    'Stop-Process python', 'pwsh -Command "Stop-Process -Name python"',
    "bash -c 'pkill python'", 'cmd /c "taskkill /IM python.exe"',
    'echo ready; taskkill /IM python.exe',
    'missing-command || taskkill //F //IM python.exe //FI "WINDOWTITLE eq *"',
    'Get-Process python | Stop-Process; echo done',
    'env LABEL=cleanup pkill python', 'nohup killall python',
    'Stop-Process -n python', 'Get-Process python | kill',
    'kill -n python', 'kill -na python', 'kill -nam python',
    'kill -n:python', 'kill -na:python', 'kill -nam:python',
    'kill -n "python" -Force', 'kill -n python,python3',
    'pwsh -Command "kill -n python"',
    'cmd /c taskkill /IM python.exe',
    'cmd.exe /d /s /c taskkill //F //IM python.exe',
    'pwsh -Command Stop-Process -Name python',
    'env -i pkill python', 'env --ignore-environment pkill python',
    'env -u EXAMPLE pkill python', 'env -C /tmp killall python',
    'env -S "pkill python"',
    'Get-Process python |\n Stop-Process',
    'Get-Process python | \n Stop-Process',
    'Get-Process python |\r\n Stop-Process',
    'Stop-Process -InputObject (Get-Process python)',
    'Stop-Process -InputObject (Get-Process python | Where-Object Name -eq python)',
    'Stop-Process -InputObject ((Get-Process python))',
    'kill -InputObject (gps python)',
])
def test_name_stop_always_denied(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny"
        assert decision.reason == DENIAL


@pytest.mark.parametrize("command", [
    'sudo -u nobody pkill python',
    'sudo -- pkill python',
    'sudo --user nobody killall python',
    'sudo --user=nobody pkill python',
    'sudo -unobody pkill python',
    'sudo -nu nobody pkill python',
    'sudo -nHu nobody pkill python',
    'sudo -g staff -u nobody pkill python',
    'sudo -h localhost pkill python',
    'sudo -D /tmp -C 3 pkill python',
    'sudo --chdir /tmp --command-timeout=30 pkill python',
    'sudo --preserve-env pkill python',
    'sudo --preserve-env=LANG pkill python',
    'sudo LABEL=cleanup -u nobody pkill python',
    'sudo -u nobody -- env -i pkill python',
    'env -i sudo -u nobody pkill python',
    'sudo -u nobody bash -c "pkill python"',
    '/usr/bin/sudo -u nobody pkill python',
])
def test_sudo_options_do_not_hide_name_stop(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny" and decision.reason == DENIAL


@pytest.mark.parametrize("command", [
    '& { Stop-Process -Name python }',
    '&{Stop-Process -Name python}',
    '. { Stop-Process -Name python }',
    '& { & { Stop-Process -Name python } }',
    '& { Get-Process python | Stop-Process }',
    '& {Stop-Process python}',
    'pwsh -Command "& { Stop-Process -Name python }"',
    '& { pkill python }',
    '& { taskkill /IM python.exe }',
    '& { Stop-Process -InputObject (Get-Process python) }',
])
def test_script_blocks_do_not_hide_name_stop(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny" and decision.reason == DENIAL


@pytest.mark.parametrize("command", [
    'taskkill /PID 1234 /F', 'taskkill //PID 1234 //F', 'kill -TERM 1234',
    'Stop-Process -Id 1234', 'Stop-Process -Id $ownedPid -Force',
    'Get-Process -Id $ownedPid | Stop-Process',
    'echo "pkill python"', 'printf "%s" "taskkill /IM python.exe"',
    'rg "Stop-Process -Name python" docs/manual.md', 'python cleanup.py',
    'Get-Process python; Stop-Process -Id $ownedPid',
    'kill -n 9 1234', 'ps -ef | kill -TERM 1234',
    'kill -n "9" 1234', 'kill -n 15 $ownedPid',
    'cmd /c taskkill /PID 1234 /F',
    'env -i kill -TERM 1234', 'env -u EXAMPLE kill -TERM 1234',
    'env -S "echo pkill python"',
    'Get-Process -Id $ownedPid |\n Stop-Process',
    'Get-Process python\n Stop-Process -Id $ownedPid',
    'Stop-Process -InputObject (Get-Process -Id $ownedPid)',
    'Stop-Process -InputObject $ownedProcess; Get-Process python',
    'Stop-Process -InputObject (Get-Process -Id:1234)',
    'env -i echo "pkill python"',
    'cmd /c "echo taskkill /IM python.exe"',
    'bash -c "echo safe" "pkill python"',
    'env -u',
    'sudo -u nobody kill -TERM 1234',
    'sudo -- taskkill /PID 1234 /F',
    'sudo -H kill -n 9 1234',
    'sudo -u pkill echo python',
    'sudo --prompt "pkill python" echo ready',
    'sudo --user=nobody echo "pkill python"',
    'sudo --preserve-env echo "pkill python"',
    'sudo -u',
    'sudo --',
    '& { Stop-Process -Id 1234 }',
    '& { Get-Process -Id $ownedPid | Stop-Process }',
    '& { echo "Stop-Process -Name python" }',
    'Write-Output "& { Stop-Process -Name python }"',
    "echo '& { pkill python }'",
])
def test_pid_cleanup_and_data_are_not_name_stop(command):
    assert not broad_process_stop(command)


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [
    {"command": "taskkill //IM python.exe"},
    {"tool_input": {"command": "Get-Process python | Stop-Process"}},
    {"command": "taskkill /IM python.exe", "tool_input": {}},
    {"command": "cmd /c taskkill /IM python.exe"},
    {"command": "env -i pkill python"},
    {"command": "Get-Process python |\n Stop-Process"},
    {"command": "Stop-Process -InputObject (Get-Process python)"},
    {"command": "sudo -u nobody pkill python"},
    {"tool_input": {"command": "sudo -- pkill python"}},
    {"command": "& { Stop-Process -Name python }"},
    {"tool_input": {"command": "pwsh -Command \"& { Stop-Process -Name python }\""}},
])
async def test_broker_denies_without_card_or_pending_future(detail):
    async def forbidden(*args):
        pytest.fail("denied commands must not reach a card/event/submit handler")

    broker = Broker(0, forbidden, forbidden, forbidden)
    result = await broker.request_approval(ApprovalRequest(
        kind="tool_permission", summary="cleanup", detail=detail))
    assert result == {"approved": False, "note": DENIAL, "state": "denied"}
    assert not broker.pending
    assert not broker.pending_requests


@pytest.mark.asyncio
async def test_pid_approval_retains_existing_card_flow():
    seen = []
    broker = None

    async def card(req):
        seen.append(req.id)
        broker.resolve(req.id, False, "fixture only")

    async def unused(*args):
        pytest.fail("unexpected handler")

    broker = Broker(0, card, unused, unused)
    req = ApprovalRequest(kind="tool_permission", summary="cleanup",
                          detail={"command": "taskkill /PID 1234 /F"})
    result = await broker.request_approval(req)
    assert seen == [req.id]
    assert result["note"] == "fixture only"
