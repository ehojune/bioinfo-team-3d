"""#634: managed reads, truthful explanations and recruiter identity; no live services."""
import os
from types import SimpleNamespace

import pytest

from labhq.models import ApprovalRequest, Task
from labhq.pi_text import with_approval_copy
from labhq.policy import evaluate_tool
from labhq.private_paths import resolve_private_paths
from labhq.settings import Settings
from tests.test_private_paths import _link_dir


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / 'staff'
    build = root / 'talent' / 'paper' / 'build' / 'task'
    skills = root / 'claude' / 'labhq-paper2agent' / 'skills' / 'paper2agent'
    for p in (build, skills, root / 'private', root / 'runs'):
        p.mkdir(parents=True)
    cfg = Settings()
    cfg.runner.talent_dir = str(root / 'talent')
    cfg.runner.workspace_root = str(root / 'runs')
    cfg.engines.claude_code.env = {'CLAUDE_CONFIG_DIR': str(root / 'claude')}
    cfg.policy.private_paths = [str(root)]
    return cfg, root, build, skills


def gate(layout, tool, data):
    cfg, root, build, skills = layout
    # This fixture models the runner's explicit paper-skill build grant.
    private = resolve_private_paths(cfg, cwd=build, open_reads=[str(skills)])
    return evaluate_tool(tool, data, cfg.policy, allowed_roots=[str(build)], workdir=str(build),
                         private_paths=private.paths, private_open_reads=private.open_reads,
                         private_enabled=private.enabled)


@pytest.mark.parametrize('which', ['build', 'skills', 'runs'])
@pytest.mark.parametrize('tool', ['Read', 'Bash'])
def test_managed_reads_need_no_approval(layout, which, tool):
    _, root, build, skills = layout
    path = {'build': build, 'skills': skills, 'runs': root / 'runs'}[which] / 'guide.md'
    path.write_text('public guide', encoding='utf-8')
    data = {'file_path': str(path)} if tool == 'Read' else {'command': f'cat "{path}"'}
    assert gate(layout, tool, data).action == 'allow'


def test_reported_compound_read_needs_no_approval(layout):
    _, _, build, skills = layout
    cmd = f'cd "{build}" && ls -la && find . -maxdepth 3 | head -50; which uv python; ls "{skills}"'
    assert gate(layout, 'Bash', {'command': cmd}).action == 'allow'


@pytest.mark.parametrize('relative', ['claude/.credentials.json', 'claude/auth.json', 'claude/token.json',
                                    'claude/labhq-paper2agent/skills/paper2agent/auth.json',
                                    'claude/labhq-paper2agent/skills/paper2agent/access_token.json',
                                    'talent/paper/build/task/.env', 'private/diary.md'])
@pytest.mark.parametrize('tool', ['Read', 'Bash'])
def test_secrets_and_personal_paths_stay_closed(layout, relative, tool):
    path = layout[1] / relative
    path.write_text('fixture only', encoding='utf-8')
    data = {'file_path': str(path)} if tool == 'Read' else {'command': f'cat "{path}"'}
    assert gate(layout, tool, data).action == ('deny' if tool == 'Read' else 'ask')


@pytest.mark.parametrize('tool', ['Read', 'Bash'])
@pytest.mark.parametrize('escape', ['dotdot', 'link', 'link_dotdot'])
def test_managed_path_escape_stays_closed(layout, tool, escape):
    _, root, build, skills = layout
    (root / 'private' / 'nested').mkdir()
    _link_dir(skills / 'alias', root / 'private' / 'nested')
    paths = {'dotdot': str(skills / '..' / '..' / 'private' / 'diary.md'),
             'link': str(skills / 'alias' / 'secret.md'),
             'link_dotdot': str(skills / 'alias' / '..' / 'diary.md')}
    path = paths[escape]
    data = {'file_path': path} if tool == 'Read' else {'command': f'cat "{path}"'}
    assert gate(layout, tool, data).action == ('deny' if tool == 'Read' else 'ask')


@pytest.mark.parametrize('template', ['rm -rf "{path}"', 'touch "{path}"', 'cat "{path}" > out',
                                     'find "{path}" -exec cat {{}} +', 'ls "{path}"; python script.py',
                                     'cat "{path}"$(echo /auth.json)'])
def test_read_exception_does_not_cover_writes_or_unknown_shell(layout, template):
    assert gate(layout, 'Bash', {'command': template.format(path=layout[3])}).action != 'allow'


def test_windows_case_and_separators(layout):
    if os.name != 'nt':
        pytest.skip('host case folding')
    path = str(layout[3] / 'guide.md').upper().replace('\\', '/')
    assert gate(layout, 'Bash', {'command': f'cat "{path}"'}).action == 'allow'
    path = str(layout[1] / 'private' / 'diary.md').upper().replace('\\', '/')
    assert gate(layout, 'Bash', {'command': f'cat "{path}"'}).action == 'ask'


def test_compound_card_explains_what_where_why_rule_risk_and_outcome(layout):
    _, _, build, skills = layout
    card = with_approval_copy({'kind': 'tool_permission', 'summary': 'Bash: policy.private_paths', 'detail': {
        'tool_name': 'Bash', 'policy_reason': 'Bash names a PI personal path (policy.private_paths)',
        'tool_input': {'command': f'cd "{build}" && ls -la && find . -maxdepth 3 | head -50; which uv python; ls "{skills}"'},
        'task_context': {'kind': 'paper_skill', 'paper_title': 'Example paper', 'step_id': 'paper_skill_x'},
        'path_labels': {str(build): '이 작업의 빌드 폴더', str(skills): 'Paper2Agent 스킬 참고 자료'}}})
    text = '\n'.join(str(card.get(k, '')) for k in ('pi_summary', 'pi_reason', 'pi_outcome'))
    for word in ('목록', '프로그램 위치', '빌드 폴더', '스킬 참고 자료', 'Example paper', '자문 스킬',
                 '개인 경로', '중간', '읽기', '승인하면', '거절하면'):
        assert word in text
    assert str(build) not in text
    assert not card.get('pi_needs_interpretation')


@pytest.mark.parametrize('command,word', [('rm -rf results', '삭제'), ('mkdir results', '쓰기'),
                                        ('pip install example', '설치'), ('curl https://example.org', '네트워크')])
def test_action_risk_categories(command, word):
    card = ApprovalRequest(kind='tool_permission', summary='approval',
                           detail={'tool_name': 'Bash', 'tool_input': {'command': command}})
    assert word in card.pi_summary + card.pi_reason
    assert '위험' in card.pi_reason


@pytest.mark.asyncio
async def test_runner_adds_trusted_paper_context_and_requester(layout):
    from labhq.runner.daemon import Runner
    from labhq.models import AgentSpec
    cfg, _, build, _ = layout
    task = Task(id='build', agent_id='recruiter', prompt='prepare paper skill', meta={'kind': 'paper_skill',
                'paper_skill': {'title': 'Example paper'}, 'step_id': 'paper_skill_x'})
    agent = AgentSpec(id='recruiter', name='비버 인사팀', role='recruiter', permission_mode='auto')
    events = []
    async def emit_all(items):
        events.extend(items)
    runner = SimpleNamespace(s=cfg, task_req={'build': 'r'}, approval_tasks={}, emit_all=emit_all,
                             workspaces={'build': SimpleNamespace(task=task, agent=agent, dir=build)})
    req = ApprovalRequest(task_id='build', agent_id='recruiter', kind='tool_permission', summary='approval',
                          detail={'tool_name': 'Bash', 'tool_input': {'command': 'ls .'}})
    await Runner._on_approval(runner, req)
    card = events[0].data
    assert card['agent_id'] == 'recruiter'
    assert card['detail']['requester_name'] == '비버 인사팀'
    assert 'Example paper' in card['pi_reason']
    assert card['detail']['permission_mode'] == 'auto'
    assert 'auto여도 이 규칙 때문에 묻습니다' in card['pi_reason']


def test_cli_approvals_uses_same_copy(monkeypatch, capsys):
    from labhq import cli
    card = ApprovalRequest(id='a', kind='tool_permission', summary='raw command', detail={
        'tool_name': 'Bash', 'tool_input': {'command': 'ls .'}, 'requester_name': '비버 인사팀'}).model_dump()
    monkeypatch.setattr(cli, '_api', lambda *args, **kwargs: [card])
    cli.main(['approvals'])
    text = capsys.readouterr().out
    for field in ('pi_summary', 'pi_reason', 'pi_outcome'):
        assert card[field] in text
    assert '비버 인사팀' in text and 'raw command' not in text
    cli.main(['approvals', '--details'])
    assert 'raw command' in capsys.readouterr().out


def test_known_templates_do_not_schedule_interpreter_but_opaque_commands_do():
    from labhq.pi_language import message_source
    for command, needed in [('ls -la', False), ('python -c "run()"', True)]:
        card = ApprovalRequest(kind='tool_permission', summary='Bash approval',
                               detail={'tool_name': 'Bash', 'tool_input': {'command': command}}).model_dump()
        assert card['pi_needs_interpretation'] is needed
        assert bool(message_source({'type': 'approval.requested', 'data': card})) is needed


def test_managed_reads_do_not_open_private_parent_when_workdir_is_kept(layout):
    cfg, root, build, _ = layout
    private = resolve_private_paths(cfg, keep=[build], cwd=build)
    assert str(root) in private.paths
    assert not private.skipped
    assert evaluate_tool('Read', {'file_path': str(root / 'private' / 'diary.md')}, cfg.policy,
                         workdir=str(build), private_paths=private.paths,
                         private_open_reads=private.open_reads).action == 'deny'


def test_linked_managed_root_never_grants_its_destination(layout):
    cfg, root, _, skills = layout
    alias = root / 'linked-talent'
    _link_dir(alias, root / 'private')
    cfg.runner.talent_dir = str(alias)
    private = resolve_private_paths(cfg, cwd=skills)
    assert str(root / 'private') not in private.open_reads
    assert gate(layout, 'Bash', {'command': f'cat "{alias}/diary.md"'}).action == 'ask'


def test_explicit_private_child_beats_managed_read_root(layout):
    cfg, _, _, skills = layout
    private = skills / 'private'
    private.mkdir()
    cfg.policy.private_paths.append(str(private))
    assert gate(layout, 'Bash', {'command': f'cat "{private}/diary.md"'}).action == 'ask'
    assert gate(layout, 'Read', {'file_path': str(private / 'diary.md')}).action == 'deny'


def test_existing_own_file_write_grant_does_not_open_all_managed_folders(layout):
    _, root, build, skills = layout
    assert gate(layout, 'Write', {'file_path': str(build / 'out.md'), 'content': 'x'}).action == 'allow'
    for path in (skills / 'out.md', root / 'talent' / 'other.md', build / '.credentials.json'):
        assert gate(layout, 'Write', {'file_path': str(path), 'content': 'x'}).action == 'deny'


@pytest.mark.asyncio
@pytest.mark.parametrize('engine_name', ['claude_code', 'codex'])
@pytest.mark.parametrize('plugin', [False, True])
async def test_runner_loaded_skills_and_plugins_allow_literal_shell_reads(tmp_path, monkeypatch, engine_name, plugin):
    from labhq.models import Engine
    from tests.test_plugin_private_reads import _run
    ctx, home, skill, secrets = await _run(tmp_path, monkeypatch, Engine(engine_name), plugin)
    for path, action in [(skill / 'SKILL.md', 'allow'), (secrets[0], 'ask')]:
        result = evaluate_tool('Bash', {'command': f'cat "{path}"'}, ctx.settings.policy,
                               allowed_roots=[str(ctx.workdir)], workdir=str(ctx.workdir),
                               private_paths=ctx.private_paths, private_open_reads=ctx.private_open_reads,
                               private_enabled=ctx.private_enabled, home=str(home))
        assert result.action == action
