"""Task-owned operations need no card; credentials and other tasks still do."""
import json
from pathlib import Path

import pytest

from labhq.policy import evaluate_tool
from tests.test_tool_card_explain import gate, layout
from tests.test_private_paths import _link_dir

REPLAY = json.loads((Path(__file__).parent / 'fixtures/approval_fatigue.json').read_text(encoding='utf-8'))


def replay_input(row, layout):
    from labhq.private_paths import claude_project_slug
    _, root, work, skills = layout
    mapping = {'work': work, 'skill': skills, 'other': root / 'runs/other',
               'saved': root / 'claude/projects' / claude_project_slug(str(work))}
    command = row['command']
    for name, value in mapping.items():
        command = command.replace('{' + name + '}', value.as_posix())
    return {'command': command}


@pytest.mark.parametrize('row', REPLAY, ids=lambda r: str(r['case']))
def test_replay_supplied_46_records(layout, row):
    if row['status'] == 'truncated_input_unavailable':
        pytest.skip('source input truncated; no exact replay claim')
    assert gate(layout, row['tool'], replay_input(row, layout)).action == row['after']


def test_replay_coverage_is_explicit():
    from collections import Counter
    assert len(REPLAY) == 46
    assert Counter(row['status'] for row in REPLAY) == {
        'complete_input_assumed_context': 26, 'mock_reconstructed_from_summary': 8,
        'truncated_input_unavailable': 12}


@pytest.mark.parametrize('command', [
    'mkdir -p tmp; touch tmp/item; cat tmp/item',
    'rm -rf tmp/', 'rm -rf .tmp/paper-review',
    'cd .tmp && rm -rf paper-review',
    'cat "{work}/guide.md" > "{work}/copy.md"',
])
def test_workspace_shell_needs_no_card(layout, command):
    work = layout[2]
    (work / '.tmp').mkdir(exist_ok=True)
    assert gate(layout, 'Bash', {'command': command.replace('{work}', work.as_posix())}).action == 'allow'


@pytest.mark.parametrize('tool,command', [
    ('Bash', 'rm -rf "{work}/tmp"'),
    ('PowerShell', 'Remove-Item -Recurse -Force "{work}/tmp"'),
    ('PowerShell', 'Invoke-WebRequest -Uri "https://example.org/supplement.pdf" -OutFile src/supplement.pdf -UseBasicParsing'),
])
def test_absolute_delete_and_download(layout, tool, command):
    assert gate(layout, tool, {'command': command.replace('{work}', layout[2].as_posix())}).action == 'allow'


@pytest.mark.parametrize('command', [
    'rm -rf ../sibling', 'rm -rf .', 'rm -rf "{work}"',
    'cd .. && rm -rf sibling', 'rm -rf tmp/../../sibling',
    'cat .env', 'cat auth.json', 'cat ../other/guide.md',
    'cat "{root}/private/diary.md"', 'touch "{root}/runs/other/output.md"',
    'cat "${UNKNOWN}"', 'cat "unterminated',
    "python -c \"open('../other/secret')\"",
    "python -c \"import os; os.system('cat ../private')\"",
    'S="{work}"; cat "$S/guide.md"',
    "python - <<'EOF'\nimport json\np='.tmp/bundle.json'\nb=json.load(open(p))\njson.dump(b,open('outputs/bundle.json','w'))\nEOF",
])
def test_real_risks_still_ask(layout, command):
    cmd = command.replace('{work}', layout[2].as_posix()).replace('{root}', layout[1].as_posix())
    assert gate(layout, 'Bash', {'command': cmd}).action == 'ask'


@pytest.mark.parametrize('tool,command', [
    ('Bash', 'cat alias/secret.md'), ('Bash', 'rm -rf alias/cache'),
    ('Bash', 'cat alias/../secret.md'),
    ('PowerShell', 'Remove-Item -Recurse alias/cache'),
])
def test_symlink_or_windows_junction_escape(layout, tool, command):
    _link_dir(layout[2] / 'alias', layout[1] / 'private')
    assert gate(layout, tool, {'command': command}).action == 'ask'


def test_explicit_private_child_is_never_exempt(layout):
    cfg, root, work, _ = layout
    private = work / 'protected'
    private.mkdir()
    cfg.policy.private_paths.append(str(private))
    assert gate(layout, 'Bash', {'command': 'touch protected/file'}).action == 'ask'


def test_zone_still_wins(layout):
    from labhq.settings import DataZone
    cfg, _, work, _ = layout
    cfg.policy.data_zones = [DataZone(path=str(work / 'raw'), level='restricted')]
    assert gate(layout, 'Bash', {'command': 'rm -rf raw'}).action == 'ask'


def test_read_only_still_wins(layout):
    cfg, root, work, _ = layout
    assert evaluate_tool('Bash', {'command': 'rm -rf tmp'}, cfg.policy, workdir=str(work),
                         allowed_roots=[str(work)], private_paths=[str(root)], read_only=True).action == 'deny'


@pytest.mark.asyncio
async def test_auto_workspace_permission_logs_without_card(layout, monkeypatch):
    from types import SimpleNamespace
    from labhq.tools import approval_mcp as mcp
    cfg, root, work, _ = layout
    monkeypatch.setattr(mcp, 'S', cfg)
    monkeypatch.setattr(mcp, 'WORKDIR', str(work))
    monkeypatch.setattr(mcp, 'WORKDIR_INPUT', str(work))
    monkeypatch.setattr(mcp, 'EXTRA_ROOTS', [])
    monkeypatch.setattr(mcp, 'READ_ONLY', False)
    monkeypatch.setattr(mcp, 'TOKEN', 'fixture-only')
    monkeypatch.setattr(mcp, 'PRIVATE', SimpleNamespace(paths=[str(root)], enabled=True, open_reads=[str(work)]))
    sent = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            sent.append((url, kwargs['json']))
            return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(mcp.httpx, 'AsyncClient', Client)
    result = json.loads(await mcp.approval_prompt('Bash', {'command': 'rm -rf tmp/'}))
    assert result['behavior'] == 'allow'
    assert len(sent) == 1 and sent[0][0].endswith('/event')
    assert sent[0][1]['data']['policy_rule'] == 'task_workspace'
    assert sent[0][1]['data']['text'].startswith('문법 안:')


@pytest.mark.asyncio
async def test_legacy_scope_fields_never_create_or_reuse_a_grant():
    from labhq.models import ApprovalRequest
    from labhq.runner.approvals import Broker
    cards = []
    async def card(req):
        cards.append(req)
        broker.resolve(req.id, True, choice='allow_task_class')
    async def noop(body): pass
    broker = Broker(0, card, noop, noop)
    answers = []
    for _ in range(2):
        answers.append(await broker.request_approval(ApprovalRequest(
            task_id='t', agent_id='a', kind='tool_permission', summary='fixture',
            detail={'task_scope_label': 'legacy'})))
    assert len(cards) == 2
    assert all('task_scope_label' not in c.detail for c in cards)
    assert all('choice' not in answer for answer in answers)


@pytest.mark.asyncio
async def test_gateway_ignores_a_saved_legacy_grant_choice(tmp_path):
    from labhq.gateway.server import Hub
    from labhq.settings import Settings
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / 'state')
    hub = Hub(cfg)
    hub.store.put('decision', 'legacy', {'origin': 'runner', 'approved': True, 'note': 'legacy',
                                         'choice': 'allow_task_class'})
    sent = []
    async def send(runner, data): sent.append(data)
    hub.send_runner = send
    await hub.flush_decisions('runner')
    assert sent == [{'type': 'approval.resolved', 'id': 'legacy', 'approved': True, 'note': 'legacy'}]


def test_dangerous_command_pattern_still_wins(layout):
    cfg, _, _, _ = layout
    cfg.policy.approvals.bash_ask_patterns.append(r'\brm\b')
    assert gate(layout, 'Bash', {'command': 'rm -rf tmp'}).action == 'ask'


@pytest.mark.parametrize('place', ['work', 'skill'])
def test_glob_read_never_reopens_restricted_child(layout, place):
    from labhq.settings import DataZone
    cfg, _, work, skills = layout
    folder = work if place == 'work' else skills
    private = folder / 'protected.py'
    private.write_text('fixture only', encoding='utf-8')
    cfg.policy.data_zones = [DataZone(path=str(private), level='restricted')]
    assert gate(layout, 'Bash', {'command': f'cat "{folder.as_posix()}"/*.py'}).action == 'ask'
