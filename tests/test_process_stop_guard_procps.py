"""procps command strings only; neither utilities nor process fixtures are executed."""
import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.process_stop_guard import DENIAL, broad_process_stop
from labhq.runner.approvals import Broker
from labhq.settings import PolicySettings


CASES = [
    pytest.param('skill -c python', id='4321588842-skill'),
    pytest.param('skill -KILL -c python', id='skill-signal'),
    pytest.param('skill -9 --command=python', id='skill-long-command'),
    pytest.param('skill -vcpython', id='skill-short-cluster'),
    pytest.param('skill --comm python', id='skill-long-abbreviation'),
    pytest.param('skill python', id='skill-implicit-name'),
    pytest.param('skill -c -n', id='skill-no-action-is-operand'),
    pytest.param('skill --command=--help', id='skill-help-is-operand'),
    pytest.param('skill -c --version', id='skill-version-is-operand'),
    pytest.param('skill -c python -p 1234', id='skill-mixed-selectors-conservative'),
    pytest.param('sudo -u nobody /usr/bin/skill -c python', id='skill-path-wrapper'),
    pytest.param('bash -c "skill -KILL -c python"', id='skill-shell-wrapper'),
    pytest.param('kill $(pidof python)', id='4321588847-pidof-substitution'),
    pytest.param('kill -TERM "$(pidof -x python)"', id='pidof-quoted'),
    pytest.param('pidof python | xargs kill', id='pidof-pipeline'),
    pytest.param('pidof python | xargs -r kill -TERM 1234', id='pidof-additional-targets'),
    pytest.param('kill $(command -p /usr/bin/pidof python)', id='pidof-wrapper'),
    pytest.param('kill "$(echo "$(pidof python)")"', id='pidof-nested'),
    pytest.param('kill `pidof python`', id='pidof-backquote'),
    pytest.param('skill -p $(pidof python)', id='pidof-skill-consumer'),
    pytest.param('pidof python | xargs skill -p 1234', id='pidof-xargs-skill'),
    pytest.param('kill $(skill -n -c python)', id='skill-query-selected-output'),
    pytest.param('skill --no-action --command python | xargs kill', id='skill-query-pipeline'),
]


@pytest.mark.parametrize('command', CASES)
@pytest.mark.asyncio
async def test_procps_name_selection_is_denied_without_card_or_handler(command):
    assert broad_process_stop(command)
    for tool in ('Bash', 'PowerShell'):
        decision = evaluate_tool(tool, {'command': command}, PolicySettings(), workdir='.')
        assert decision.action == 'deny' and decision.reason == DENIAL

    async def forbidden(*args):
        pytest.fail('a broad stop must not reach card/event/submit handlers')

    for detail in ({'command': command}, {'tool_input': {'command': command}}):
        broker = Broker(0, forbidden, forbidden, forbidden)
        result = await broker.request_approval(ApprovalRequest(
            kind='tool_permission', summary='literal procps regression', detail=detail))
        assert result == {'approved': False, 'note': DENIAL, 'state': 'denied'}
        assert not broker.pending and not broker.pending_requests


@pytest.mark.parametrize('command', [
    'skill -n -c python', 'skill -KILL -n -c python',
    'skill --no-action --command python', 'skill -nvcpython',
    'skill --help', 'skill --version', 'skill -h', 'skill -V',
    'skill -l', 'skill -L', 'skill --list', 'skill --table',
    'skill -c python --help', 'skill --no-a -c python',
    'skill -p 1234', 'skill -KILL -p1234', 'skill --pid 1234', 'skill 1234',
    'skill -p $ownedPid', 'skill -n -p1234 | xargs kill',
    'pidof python', 'pidof -h', 'echo $(pidof python)',
    'pidof python | xargs echo kill', 'pidof python | kill -TERM 1234',
    'echo "skill -c python"', "echo 'kill $(pidof python)'",
    'kill $(printf 1234)', 'skill -p $(printf 1234)',
])
def test_query_options_data_and_explicit_pid_cleanup_remain_outside_name_stop(command):
    assert not broad_process_stop(command)
