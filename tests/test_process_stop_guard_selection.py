"""P1 execution/selection regressions; every command is inert test data."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param('if false; then :; else pkill python; fi', id='4320847541-else'),
    pytest.param('if false; then :; else sudo -u nobody killall python; fi', id='else-wrapper'),
    pytest.param('if false; then :; else\n pkill python; fi', id='else-newline'),
    pytest.param('$p = Get-Process -Name python; Stop-Process -InputObject $p', id='4320847550-assignment'),
    pytest.param('$p=Get-Process python; spps -InputObject $p', id='assignment-attached'),
    pytest.param('$p =Get-Process python; spps -InputObject $p', id='assignment-attached-rhs'),
    pytest.param('$p= Get-Process python; spps -InputObject $p', id='assignment-separated-rhs'),
    pytest.param('$p =\nGet-Process python; spps -InputObject $p', id='assignment-multiline-rhs'),
    pytest.param('$P = gps python\n Stop-Process -InputObject $p', id='assignment-case-newline'),
    pytest.param('$p = (Get-Process python); Stop-Process -InputObject $p', id='assignment-expression'),
    pytest.param('$p = (echo ready; Get-Process python); Stop-Process -InputObject $p', id='assignment-later-output'),
    pytest.param('$p = Get-Process python; $p | Stop-Process', id='assignment-pipeline'),
    pytest.param('$p = Get-Process python; $q = $p; Stop-Process -InputObject $q', id='assignment-copy'),
    pytest.param('$p = Get-Process python; Stop-Process -Id $p.Id', id='assignment-selected-ids'),
    pytest.param('$p = Get-Process python; Stop-Process -InputObject ($p)', id='assignment-input-expression'),
    pytest.param('$p = Get-Process python; Stop-Process -InputObject $($p)', id='assignment-subexpression'),
    pytest.param('$p = Get-Process python; Stop-Process -InputObject $(echo "$($p)")', id='assignment-nested-subexpression'),
    pytest.param('$p = Get-Process python; $p = Get-Process -Id 1234; Stop-Process -InputObject $p', id='assignment-overwrite-conservative'),
    pytest.param('kill $(pgrep python)', id='4321129633-dollar-substitution'),
    pytest.param('kill -TERM "$(pgrep -f python)"', id='substitution-quoted'),
    pytest.param('kill 1234 $(pgrep python)', id='substitution-additional-targets'),
    pytest.param('kill $(command -p pgrep python)', id='substitution-wrapper'),
    pytest.param('kill "$(echo "$(pgrep python)")"', id='substitution-nested'),
    pytest.param('kill $(pgrep "[p]ython(3)?")', id='substitution-parentheses-data'),
    pytest.param('kill `pgrep python`', id='substitution-backquote-selection'),
    pytest.param('echo $(pkill python)', id='substitution-executed-stop'),
    pytest.param('echo "$(killall python)"', id='substitution-quoted-stop'),
    pytest.param('echo $(echo "$(pkill python)")', id='substitution-nested-stop'),
    pytest.param('kill `printf 1234`', id='unproven-backquote-target-stays-denied'),
    pytest.param(r'echo \$(pkill python)', id='escaped-dollar-bare-group-stays-denied'),
    pytest.param('echo ' + '$(' * 12 + 'pgrep python' + ')' * 12, id='substitution-depth-fails-closed'),
]


@pytest.mark.parametrize('command', CASES)
@pytest.mark.asyncio
async def test_name_selection_is_denied_without_approval_handlers(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('name-selected stops must not reach card/event/submit handlers')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='literal selection regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    'if false; then :; else echo "pkill python"; fi',
    'if false; then :; else kill -TERM 1234; fi',
    '$p = Get-Process python', '$p = Get-Process python; Write-Output $p',
    '$p = Get-Process python; Stop-Process -Id 1234',
    '$p = Get-Process -Id 1234; Stop-Process -InputObject $p',
    '$p=(Get-Process -Id $ownedPid); $p | Stop-Process',
    '$p = "Get-Process python"; Write-Output $p',
    '$p = Get-Process python; Stop-Process -InputObject $pOther',
    'kill $(printf 1234)', 'kill "$(printf 1234)"',
    'kill $(echo "$(printf 1234)")',
    'echo $(pgrep python)', 'printf "%s" "$(pgrep python)"',
    "echo '$(pkill python)'",
    r'echo "\$(pkill python)"', "echo $'$(pkill python)'",
    "echo 'kill $(pgrep python)'", 'echo $(echo "pkill python")',
    'echo "$(printf \'%s\' \'pkill python\')"',
    'echo $(printf "%s" "[()] pgrep python")',
])
def test_queries_literal_data_and_explicit_pids_remain_outside_name_stop(command):
    assert not broad_process_stop(command)
