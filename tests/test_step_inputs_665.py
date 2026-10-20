"""Ancestor input contract with mock adapters; no staff/process integration."""
import hashlib
import json
from pathlib import Path

import pytest

from labhq.models import Engine, Task
from labhq.policy import evaluate_tool
from labhq.settings import DataZone
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_tool_card_explain import _system_commands


def source(root, step='fetch', request='r'):
    folder = root / ('source-' + step)
    (folder / 'outputs').mkdir(parents=True)
    (folder / 'outputs/table.tsv').write_bytes(b'gene\nfixture\n')
    (folder / 'outputs/uncollected.txt').write_text('not collected', encoding='utf-8')
    (folder / 'manifest.json').write_text(json.dumps({
        'task': {'id': 't-' + step, 'request_id': request, 'meta': {'step_id': step}},
        'runs': {'t-' + step: {'ok': True}},
    }), encoding='utf-8')
    return folder, {'workdir': str(folder), 'outputs': ['outputs/table.tsv'],
                    'output_sha256': {'outputs/table.tsv': hashlib.sha256(b'gene\nfixture\n').hexdigest()}}


async def run_step(tmp_path, monkeypatch, engine=Engine.claude_code, mutate=None, reuse=None):
    settings = _runner_settings(tmp_path, None)
    root = Path(settings.runner.workspace_root)
    root.mkdir(exist_ok=True)
    folder, artifact = source(root)
    if mutate:
        mutate(settings, folder, artifact)
    _system_commands(monkeypatch)
    # The old branch would create a native link; model its refusal for the red reproduction.
    monkeypatch.setattr('labhq.runner.workspace.TaskWorkspace.link_inputs',
                        lambda self, links: {key: 'mock legacy link unavailable' for key in links})
    runner, seen = _capture_runner(settings, monkeypatch, engine)
    task = Task(id='down', agent_id='worker', request_id='r', prompt='read inputs/fetch/table.tsv',
        meta={'kind': 'step', 'step_id': 'qc', 'upstream_steps': {'fetch': str(folder)},
              'upstream_dirs': [str(folder)], 'upstream_artifacts': {'fetch': artifact},
              **({'workdir': str(reuse)} if reuse else {})})
    result = await runner.run_task(task)
    assert result.ok, result.error
    return runner, seen['ctx'], folder


def gate(ctx, tool, data):
    return evaluate_tool(tool, data, ctx.settings.policy, workdir=str(ctx.workdir),
        allowed_roots=[str(ctx.workdir), *ctx.extra_dirs], private_paths=ctx.private_paths,
        private_open_reads=ctx.private_open_reads, private_enabled=ctx.private_enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', [Engine.claude_code, Engine.codex])
async def test_collected_ancestor_is_readable_only_through_local_inputs(tmp_path, monkeypatch, engine):
    _, ctx, upstream = await run_step(tmp_path, monkeypatch, engine)
    target = ctx.workdir / 'inputs/fetch/table.tsv'
    assert target.read_bytes() == b'gene\nfixture\n'
    assert not target.is_symlink()
    assert not (target.parent / 'uncollected.txt').exists()
    assert str(upstream) not in ctx.extra_dirs
    assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'allow'
    assert gate(ctx, 'Bash', {'command': 'cat inputs/fetch/table.tsv'}).action == 'allow'
    assert gate(ctx, 'Write', {'file_path': str(target), 'content': 'changed'}).action == 'deny'
    assert gate(ctx, 'Bash', {'command': 'touch inputs/fetch/table.tsv'}).action != 'allow'
    assert gate(ctx, 'Bash', {'command': f'cat "{upstream}/outputs/table.tsv"'}).action == 'ask'
    assert gate(ctx, 'Read', {'file_path': str(upstream / 'outputs/table.tsv')}).action == 'deny'
    assert 'inputs/' in gate(ctx, 'Bash', {'command': f'cat "{upstream}/outputs/table.tsv"'}).reason
    assert 'inputs/' in ctx.prompt and 'instead' not in ctx.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['request', 'step', 'unfinished', 'hash', 'traversal', 'credential', 'zone'])
async def test_unsafe_or_unproven_artifact_is_not_staged(tmp_path, monkeypatch, bad):
    def mutate(settings, folder, artifact):
        manifest = json.loads((folder / 'manifest.json').read_text())
        if bad in {'request', 'step', 'unfinished'}:
            if bad == 'request':
                manifest['task']['request_id'] = 'other'
            elif bad == 'step':
                manifest['task']['meta']['step_id'] = 'sibling'
            else:
                manifest['runs']['t-fetch']['ok'] = False
            (folder / 'manifest.json').write_text(json.dumps(manifest))
        elif bad == 'hash':
            artifact['output_sha256']['outputs/table.tsv'] = '0' * 64
        elif bad == 'traversal':
            artifact['outputs'] = ['outputs/../manifest.json']
        elif bad == 'credential':
            artifact['outputs'] = ['outputs/auth.json']
            (folder / 'outputs/auth.json').write_text('fixture only')
            artifact['output_sha256'] = {'outputs/auth.json': hashlib.sha256(b'fixture only').hexdigest()}
        else:
            settings.policy.data_zones = [DataZone(path=str(folder / 'outputs'), level='internal')]
    _, ctx, upstream = await run_step(tmp_path, monkeypatch, mutate=mutate)
    assert not (ctx.workdir / 'inputs/fetch/table.tsv').exists()
    assert gate(ctx, 'Bash', {'command': f'cat "{upstream}/outputs/table.tsv"'}).action != 'allow'
    assert 'unavailable' in ctx.prompt.lower()
    assert 'read its files at' not in ctx.prompt


@pytest.mark.asyncio
async def test_restage_removes_only_previous_snapshot_files(tmp_path, monkeypatch):
    settings = _runner_settings(tmp_path, None)
    root = Path(settings.runner.workspace_root)
    root.mkdir(exist_ok=True)
    folder, artifact = source(root)
    from labhq.runner.step_inputs import stage_inputs
    from labhq.runner.workspace import TaskWorkspace
    from labhq.models import AgentSpec
    task = Task(id='down', agent_id='worker', request_id='r', prompt='fixture')
    ws = TaskWorkspace(root, task, AgentSpec(id='worker', name='Worker', role='test'))
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not refused and staged
    artifact['outputs'] = ['outputs/new.tsv']
    (folder / 'outputs/new.tsv').write_bytes(b'new')
    artifact['output_sha256'] = {'outputs/new.tsv': hashlib.sha256(b'new').hexdigest()}
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not refused and staged
    assert not (ws.dir / 'inputs/fetch/table.tsv').exists()
    assert (ws.dir / 'inputs/fetch/new.tsv').read_bytes() == b'new'
    assert (folder / 'outputs/table.tsv').read_bytes() == b'gene\nfixture\n'


@pytest.mark.parametrize('bad', ['size', 'missing_hash', 'private', 'link_swap', 'existing_input'])
def test_staging_failures_never_open_unproven_input(tmp_path, monkeypatch, bad):
    from labhq.runner.step_inputs import stage_inputs
    from labhq.runner.workspace import TaskWorkspace
    from labhq.models import AgentSpec
    from labhq.adapters.held_dir import HeldDir
    root = tmp_path / 'runs'
    root.mkdir()
    folder, artifact = source(root)
    task = Task(id='down', agent_id='worker', request_id='r', prompt='fixture')
    ws = TaskWorkspace(root, task, AgentSpec(id='worker', name='Worker', role='test'))
    limit, private = 1024, []
    if bad == 'size':
        limit = 1
    elif bad == 'missing_hash':
        artifact['output_sha256'] = {}
    elif bad == 'private':
        private = [str(folder / 'outputs/table.tsv')]
    elif bad == 'existing_input':
        (ws.dir / 'inputs/fetch').mkdir(parents=True)
        (ws.dir / 'inputs/fetch/keep.txt').write_text('preserve')
    else:
        opened = HeldDir.open_read_file
        def refuse_link(self, name):
            if name == 'table.tsv':
                raise OSError('mock link swapped after path validation')
            return opened(self, name)
        monkeypatch.setattr(HeldDir, 'open_read_file', refuse_link)
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], private, limit, 4096)
    assert not staged and 'fetch' in refused
    assert not (ws.dir / 'inputs/fetch/table.tsv').exists()
    if bad == 'existing_input':
        assert (ws.dir / 'inputs/fetch/keep.txt').read_text() == 'preserve'
    assert (folder / 'outputs/table.tsv').read_bytes() == b'gene\nfixture\n'
    if bad == 'size':
        staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
        assert staged and not refused
