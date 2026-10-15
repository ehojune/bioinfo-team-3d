"""PR #659 P1 reproductions: classify only; never execute the submitted shell."""
import os

import pytest

from tests.test_tool_card_explain import gate, layout
from tests.test_private_paths import _link_dir


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
