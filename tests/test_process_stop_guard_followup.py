"""P1 follow-ups: strings only, with policy checks and fake approval handlers."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param('coproc pkill python', id='4318107944-coproc'),
    pytest.param('coproc command -p pkill python', id='coproc-prefix'),
    pytest.param("bash -c 'coproc killall python'", id='coproc-shell'),
    pytest.param("eval 'pkill python'", id='4318556373-eval'),
    pytest.param("eval 'pkill' 'python'", id='eval-join-args'),
    pytest.param('eval pkill python', id='eval-unquoted'),
    pytest.param("eval -- 'pkill python'", id='eval-end-options'),
    pytest.param('eval ' * 12 + 'pkill python', id='eval-nesting-bound'),
    pytest.param('echo `pkill python`', id='4318556385-backquote'),
    pytest.param('echo "prefix `pkill python` suffix"', id='backquote-double-quoted'),
    pytest.param('echo `eval "pkill python"`', id='backquote-eval'),
    pytest.param("eval 'echo `pkill python`'", id='eval-deferred-backquote'),
    pytest.param(r'echo `echo \`pkill python\``', id='backquote-nested'),
    pytest.param('timeout 1 pkill python', id='4319098856-timeout'),
    pytest.param('timeout -s TERM -k 2 1 pkill python', id='timeout-separate-operands'),
    pytest.param('timeout -vk2 --signal=TERM 1 /bin/pkill python', id='timeout-attached-cluster'),
    pytest.param('timeout --preserve-status --foreground --kill-after 2 1 pkill python', id='timeout-long'),
    pytest.param('timeout --sig TERM --kill-a=2 -- 1 pkill python', id='timeout-long-abbreviations'),
    pytest.param('nice pkill python', id='nice'),
    pytest.param('nice -n 5 pkill python', id='nice-separate'),
    pytest.param('nice -n5 pkill python', id='nice-attached'),
    pytest.param('nice --adjustment=5 pkill python', id='nice-long'),
    pytest.param('nice --adjust 5 pkill python', id='nice-long-abbreviation'),
    pytest.param('nice -5 pkill python', id='nice-legacy'),
    pytest.param('nice --5 pkill python', id='nice-legacy-negative'),
    pytest.param('setsid pkill python', id='setsid'),
    pytest.param('setsid -fw -- pkill python', id='setsid-flags'),
    pytest.param('setsid --fork --wait pkill python', id='setsid-long'),
    pytest.param('env -i timeout -k2 1 nice -n5 setsid -f pkill python', id='execution-wrapper-chain'),
    pytest.param(r'find . -exec pkill python \;', id='4319098865-find-exec'),
    pytest.param(r'find . -execdir killall python \;', id='find-execdir'),
    pytest.param("find . -exec pkill '{}' +", id='find-batch-quoted'),
    pytest.param('find . -exec pkill {} +', id='find-batch-placeholder'),
    pytest.param(r"find . \( -name '*.tmp' -o -name '*.part' \) -exec pkill python \;", id='find-expression'),
    pytest.param(r"find . -exec sh -c 'pkill python' sh '{}' \;", id='find-shell'),
    pytest.param(r'find . -exec echo ready \; -execdir pkill python \;', id='find-multiple-actions'),
    pytest.param(r'find . -ok pkill python \;', id='find-ok'),
    pytest.param("dash -c 'pkill python'", id='4320283756-dash'),
    pytest.param("/bin/dash -ec 'killall python'", id='dash-cluster'),
    pytest.param("trap 'pkill python' EXIT", id='4320283761-trap'),
    pytest.param("trap -- 'killall python' EXIT ERR", id='trap-end-options'),
    pytest.param("trap -- '-; pkill python' EXIT", id='trap-dash-handler-after-end-options'),
    pytest.param("trap 'eval pkill python' EXIT", id='trap-eval'),
    pytest.param('pgrep python | xargs kill', id='4320283767-pgrep'),
    pytest.param('pgrep -f python | xargs -r kill -TERM', id='pgrep-signal'),
    pytest.param('pgrep python | xargs -n1 /bin/kill', id='pgrep-xargs-options'),
    pytest.param('pgrep python | xargs kill -TERM 1234', id='pgrep-appended-pids'),
    pytest.param("$'pkill' python", id='4320283775-ansi-c'),
    pytest.param("p$'k'ill python", id='ansi-adjacent'),
    pytest.param(r"$'\x70kill' python", id='ansi-hex'),
    pytest.param(r"p$'\153'ill python", id='ansi-octal'),
    pytest.param(r"p$'\u006b'ill python", id='ansi-unicode'),
    pytest.param(r"p$'\U0000006b'ill python", id='ansi-long-unicode'),
    pytest.param(r"$'/usr/bin/\x70kill' python", id='ansi-path'),
    pytest.param(r"$'taskkill' $'/I\x4d' python.exe", id='ansi-taskkill-option'),
    pytest.param(r"eval $'pkill\x20python'", id='ansi-eval-payload'),
    pytest.param(r"dash -c $'pkill\x20python'", id='ansi-shell-payload'),
]


@pytest.mark.parametrize('command', CASES)
@pytest.mark.asyncio
async def test_review_followup_denied_before_policy_or_approval(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('denied commands must not reach card/event/submit handlers')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='literal P1 follow-up', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    'coproc echo pkill python', 'coproc kill -TERM 1234',
    "eval 'echo pkill python'", "eval 'kill -TERM 1234'",
    "printf '%s' 'eval pkill python'", "echo '`pkill python`'",
    r'echo \`pkill python\`', r'echo "\`pkill python\`"',
    'echo `echo pkill python`', 'echo `kill -TERM 1234`',
    "trap -p 'pkill python'", 'trap -l', 'trap - EXIT', "trap '' EXIT",
    "trap 'echo pkill python' EXIT", "trap 'kill -TERM 1234' EXIT",
    'timeout --help pkill python', 'nice --version pkill python', 'setsid --help pkill python',
    'timeout -s pkill 1 echo ready', 'nice -n pkill echo ready',
    'timeout 1 echo pkill python', 'nice echo pkill python', 'setsid echo pkill python',
    'timeout 1 kill -TERM 1234', 'nice -n5 kill -TERM 1234', 'setsid -w kill -TERM 1234',
    r'find . -name -exec -print', r"find . -printf '-exec pkill python;'",
    r'find --help -exec pkill python \;', r'find . -exec pkill python \; --help',
    r'find -version -exec pkill python \;',
    r"find . -exec printf '%s' 'pkill python' \;", r'find . -exec echo -exec pkill python \;',
    r'find . -exec kill -TERM 1234 \;', 'find . -name pkill -print',
    "dash -c 'echo pkill python'", "dash -c 'kill -TERM 1234'",
    "dash -c 'echo safe' 'pkill python'", 'pgrep python', 'pgrep python | xargs echo kill',
    'pgrep python | kill -TERM 1234', "echo 'pgrep python | xargs kill'",
    "echo $'pkill python'", "printf '%s' $'pkill python'", "$'echo' pkill python",
    "$'pkill-suffix' python", '"$\'pkill\'" python',
    r"$'\cß' python", r"$'\Uffffffff' python",
    'Stop-Process -Force -Id 1234',
])
def test_followup_data_option_operands_and_pids_stay_outside_name_stop(command):
    assert not broad_process_stop(command)


def test_explicit_powershell_name_after_switch_is_already_denied():
    # The reported positional Name example is not a Name parameter binding.
    assert broad_process_stop('Stop-Process -Force -Name python')
