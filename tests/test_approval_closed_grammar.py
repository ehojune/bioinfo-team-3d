"""PR #659 P1 reproductions: classify only; never execute the submitted shell."""
import os
from pathlib import Path

import pytest

from tests.test_tool_card_explain import gate, layout
from tests.test_private_paths import _link_dir
from labhq.policy import evaluate_tool


def path_gate(layout, command, path):
    cfg, root, work, _ = layout
    return evaluate_tool('Bash', {'command': command}, cfg.policy, workdir=str(work),
                         allowed_roots=[str(work)], private_paths=[str(root)],
                         private_enabled=True, private_open_reads=[str(work)],
                         environ={**os.environ, 'PATH': path})


@pytest.mark.parametrize('operand', [r'Data:\x', 'Env:PATH', 'HKLM:', 'Registry::',
                                     r'FileSystem::C:\x', 'local:stream'])
def test_powershell_provider_operand_requires_card(layout, operand):
    decision = gate(layout, 'PowerShell', {'command': 'Remove-Item -Recurse -Force ' + operand})
    assert decision.action == 'ask'
    assert decision.reason.startswith('문법 밖:')


@pytest.mark.parametrize('prefix', ['bin', '.', ''])
def test_workspace_path_executable_requires_card(layout, prefix):
    work = layout[2]
    folder = work / prefix
    folder.mkdir(exist_ok=True)
    executable = folder / ('touch.exe' if os.name == 'nt' else 'touch')
    executable.write_text('fixture only; never executed', encoding='utf-8')
    executable.chmod(0o755)
    path = (str(folder) if prefix == 'bin' else prefix) + os.pathsep + os.environ['PATH']
    decision = path_gate(layout, 'touch outputs/x', path)
    assert decision.action == 'ask'
    assert str(executable.resolve()) in decision.reason


def test_system_touch_allowed_and_resolution_logged_once(layout, monkeypatch):
    import labhq.workspace_commands as commands
    system = Path('C:/Program Files/Git/usr/bin' if os.name == 'nt' else '/usr/bin')
    executable = system / ('touch.exe' if os.name == 'nt' else 'touch')
    assert executable.is_file()
    # Model an unprivileged staff token even when CI itself runs as administrator/root.
    monkeypatch.setattr(commands, '_executable_writable', lambda path: False)
    calls = []
    resolve = commands._resolve_executable
    def counted(*args, **kwargs):
        calls.append(args[0])
        return resolve(*args, **kwargs)
    monkeypatch.setattr(commands, '_resolve_executable', counted)
    decision = path_gate(layout, 'touch outputs/x; touch outputs/y', str(system))
    assert decision.action == 'allow'
    assert str(executable.resolve()) in decision.reason
    assert calls == ['touch']


def test_writable_system_executable_requires_card(layout, monkeypatch):
    import labhq.workspace_commands as commands
    monkeypatch.setattr(commands, '_executable_writable', lambda path: True)
    system = 'C:/Program Files/Git/usr/bin' if os.name == 'nt' else '/usr/bin'
    decision = path_gate(layout, 'touch outputs/x', system)
    assert decision.action == 'ask'
    assert '(writable)' in decision.reason


def test_actual_write_access_is_detected(tmp_path):
    from labhq.workspace_commands import _executable_writable
    executable = tmp_path / 'fixture'
    executable.write_text('fixture only', encoding='utf-8')
    assert _executable_writable(executable)
    assert _executable_writable(tmp_path)


@pytest.mark.parametrize('tool', ['Bash', 'PowerShell'])
@pytest.mark.parametrize('command', [
    'cat local > alias', 'touch alias', "python -c \"open('alias','w').write('changed')\"",
])
def test_hardlink_write_requires_card(layout, command, tool):
    _, root, work, _ = layout
    secret = root / 'private' / 'secret'
    secret.write_text('unchanged', encoding='utf-8')
    (work / 'local').write_text('fixture', encoding='utf-8')
    os.link(secret, work / 'alias')
    assert gate(layout, tool, {'command': command}).action == 'ask'
    assert secret.read_text(encoding='utf-8') == 'unchanged'


def test_failed_cd_requires_card(layout):
    _link_dir(layout[2] / 'alias', layout[1] / 'private')
    assert gate(layout, 'Bash', {'command': 'cd missing; cat alias/secret'}).action == 'ask'


def test_prefix_assignment_requires_card(layout, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(layout[1] / 'private'))
    command = 'CLAUDE_CONFIG_DIR=tmp cat "$CLAUDE_CONFIG_DIR/settings.json"'
    assert gate(layout, 'Bash', {'command': command}).action == 'ask'


@pytest.mark.parametrize('command', [
    'cat "$X"', 'cat `pwd`', 'cat $(pwd)', 'X=tmp cat local', 'X=tmp',
    'cat <<< local', 'cat <(cat local)', 'cat >(cat local)',
    'eval "cat local"', 'source local', '. local', 'exec cat local',
    'cat local; cd tmp', 'cat local | cd tmp', 'cat local || cd tmp',
    'cd tmp; cat local', 'cd tmp | cat local', 'cd tmp || cat local',
    'cd tmp && cd next && cat local', 'cat local && cd tmp',
])
def test_outside_grammar_requires_card(layout, command):
    decision = gate(layout, 'Bash', {'command': command})
    assert decision.action == 'ask'
    assert decision.reason.startswith('문법 밖:')


@pytest.mark.parametrize('command', ['touch alias/new', 'cat local > alias/new'])
def test_linked_parent_requires_card_even_inside_workspace(layout, command):
    work = layout[2]
    (work / 'plain').mkdir()
    _link_dir(work / 'alias', work / 'plain')
    assert gate(layout, 'Bash', {'command': command}).action == 'ask'
