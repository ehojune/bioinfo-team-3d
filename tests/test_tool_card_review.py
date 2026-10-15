"""PR #638 review regressions; fake runner and isolated filesystem only."""
import pytest

from labhq.models import Task
from labhq.policy import evaluate_tool
from labhq.private_paths import credential_path
from labhq.pi_text import with_approval_copy
from tests.test_claude_staff_home import _staff
from tests.test_private_paths import _capture_runner, _runner_settings


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['', 'paper_skill', 'recruit'])
async def test_only_skill_build_tasks_read_paper2agent(tmp_path, monkeypatch, kind):
    home, staff, _, _, _, _ = _staff(tmp_path, monkeypatch)
    settings = _runner_settings(tmp_path, [str(staff), str(tmp_path / 'talent_dir')])
    settings.engines.claude_code.env = {'CLAUDE_CONFIG_DIR': str(staff)}
    settings.recruit.agent_id = 'worker'
    runner, seen = _capture_runner(settings, monkeypatch)
    # Bypass preparation/downloads, while exercising the real runner's permission setup.
    task = Task(id='build', agent_id='worker', prompt='fixture', meta={'kind': kind})
    build = tmp_path / 'talent_dir' / 'build'
    assert (await runner._run_task(task, build)).ok
    ctx = seen['ctx']
    skill = staff / 'labhq-paper2agent' / 'skills' / 'paper2agent'
    def gate(tool, path):
        data = {'command': f'cat "{path}"'} if tool == 'Bash' else {'file_path': str(path)}
        return evaluate_tool(tool, data, settings.policy, allowed_roots=[str(ctx.workdir)],
                             workdir=str(ctx.workdir), private_paths=ctx.private_paths,
                             private_open_reads=ctx.private_open_reads, home=str(home), environ={}).action
    assert gate('Bash', skill / 'SKILL.md') == ('allow' if kind else 'ask')
    assert gate('Read', skill / 'SKILL.md') == ('allow' if kind else 'deny')
    assert gate('Read', build / 'result.txt') == 'allow'
    for path in (staff / 'skills' / 'guide.md', skill.parent / 'other' / 'SKILL.md'):
        assert gate('Bash', path) == 'ask'
        assert gate('Read', path) == 'deny'
    for path in (staff / '.credentials.json', skill / 'auth.json', skill / '.ssh' / 'config'):
        assert gate('Read', path) == 'deny'
        assert gate('Bash', path) == 'ask'
    assert gate('Write', skill / 'SKILL.md') == 'deny'


@pytest.mark.parametrize('parent', ['auth', 'oauth', 'token', 'credentials'])
def test_normal_parent_names_are_not_credentials(parent):
    assert not credential_path(f'/srv/{parent}/runs/guide.md')
    assert not credential_path(f'C:\\srv\\{parent}\\runs\\guide.md')
    assert credential_path(f'/srv/{parent}/runs/access_token.json')
    assert credential_path(f'/srv/{parent}/runs/.ssh/config')


def test_auto_card_explains_why_it_still_asks():
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': 'Bash', 'tool_input': {'command': 'cat private.txt'},
        'permission_mode': 'auto', 'policy_reason': 'policy.private_paths'}})
    assert 'auto여도 이 규칙 때문에 묻습니다' in card['pi_reason']
    assert '개인 경로 보호' in card['pi_reason']
