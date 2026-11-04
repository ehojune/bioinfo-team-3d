"""PR 791 regressions using synthetic artifacts and command construction, never native staff processes."""
import asyncio
import hashlib
import json
import threading
from pathlib import Path

import pytest

from labhq.adapters.base import RunContext
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.step_inputs import stage_inputs
from labhq.runner.workspace import TaskWorkspace
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_step_inputs_665 import source, run_step
from tests.test_tool_card_explain import _system_commands


@pytest.fixture(autouse=True)
def mock_workers(monkeypatch):
    from labhq import durable
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    async def write_inline(lane, fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    monkeypatch.setattr(durable, 'run', write_inline)
    monkeypatch.setattr(durable, 'call', lambda lane, fn, *args, **kwargs: fn(*args, **kwargs))
    def denied(*args, **kwargs):
        pytest.fail('mock regression must not start a native worker')
    monkeypatch.setattr(threading.Thread, 'start', denied)


def workspace(tmp_path):
    root = tmp_path / 'runs'
    root.mkdir()
    ws = TaskWorkspace(root, Task(id='down', agent_id='worker', request_id='r', prompt='fixture'),
                       AgentSpec(id='worker', name='Worker', role='fixture'))
    return root, ws


@pytest.mark.parametrize('windows', [False, True])
@pytest.mark.parametrize('resume', [False, True])
def test_codex_input_snapshot_has_no_writable_ancestor_or_cli_escape(tmp_path, monkeypatch, windows, resume):
    from labhq.settings import Settings
    monkeypatch.setattr('labhq.adapters.codex._is_windows', lambda: windows)
    settings = Settings()
    settings.engines.codex.isolate_user_config = False
    bypass = '--dangerously-bypass-approvals-and-sandbox'
    settings.engines.codex.extra_args = [bypass]
    workdir = tmp_path / 'task'
    workdir.mkdir()
    async def emit(*args):
        pass
    ctx = RunContext(task=Task(agent_id='worker', prompt='fixture',
                              resume_session_id='fixture-session' if resume else None),
                     agent=AgentSpec(id='worker', name='Worker', role='fixture', engine=Engine.codex),
                     settings=settings, workdir=workdir, prompt='fixture', mcp_servers=[], env={}, emit=emit,
                     extra_dirs=[str(workdir)])
    ctx.read_only_inputs = [str(workdir / 'inputs' / 'fetch')]
    cmd = CodexAdapter(settings).build_command(ctx)
    profiles = [v for v in cmd if v.startswith('permissions.labhq_staff=')]
    assert profiles and 'extends = ":read-only"' in profiles[0]
    assert f'{json.dumps(str(workdir.resolve()))} = "write"' not in profiles[0]
    for name in ('outputs', '.tmp'):
        assert f'{json.dumps(str(workdir.resolve() / name))} = "write"' in profiles[0]
    assert '--add-dir' not in cmd and bypass not in cmd and '-s' not in cmd
    assert '--ignore-user-config' in cmd and 'approval_policy="never"' in cmd


@pytest.mark.asyncio
async def test_runner_binds_staged_snapshot_to_the_codex_sandbox(tmp_path, monkeypatch):
    _, ctx, _ = await run_step(tmp_path, monkeypatch, engine=Engine.codex)
    assert getattr(ctx, 'read_only_inputs', []) == [str(ctx.workdir / 'inputs' / 'fetch')]


@pytest.mark.parametrize('proof', ['successful_retry', 'failed_retry', 'missing_run', 'missing_task',
                                  'empty_task', 'other_request', 'other_step'])
def test_staging_uses_the_result_task_run_instead_of_the_first_run(tmp_path, proof):
    root, ws = workspace(tmp_path)
    folder, artifact = source(root)
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['runs']['t-fetch']['ok'] = False
    manifest['runs']['retry-fetch'] = {'ok': proof not in {'failed_retry'}}
    artifact['task_id'] = 'missing' if proof == 'missing_run' else 'retry-fetch'
    if proof == 'missing_task':
        artifact.pop('task_id')
        manifest['runs']['t-fetch']['ok'] = True
    elif proof == 'empty_task':
        artifact['task_id'] = ''
        manifest['runs'][''] = {'ok': True}
    elif proof == 'other_request':
        manifest['runs']['retry-fetch']['request_id'] = 'other'
    elif proof == 'other_step':
        manifest['runs']['retry-fetch']['step_id'] = 'other'
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    if proof == 'successful_retry':
        assert staged and not refused
        assert (ws.dir / 'inputs/fetch/table.tsv').read_bytes() == b'gene\nfixture\n'
    else:
        assert not staged and 'fetch' in refused


@pytest.mark.parametrize('problem', ['hash', 'size', 'copy_error'])
def test_refused_ancestor_has_no_readable_partial_snapshot(tmp_path, monkeypatch, problem):
    root, ws = workspace(tmp_path)
    folder, artifact = source(root)
    artifact['task_id'] = 't-fetch'
    later = folder / 'outputs' / 'later.tsv'
    later.write_bytes(b'later fixture')
    artifact['outputs'].append('outputs/later.tsv')
    artifact['output_sha256']['outputs/later.tsv'] = hashlib.sha256(later.read_bytes()).hexdigest()
    limit = 1024
    if problem == 'hash':
        artifact['output_sha256']['outputs/later.tsv'] = '0' * 64
    elif problem == 'size':
        limit = len(b'gene\nfixture\n')
        later.write_bytes(b'x' * (limit + 1))
        artifact['output_sha256']['outputs/later.tsv'] = hashlib.sha256(later.read_bytes()).hexdigest()
    else:
        from labhq.runner import step_inputs
        original = step_inputs._copy
        def broken(source, parts, *args):
            if parts[-1] == 'later.tsv':
                raise OSError('synthetic second-file failure')
            return original(source, parts, *args)
        monkeypatch.setattr(step_inputs, '_copy', broken)
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], limit, 4096)
    assert not staged and 'fetch' in refused
    assert not (ws.dir / 'inputs/fetch/table.tsv').exists()
    assert not (ws.dir / 'inputs/fetch/later.tsv').exists()
    assert (folder / 'outputs/table.tsv').read_bytes() == b'gene\nfixture\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['eligible', 'file', 'total'])
async def test_collected_step_hash_covers_the_input_cap_and_reaches_next_step(tmp_path, monkeypatch, bound):
    settings = _runner_settings(tmp_path, None)
    settings.runner.output_hash_max_bytes = 4
    settings.runner.input_hash_max_file_bytes = 4 if bound == 'file' else 32
    settings.runner.input_hash_max_total_bytes = 8 if bound == 'total' else 128
    _system_commands(monkeypatch)
    runner, _ = _capture_runner(settings, monkeypatch)
    body = b'bounded synthetic matrix'
    class Adapter:
        async def run(self, ctx):
            assert ctx.before_spawn() is None  # mock the lifecycle boundary, never spawn a CLI
            (ctx.workdir / 'outputs/matrix.tsv').write_bytes(body)
            return TaskResult(task_id=ctx.task.id, agent_id='worker', ok=True)
    monkeypatch.setattr('labhq.runner.daemon.get_adapter', lambda *_: Adapter())
    result = await runner.run_task(Task(id='up', agent_id='worker', request_id='r', prompt='fixture',
        meta={'kind': 'step', 'step_id': 'fetch', 'outputs': ['matrix.tsv']}))
    assert result.ok and result.outputs == ['outputs/matrix.tsv']
    assert result.output_sha256 == ({'outputs/matrix.tsv': hashlib.sha256(body).hexdigest()}
                                    if bound == 'eligible' else {})
    ws = TaskWorkspace(Path(settings.runner.workspace_root),
        Task(id='down', agent_id='worker', request_id='r', prompt='fixture'),
        AgentSpec(id='worker', name='Worker', role='fixture'))
    artifact = {'task_id': result.task_id, 'workdir': result.workdir,
                'outputs': result.outputs, 'output_sha256': result.output_sha256}
    staged, refused = stage_inputs(ws, {'fetch': artifact}, settings.runner.workspace_root, [], [],
                                  settings.runner.input_hash_max_file_bytes,
                                  settings.runner.input_hash_max_total_bytes)
    if bound == 'eligible':
        assert staged and not refused
        assert (ws.dir / 'inputs/fetch/matrix.tsv').read_bytes() == body
    else:
        assert not staged and 'fetch' in refused
        assert not (ws.dir / 'inputs/fetch/matrix.tsv').exists()


def test_refused_restage_also_clears_a_previous_owned_snapshot(tmp_path):
    root, ws = workspace(tmp_path)
    _, artifact = source(root)
    artifact['task_id'] = 't-fetch'
    assert stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)[0]
    artifact['output_sha256'] = {}  # fail before copying the next snapshot
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not staged and 'fetch' in refused
    assert not (ws.dir / 'inputs/fetch/table.tsv').exists()


def test_cleanup_failure_stops_instead_of_returning_readable_partial_input(tmp_path, monkeypatch):
    from labhq.runner import step_inputs
    root, ws = workspace(tmp_path)
    _, artifact = source(root)
    artifact['task_id'] = 't-fetch'
    reset = step_inputs._reset
    copy_file = step_inputs._copy
    def fail_after_copy(*args):
        copy_file(*args)
        raise OSError('synthetic failure after copy')
    def fail_cleanup(workdir, step, names):
        if not names and (workdir / 'inputs' / step / 'table.tsv').exists():
            raise OSError('synthetic cleanup refusal')
        return reset(workdir, step, names)
    monkeypatch.setattr(step_inputs, '_copy', fail_after_copy)
    monkeypatch.setattr(step_inputs, '_reset', fail_cleanup)
    with pytest.raises(RuntimeError, match='could not be cleared'):
        stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)


@pytest.mark.asyncio
async def test_reused_workspace_inputs_remain_protected_without_new_artifact_metadata(tmp_path, monkeypatch):
    runner, ctx, _ = await run_step(tmp_path, monkeypatch, engine=Engine.codex)
    # Model a later turn reusing this workspace. No native CLI is run by the capture adapter.
    later, seen = _capture_runner(runner.s, monkeypatch, Engine.codex)
    result = await later.run_task(Task(id='later', agent_id='worker', request_id='r', prompt='fixture',
        meta={'kind': 'result_correction', 'workdir': str(ctx.workdir)}))
    assert result.ok
    assert seen['ctx'].read_only_inputs == [str(ctx.workdir / 'inputs')]
