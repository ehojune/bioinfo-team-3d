"""Encoded/member/wrapper P1 regressions; strings and fake brokers only."""
import base64

import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


def encoded(command):
    return base64.b64encode(command.encode('utf-16-le')).decode('ascii')


BROAD = encoded('Stop-Process -Name python')
OWNED = encoded('Stop-Process -Id 1234')
CASES = [
    pytest.param('pwsh -EncodedCommand ' + BROAD, id='4322864519-encoded-command'),
    *[pytest.param(f'powershell.exe -NoProfile {flag} {BROAD}', id='encoded-' + flag)
      for flag in ('-e', '-ec', '-enc', '-encoded', '-ENCODEDCOMMAND')],
    'pwsh -OutputFormat text -ec ' + BROAD,
    'pwsh -i -ec ' + BROAD,
    'pwsh -in -ec ' + BROAD,
    'pwsh -WorkingDirectory work -ec ' + BROAD,
    'sudo -u nobody pwsh -ec ' + BROAD,
    'pwsh -ec "' + BROAD[:12] + ' \n' + BROAD[12:] + '"',
    'pwsh -ec ' + encoded('pwsh -ec ' + BROAD),
    'pwsh -ec ' + encoded('\ufeffStop-Process -Name python'),
    'pwsh -ec invalid-base64',
    'pwsh -ec ' + base64.b64encode(b'x').decode('ascii'),
    pytest.param('pwsh -ec ' + encoded('Write-Output ' + 'x' * 49140), id='encoded-size-bound'),
    pytest.param('Get-Process python | ForEach-Object -MemberName Kill', id='4322864528-member-name'),
    *[f'gps python | {alias} Kill' for alias in ('ForEach-Object', 'foreach', '%')],
    'Get-Process python | ForEach-Object -M Kill',
    'Get-Process python | ForEach-Object -MemberName:KiLl',
    'Get-Process python | ForEach-Object -ErrorAction Stop -MemberName Kill',
    'Get-Process python | ForEach-Object -ea Stop -MemberName Kill',
    'Get-Process python | ForEach-Object -ar "argument" -MemberName Kill',
    'Get-Process python | ForEach-Object -Verbose -MemberName Kill',
    'Get-Process python | ForEach-Object -MemberName "Ki*"',
    'Get-Process python | ForEach-Object -MemberName "K?ll"',
    '$p = Get-Process python; $p | foreach Kill',
    '$p = Get-Process python; ForEach-Object -InputObject $p -MemberName Kill',
    pytest.param('stdbuf -oL pkill python', id='4322864535-stdbuf'),
    'stdbuf -o L killall python',
    'stdbuf --output=L --input=0 --error=0 pkill python',
    'stdbuf --out L -- pkill python',
    'sudo -u nobody stdbuf -i0 -e0 kill 0',
    'stdbuf -oL bash -c "pkill python"',
]


@pytest.mark.parametrize('command', CASES)
async def test_payload_consumers_deny_without_approval_handlers(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('broad stops must not reach card/event/submit handlers')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='static payload regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    'pwsh -ec ' + OWNED,
    'powershell -e ' + encoded('Get-Process python'),
    'pwsh -ec ' + encoded("Write-Output 'Stop-Process -Name python'"),
    'pwsh -ec ' + encoded('pwsh -ec ' + OWNED),
    pytest.param('pwsh -ec ' + encoded('Write-Output ' + 'x' * 49139), id='encoded-exact-bound-safe'),
    'pwsh -File script.ps1 -EncodedCommand ' + BROAD,
    'pwsh script.ps1 -ec ' + BROAD,
    'pwsh -Command Write-Output -EncodedCommand ' + BROAD,
    'echo "pwsh -ec ' + BROAD + '"',
    'Get-Process -Id 1234 | ForEach-Object -MemberName Kill',
    '$p = Get-Process -Id $ownedPid; ForEach-Object -InputObject $p -MemberName Kill',
    'Get-Process python | ForEach-Object -MemberName Id',
    'Get-Process python | ForEach-Object -MemberName Id -ArgumentList Kill',
    'Get-Process python | ForEach-Object -ErrorVariable Kill -MemberName Id',
    'Get-Process python | ForEach-Object -ev Kill -MemberName Id',
    'Get-Process python | ForEach-Object -ArgumentList Kill -MemberName ProcessName',
    'Get-Process python | Write-Output "ForEach-Object -MemberName Kill"',
    'Get-Process python; ForEach-Object -InputObject $other -MemberName Kill',
    'stdbuf -oL kill -TERM 1234',
    'stdbuf --output L pgrep python',
    'stdbuf --help pkill python',
    'stdbuf --version pkill python',
    'stdbuf -o pkill echo ready',
    'stdbuf -oL echo "pkill python"',
    'echo "stdbuf -oL pkill python"',
])
def test_pid_queries_and_nonexecuted_payload_data_are_preserved(command):
    assert not broad_process_stop(command)
