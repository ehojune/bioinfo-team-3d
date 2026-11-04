"""Literal find actions and pipeline-derived IDs; no shell or process is executed."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param(r'find . -exec echo {} \; -exec pkill python {} \;', id='4321387908-following-action'),
    pytest.param(r'find . -exec echo {} + -exec pkill python {} \;', id='find-first-batch'),
    pytest.param(r'find . -exec echo {} \; -execdir killall python {} \;', id='find-execdir'),
    pytest.param(r'find . -ok echo {} \; -okdir pkill python {} \;', id='find-confirm-actions'),
    pytest.param(r'find . -exec echo {} \; -exec echo {} \; -exec pkill python {} +', id='find-third-batch'),
    pytest.param(r'find . -exec echo {} \; -exec sudo -u nobody pkill python {} \;', id='find-wrapper'),
    pytest.param(r'find . -exec echo {} \; -exec sh -c "pkill python" {} \;', id='find-shell-action'),
    pytest.param(r'bash -c "find . -exec echo {} \; -exec pkill python {} \;"', id='find-shell-wrapper'),
    pytest.param('Get-Process python | ForEach-Object { Stop-Process -Id $_.Id }', id='4321387934-pipeline-id'),
    pytest.param('gps python | ForEach-Object { spps -Id $_.Id }', id='pipeline-aliases'),
    pytest.param('Get-Process python | ForEach-Object { kill -Id $PSItem.Id }', id='pipeline-psitem-alias'),
    pytest.param('Get-Process python | ForEach-Object { Stop-Process -Id:$_.Id }', id='pipeline-attached-id'),
    pytest.param('Get-Process python | ForEach-Object { Stop-Process -Id ($_.Id) }', id='pipeline-id-expression'),
    pytest.param('Get-Process python | ForEach-Object { Stop-Process -Id (($PSItem.Id)) }', id='pipeline-nested-id'),
    pytest.param('Get-Process python | ForEach-Object { echo ready; Stop-Process -Id $_.Id }', id='pipeline-later-statement'),
    pytest.param('Get-Process python | ForEach-Object { $target = $_.Id; Stop-Process -Id $target }', id='pipeline-copy-id'),
    pytest.param('Get-Process python | Where-Object { $_.Name } | ForEach-Object { Stop-Process -Id $_.Id }', id='pipeline-filtered-id'),
]


@pytest.mark.parametrize('command', CASES)
@pytest.mark.asyncio
async def test_following_actions_and_selected_ids_deny_before_handlers(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('a name-selected stop must not reach card/event/submit handlers')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='literal action regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    r'find . -exec echo {} \; -exec echo pkill python {} \;',
    r'find . -exec echo {} + -exec printf "%s" "pkill python" {} \;',
    r'find . -exec echo {} \; -exec kill -TERM 1234 \;',
    r'find . -name -exec -exec echo {} \;',
    r'find . -exec echo {} \; --help -exec pkill python {} \;',
    'Get-Process -Id 1234 | ForEach-Object { Stop-Process -Id $_.Id }',
    'Get-Process -Id 1234 | ForEach-Object { Stop-Process -Id ($PSItem.Id) }',
    'Get-Process python | ForEach-Object { Stop-Process -Id 1234 }',
    'Get-Process python | ForEach-Object { Stop-Process -Id $ownedPid }',
    'Get-Process python | ForEach-Object { Stop-Process -Id $PSItemOther }',
    'Get-Process python | ForEach-Object { Write-Output $_.Id }',
    'Get-Process python; Stop-Process -Id $ownedPid',
])
def test_data_queries_and_independent_ids_remain_outside_name_stop(command):
    assert not broad_process_stop(command)
