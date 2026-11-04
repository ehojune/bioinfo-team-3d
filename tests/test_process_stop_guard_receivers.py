"""P1 receiver/wrapper/locale quoting regressions: inert strings and fake handlers."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


@pytest.mark.parametrize('command', [
    pytest.param('(Get-Process python).Kill()', id='4323699194-parenthesized-process'),
    '$p=Get-Process python; ($p).Kill()',
    '((Get-Process -Name python)).Kill($true)',
    '(gps python).Kill ()',
    '$p=Get-Process python; $q=$p; (($q)).Kill()',
    'Get-Process python | ForEach-Object { ($_).Kill() }',
    'gps python | % { (($PSItem)).Kill() }',
    '(Get-Process python | Select-Object -First 1).Kill()',
    '(Get-Process python)."Kill"()',
    'pwsh -Command "(Get-Process python).Kill()"',
    '(Get-Process python; Get-Process -Id 1234).Kill()',
    '(Get-Process python; Write-Output ready).Kill()',
    '$p=Get-Process python; (Write-Output $p).Kill()',
    '$p=Get-Process python; (Write-Output $p; Get-Process -Id 1234).Kill()',
    pytest.param('ionice pkill python', id='4323699213-ionice-command'),
    'ionice -c 3 killall python',
    'ionice -tc3 pkill python',
    'ionice -c2 -n0 pkill python',
    'ionice --class=idle --ignore -- pkill python',
    'ionice --classdata 0 --class best-effort pkill python',
    'ionice --classd=0 -c2 pkill python',
    'sudo -u nobody ionice -c3 stdbuf -oL pkill python',
    'ionice -c3 bash -c "pkill python"',
    pytest.param('$"pkill" python', id='4323699225-locale-quoted-executable'),
    'p$"k"ill python',
    '$"p"kill python',
    'p$"k"$\'ill\' python',
    'LC_ALL=C $"pkill" python',
    '$"Get-Process" python | $"Stop-Process"',
    '$"ionice" -c3 $"killall" python',
    '$"pwsh" -Command \'Stop-Process -Name python\'',
])
async def test_broad_receiver_paths_deny_before_approval_handlers(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('no approval card/event/submit may receive a broad stop')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='static receiver regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    '(Get-Process -Id 1234).Kill()',
    '$p=Get-Process -Id $ownedPid; ($p).Kill()',
    'Get-Process python; (Get-Process -Id 1234).Kill()',
    'Get-Process python; ($other).Kill()',
    '(Get-Process python).Id',
    '(Get-Process python).Kill',
    '(Get-Process python).Id.ToString()',
    '(Get-Process python); Write-Output ".Kill()"',
    '(Write-Output "Get-Process python").Kill()',
    'Write-Output "(Get-Process python).Kill()"',
    '$p=Get-Process python; (Write-Output \'$p\').Kill()',
    '$p=Get-Process python; (Write-Output "$p").Kill()',
    '$p=Get-Process python; (Write-Output \'$p\' $other).Kill()',
    'ionice -c3 kill -TERM 1234',
    'ionice -c3 pgrep python',
    'ionice --help pkill python',
    'ionice -V pkill python',
    'ionice -p 1234',
    'ionice -c3 -p 1234 5678',
    'ionice --pgid=1234 pkill python',
    'ionice --uid 1234 pkill python',
    'ionice -c pkill echo ready',
    'ionice -c3 echo "pkill python"',
    'echo "ionice -c3 pkill python"',
    '$"echo" $"pkill" python',
    '$"kill" -TERM 1234',
    '$"pgrep" python',
    'echo \'$"pkill" python\'',
    r'\$"pkill" python',
    r'echo "\$\"pkill\" python"',
])
def test_explicit_ids_queries_member_references_and_quoted_data_are_preserved(command):
    assert not broad_process_stop(command)
