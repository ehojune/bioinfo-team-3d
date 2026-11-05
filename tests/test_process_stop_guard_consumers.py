"""P1 consumer regressions: inert strings and fake approval handlers only."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param('Stop-Process -Id (Get-Process python).Id', id='4321811293-id-expression'),
    pytest.param('Stop-Process -Id ((Get-Process -Name python).Id)', id='id-nested'),
    pytest.param('spps -Id (gps python).Id', id='id-alias'),
    pytest.param('Stop-Process -Id:(Get-Process python).Id', id='id-attached'),
    pytest.param('kill -Id (Get-Process python).Id', id='id-consumer-alias'),
    pytest.param('xargs kill < <(pgrep python)', id='4321811304-process-substitution'),
    pytest.param('xargs kill < <(pidof python)', id='process-substitution-pidof'),
    pytest.param('xargs -r -n 1 kill -TERM < <(pgrep -f python)', id='process-substitution-options'),
    pytest.param('xargs kill 1234 < <(command -p pidof python)', id='process-substitution-mixed'),
    pytest.param('xargs --arg-file <(pgrep python) kill', id='process-substitution-arg-file'),
    pytest.param('bash -c "xargs kill < <(pidof python)"', id='process-substitution-shell'),
    pytest.param('echo <(pkill python)', id='process-substitution-executed-payload'),
    pytest.param('echo > >(killall python)', id='output-substitution-executed-payload'),
    pytest.param("Invoke-Expression 'Stop-Process -Name python'", id='4322020342-invoke-expression'),
    pytest.param("iex 'Stop-Process -Name python'", id='invoke-alias'),
    pytest.param("iex -Command 'Get-Process python | Stop-Process'", id='invoke-command'),
    pytest.param("Invoke-Expression -C 'pkill python'", id='invoke-command-abbreviation'),
    pytest.param("iex -Command:'Stop-Process -Name python'", id='invoke-command-attached'),
    pytest.param("iex -ErrorAction Stop 'Stop-Process -Name python'", id='invoke-common-option'),
    pytest.param("'Stop-Process -Name python' | iex", id='invoke-pipeline-literal'),
    pytest.param("Microsoft.PowerShell.Utility\\Invoke-Expression 'Stop-Process -Name python'", id='invoke-module'),
    pytest.param('watch -x pkill python', id='4322020348-watch-exec'),
    pytest.param('watch -n 1 pkill python', id='watch-interval'),
    pytest.param('watch -xn1 pkill python', id='watch-short-cluster'),
    pytest.param('watch --interval=1 --exec -- pkill python', id='watch-long'),
    pytest.param('watch --inter 1 pkill python', id='watch-option-abbreviation'),
    pytest.param('watch --differences=permanent pkill python', id='watch-optional-operand'),
    pytest.param("watch 'pkill python'", id='watch-shell-string'),
    pytest.param("watch -n1 'echo ready; pkill python'", id='watch-shell-list'),
    pytest.param('watch -x bash -c "pkill python"', id='watch-explicit-shell'),
    pytest.param('sudo -u nobody watch -q 2 -s shots pkill python', id='watch-wrapper-operands'),
    pytest.param('kill -- -1', id='4322020355-kill-broadcast'),
    pytest.param('kill 0', id='kill-current-group'),
    pytest.param('kill -TERM 0', id='kill-signal-group'),
    pytest.param('kill -s TERM -- -1234', id='kill-negative-group'),
    pytest.param('kill -n 15 0', id='kill-numeric-signal'),
    pytest.param('kill --signal=TERM -- -1', id='kill-long-signal'),
    pytest.param('kill 1234 0', id='kill-mixed-targets'),
    pytest.param('kill -- +0', id='kill-signed-zero'),
    pytest.param('kill -- -0001', id='kill-leading-zero'),
    pytest.param('Get-Process python | ForEach-Object { $_.Kill() }', id='4322020358-process-kill-method'),
    pytest.param('gps python | % { $_.Kill() }', id='method-alias'),
    pytest.param('Get-Process python | ForEach-Object { $PSItem.Kill($true) }', id='method-pipeline-item'),
    pytest.param('Get-Process python | ForEach-Object { echo ready; $_.Kill () }', id='method-later-statement'),
    pytest.param('$p = Get-Process python; $p.Kill()', id='method-selected-variable'),
    pytest.param('$p = Get-Process python; $q = $p; $q.Kill()', id='method-selected-copy'),
]


@pytest.mark.parametrize('command', CASES)
async def test_broad_consumers_never_reach_approval_handlers(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('broad stops must never reach a card/event/submit handler')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='static consumer regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    'Stop-Process -Id (Get-Process -Id 1234).Id',
    'Stop-Process -Id ((Get-Process -Id $ownedPid).Id)',
    'Write-Output (Get-Process python).Id',
    'xargs echo < <(pgrep python)', 'xargs kill < <(printf 1234)',
    'kill -TERM 1234 < <(pgrep python)', 'echo <(pgrep python)',
    "echo 'xargs kill < <(pgrep python)'", 'echo "<(pkill python)"',
    r'echo \<\(pkill python\)', "echo $'<(pkill python)'",
    "Invoke-Expression \"Write-Output 'pkill python'\"",
    "iex -ErrorVariable 'pkill python' 'Write-Output ready'",
    "'pkill python' | Write-Output", "Write-Output 'iex pkill python'",
    'watch -x echo "pkill python"', 'watch echo "pkill python"',
    "watch -x 'pkill python'", "watch -x echo '$(pkill python)'",
    'watch --help pkill python', 'watch -v pkill python',
    'watch -s pkill echo ready', 'watch --differences=pkill echo ready',
    'watch -n1 kill -TERM 1234',
    'kill -1 1234', 'kill -s TERM 1234', 'kill -n 9 1234',
    'kill --signal=TERM 1234', 'kill -l 0', 'kill -0 1234',
    'echo "kill -- -1"', 'printf "%s" "kill 0"',
    'Get-Process -Id 1234 | ForEach-Object { $_.Kill() }',
    '$p = Get-Process -Id $ownedPid; $p.Kill()',
    'Get-Process python | ForEach-Object { Write-Output "${_}.Kill()" }',
    "Get-Process python | ForEach-Object { Write-Output '$_.Kill()' }",
    '$p = Get-Process python; $p.Kill', '$p = Get-Process python; $pOther.Kill()',
])
def test_queries_explicit_pids_and_literal_data_remain_outside_broad_stop(command):
    assert not broad_process_stop(command)
