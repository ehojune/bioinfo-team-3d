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


@pytest.mark.asyncio
async def test_writable_helper_replacement_cannot_reuse_grant(layout):
    from labhq.models import ApprovalRequest
    from labhq.runner.approvals import Broker
    from labhq.workspace_commands import script_approval_scope
    _, root, work, skills = layout
    (work / 'main.py').write_text('import helper\nhelper.main()\n', encoding='utf-8')
    helper = work / 'helper.py'
    helper.write_text('def main(): pass\n', encoding='utf-8')
    data = {'command': 'python main.py'}
    cards = []
    async def card(req):
        cards.append(req)
        broker.resolve(req.id, True, choice='allow_task_class')
    async def noop(body): pass
    broker = Broker(0, card, noop, noop, tool_scope=lambda req: script_approval_scope(
        'Bash', data, str(work), [str(root)], [str(work), str(skills)]))
    def request():
        return ApprovalRequest(task_id='task', agent_id='worker', kind='tool_permission', summary='fixture')
    await broker.request_approval(request())
    helper.write_text('def main(): raise RuntimeError("changed")\n', encoding='utf-8')
    await broker.request_approval(request())
    assert len(cards) == 2
    assert not broker.scope_grants


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


@pytest.mark.asyncio
async def test_managed_tree_helper_change_invalidates_broker_grant(layout):
    from labhq.models import ApprovalRequest
    from labhq.runner.approvals import Broker
    from labhq.workspace_commands import script_approval_scope
    _, root, work, skills = layout
    script = skills / 'main.py'
    script.write_text('import helper\nhelper.main()\n', encoding='utf-8')
    helper = skills / 'helper.py'
    helper.write_text('def main(): pass\n', encoding='utf-8')
    cards = []
    def scope(req):
        return script_approval_scope('Bash', {'command': f'python "{script.as_posix()}"'},
                    str(work), [str(root)], [str(work), str(skills)], managed_roots=[str(skills)])
    async def card(req):
        cards.append(req)
        broker.resolve(req.id, True, choice='allow_task_class')
    async def noop(body): pass
    broker = Broker(0, card, noop, noop, tool_scope=scope)
    def request():
        return ApprovalRequest(task_id='task', agent_id='worker', kind='tool_permission', summary='fixture')
    first = scope(None)
    assert first
    await broker.request_approval(request())
    await broker.request_approval(request())
    assert len(cards) == 1
    helper.write_text('def main(): raise RuntimeError("changed")\n', encoding='utf-8')
    assert scope(None) != first
    await broker.request_approval(request())
    assert len(cards) == 2


@pytest.mark.parametrize('change', ['add', 'remove', 'rename', 'hardlink', 'junction', 'writable', 'unmanaged'])
def test_scope_tracks_entire_tree_and_provenance(layout, change):
    from labhq.workspace_commands import script_approval_scope
    _, root, work, skills = layout
    script = skills / 'main.py'
    script.write_text('pass', encoding='utf-8')
    helper = skills / '.helper'
    helper.write_text('fixture', encoding='utf-8')
    managed = [str(skills)]
    writable = []
    def scope():
        return script_approval_scope('Bash', {'command': f'python "{script.as_posix()}"'},
                    str(work), [str(root)], [str(work), str(skills)],
                    managed_roots=managed, writable_roots=writable)
    first = scope()
    assert first
    if change == 'add':
        (skills / 'extra.py').write_text('pass', encoding='utf-8')
    elif change == 'remove':
        helper.unlink()
    elif change == 'rename':
        helper.rename(skills / 'renamed')
    elif change == 'hardlink':
        os.link(helper, work / 'alias')
    elif change == 'junction':
        _link_dir(skills / 'alias', work)
    elif change == 'writable':
        writable.append(str(skills))
    else:
        managed.clear()
    assert scope() != first
    if change in {'hardlink', 'junction', 'writable', 'unmanaged'}:
        assert scope() is None
