"""PR #638: reproduce the four review findings without live engines or services."""
import os
from types import SimpleNamespace

import pytest

from labhq.adapters import codex
from labhq.pi_text import with_approval_copy
from labhq.policy import evaluate_tool
from labhq.private_paths import resolve_private_paths
from labhq.settings import DataZone, PolicySettings
from labhq.tool_command import commands
from labhq.tool_explanation import explain
from tests.test_private_paths import _link_dir
from tests.test_tool_card_explain import gate, layout
from tests.test_private_paths import request_gate_1456, request_paths_1456


@pytest.mark.parametrize('command', ['wc -l inputs/precedent/*', 'cat "unterminated'])
def test_unproven_read_card_does_not_claim_personal_access(request_paths_1456, command):
    command = f'cd "{request_paths_1456[0].as_posix()}"; {command}'
    decision = request_gate_1456(request_paths_1456, 'Bash', {'command': command})
    assert decision.action == 'ask'
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': 'Bash', 'policy_reason': decision.reason, 'tool_input': {'command': command}}})
    assert '읽기 범위 미확인 — 개인 파일 접근 여부를 판단하지 못했습니다. 명령을 확인해 주세요.' in card['pi_reason']
    assert '개인 경로 접근 여부와 실행 범위 미확인' in card['pi_reason']
    assert '위험 등급: 낮음' not in card['pi_reason']
    assert '위험 등급: 높음' not in card['pi_reason']
    assert '접근을 감지했습니다' not in card['pi_reason']


def test_unproven_read_card_preserves_restricted_warning():
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': 'Bash', 'policy_reason': 'Bash read scope is ambiguous (policy.private_paths)',
        'tool_input': {'command': 'wc -l *.txt'}, 'internal_row_output': True}})
    assert '위험 등급: 높음' in card['pi_reason']
    assert '내부 자료 원행 노출' in card['pi_reason']


def test_unproven_read_card_preserves_known_destructive_risk():
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': 'Bash', 'policy_reason': 'Bash read scope is ambiguous (policy.private_paths)',
        'tool_input': {'command': 'rm -r outputs'}}})
    assert '위험 등급: 높음' in card['pi_reason']


@pytest.mark.parametrize('operand', [r'/runs/Private\ Data/diary.txt',
                                    '"/runs/Private Data/diary.txt"',
                                    '/runs/Private" Data"/diary.txt'])
def test_posix_space_is_one_operand(operand):
    assert commands('cat ' + operand) == [['cat', '/runs/Private Data/diary.txt']]


@pytest.mark.parametrize('spelling', ['escaped', 'quoted', 'joined'])
def test_private_space_child_never_inherits_managed_grant(layout, spelling):
    cfg, root, _, _ = layout
    private = root / 'runs' / 'Private Data'
    private.mkdir()
    cfg.policy.private_paths.append(str(private))
    path = (private / 'diary.txt').as_posix()
    operand = {'escaped': path.replace(' ', r'\ '), 'quoted': f'"{path}"',
               'joined': path.replace('Private Data', 'Private" Data"')}[spelling]
    assert gate(layout, 'Bash', {'command': 'cat ' + operand}).action == 'ask'


@pytest.mark.parametrize('command', ['cat "unterminated', 'cat public\\', 'cat "${UNKNOWN}"'])
def test_uninterpretable_managed_read_asks(layout, command):
    assert gate(layout, 'Bash', {'command': command}).action == 'ask'


@pytest.mark.parametrize('command', [r'cat C:\staff\private\diary.txt', 'cat C:diary.txt'])
def test_ambiguous_windows_drive_does_not_receive_read_grant(layout, command):
    assert gate(layout, 'Bash', {'command': command}).action == 'ask'


@pytest.mark.parametrize('policy_reason', ['shell references restricted zone',
                                        'policy.data_zones: restricted', 'policy.private_paths'])
@pytest.mark.parametrize('command', ['cat sample.txt', 'head -5 sample.txt', 'touch sample.txt',
                                    'rm sample.txt', 'curl https://example.org', 'pip install example'])
def test_sensitive_path_card_has_high_risk(policy_reason, command):
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': 'Bash', 'policy_reason': policy_reason, 'tool_input': {'command': command}}})
    assert '위험 등급: 높음' in card['pi_reason']
    assert '노출' in card['pi_reason'] and '확인 필요' in card['pi_reason']
    assert '위험 등급: 낮음' not in card['pi_reason']


@pytest.mark.parametrize('tool', ['Read', 'Bash'])
def test_card_uses_real_restricted_path_decision(tmp_path, tool):
    zone = tmp_path / 'restricted'
    zone.mkdir()
    path = (zone / 'sample.txt').as_posix()
    policy = PolicySettings(data_zones=[DataZone(path=str(zone), level='restricted')])
    data = {'file_path': path} if tool == 'Read' else {'command': f'cat "{path}"'}
    decision = evaluate_tool(tool, data, policy, workdir=str(tmp_path), private_enabled=False)
    assert decision.action == ('deny' if tool == 'Read' else 'ask')
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {
        'tool_name': tool, 'policy_reason': decision.reason, 'tool_input': data}})
    assert '위험 등급: 높음' in card['pi_reason'] and '통제 데이터 노출' in card['pi_reason']


def test_powershell_literal_separators_survive_bash_tokenizer_fix():
    command = r'New-Item -ItemType Directory -Force C:\work\results'
    action, _, _ = explain({'tool_name': 'PowerShell', 'tool_input': {'command': command}})
    assert r'C:\work\results' in action


@pytest.mark.parametrize('read_only,sandbox', [(True, 'workspace-write'), (False, 'read-only'),
                                             (False, 'workspace-write')])
def test_codex_profiles_keep_same_scoped_read_grants(layout, monkeypatch, read_only, sandbox):
    cfg, root, build, skills = layout
    private_child = root / 'runs' / 'private'
    private_child.mkdir()
    cfg.policy.private_paths.append(str(private_child))
    private = resolve_private_paths(cfg, cwd=build, open_reads=[str(skills)])
    cfg.engines.codex.windows_sandbox = 'elevated'
    cfg.engines.codex.isolate_user_config = True
    monkeypatch.setattr(codex, '_is_windows', lambda: True)
    ctx = SimpleNamespace(agent=SimpleNamespace(id='recruiter', sandbox=sandbox), read_only=read_only,
                          read_only_inputs=[], native_deny_read_paths=list(private.paths),
                          workdir=build, private_paths=private.paths, private_open_reads=private.open_reads)
    flags = codex.CodexAdapter(cfg).sandbox_flags(ctx)
    profile = next(v for v in flags if v.startswith('permissions.labhq_staff='))
    for path in (build, skills):
        assert f'{codex._toml(str(path))} = "read"' in profile
    for path in (root / 'runs', root / 'talent', build.parent):
        assert f'{codex._toml(str(path))} = "read"' not in profile
    for path in (root, private_child):
        assert f'{codex._toml(str(path))} = "deny"' in profile
    assert f'{codex._toml(str(skills.parent))} = "read"' not in profile
    assert f'{codex._toml(str(skills))} = "write"' not in profile


@pytest.mark.parametrize('tool', ['Bash', 'Read', 'Write'])
@pytest.mark.parametrize('escape', ['dotdot', 'link', 'link_dotdot'])
def test_card_label_does_not_hide_path_escape(layout, tool, escape):
    _, root, _, skills = layout
    (root / 'private' / 'nested').mkdir()
    _link_dir(skills / 'alias', root / 'private' / 'nested')
    paths = {'dotdot': skills / '..' / '..' / 'private' / 'diary.md',
             'link': skills / 'alias' / 'secret.md',
             'link_dotdot': skills / 'alias' / '..' / 'diary.md'}
    path = paths[escape].as_posix()
    data = {'command': f'cat "{path}"'} if tool == 'Bash' else {'file_path': path}
    action, _, _ = explain({'tool_name': tool, 'tool_input': data,
                           'path_labels': {str(skills): 'Paper2Agent 스킬 참고 자료'}})
    assert 'Paper2Agent' not in action


def test_card_normalizes_relative_path_against_workdir(layout):
    _, _, build, skills = layout
    action, _, _ = explain({'tool_name': 'Read', 'tool_input': {'file_path': './guide.md'},
                           'workdir': str(build), 'path_labels': {str(build / '.'): '빌드 폴더'}})
    assert '빌드 폴더' in action
    if os.name == 'nt':
        action, _, _ = explain({'tool_name': 'Read', 'tool_input': {
            'file_path': (skills / 'guide.md').as_posix().upper()},
            'path_labels': {str(skills): '스킬 참고 자료'}})
        assert '스킬 참고 자료' in action


@pytest.mark.parametrize('tool, command, policy_reason', [
    ('PowerShell', 'New-Item outputs/sub', 'PowerShell reaches a PI personal path (policy.private_paths) through a link'),
    ('Bash', 'mkdir -p outputs/sub', 'Bash read scope is ambiguous (policy.private_paths)'),
])
def test_task_folder_write_card_is_not_a_personal_path_risk(tool, command, policy_reason):
    """#1445 r22: a write the runner grouped as the task folder is shown as one, without a high risk."""
    detail = {'tool_name': tool, 'policy_reason': policy_reason, 'tool_input': {'command': command}}
    card = with_approval_copy({'kind': 'tool_permission', 'detail': {**detail, 'grant_category': 'workspace'}})
    assert '작업 폴더 안에서 파일을 만들거나 고칩니다.' in card['pi_reason']
    assert '위험 등급: 높음' not in card['pi_reason'] and '개인' not in card['pi_reason']
    # Without the runner's category the same reason keeps its existing warning.
    plain = with_approval_copy({'kind': 'tool_permission', 'detail': detail})
    assert '작업 폴더 안에서 파일을 만들거나 고칩니다.' not in plain['pi_reason']


def test_task_folder_read_card_does_not_claim_a_write():
    detail = {'tool_name': 'Bash', 'policy_reason': 'Bash read scope is ambiguous (policy.private_paths)',
              'tool_input': {'command': 'grep -n x outputs/a.txt'}, 'grant_category': 'workspace'}
    card = with_approval_copy({'kind': 'tool_permission', 'detail': detail})
    assert '작업 폴더 안의 파일을 다룹니다.' in card['pi_reason'] and '만들거나 고칩니다' not in card['pi_reason']
    assert '위험 등급: 높음' not in card['pi_reason']
