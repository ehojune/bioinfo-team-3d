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
])
def test_name_stop_always_denied(command):
    assert broad_process_stop(command)
    for tool in ("Bash", "PowerShell"):
        decision = evaluate_tool(tool, {"command": command}, PolicySettings(), workdir=".")
        assert decision.action == "deny"
        assert decision.reason == DENIAL


@pytest.mark.parametrize("command", [
    'taskkill /PID 1234 /F', 'taskkill //PID 1234 //F', 'kill -TERM 1234',
    'Stop-Process -Id 1234', 'Stop-Process -Id $ownedPid -Force',
    'Get-Process -Id $ownedPid | Stop-Process',
    'echo "pkill python"', 'printf "%s" "taskkill /IM python.exe"',
    'rg "Stop-Process -Name python" docs/manual.md', 'python cleanup.py',
    'Get-Process python; Stop-Process -Id $ownedPid',
])
def test_pid_cleanup_and_data_are_not_name_stop(command):
    assert not broad_process_stop(command)


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [
    {"command": "taskkill //IM python.exe"},
    {"tool_input": {"command": "Get-Process python | Stop-Process"}},
    {"command": "taskkill /IM python.exe", "tool_input": {}},
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
