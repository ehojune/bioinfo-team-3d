"""A completed HPC run can supply PC inputs only with matching receipts."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from labhq import hpc_transfer as ht
from labhq.adapters.mock import MockAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.runner.step_handoff import required_hpc_revisions
from labhq.runner.step_inputs import stage_inputs
from labhq.runner.workspace import TaskWorkspace
from tests.test_hpc_transfer_1308 import site, write_script
from tests.test_workspace_boundary import _settings


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _row(path, contents):
    return {'path': path, 'bytes': len(contents), 'sha256': _sha(contents)}


def _restart(runner, settings, agent, monkeypatch):
    runner.store.close()
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
    return runner


@pytest.mark.parametrize('oversize', [False, True])
async def test_fake_hpc_receipt_survives_restart_and_controls_pc_adapter(site, monkeypatch, oversize):
    settings = _settings(site.tmp)
    settings.hpc = site.settings.hpc
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    agent = AgentSpec(id='worker', name='Worker', role='test', engine=Engine.mock, builtin_mcp=['hpc'])
    contents = {'results/a.tsv': b'a\n', 'results/z.tsv': b'z' * (1024 * 1024 + 1) if oversize else b'z\n'}
    reads = []
    row_paths = ['outputs/' + name for name in contents]
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, 'get', lambda _: agent)

    def scanning_fixture(_transfer, folder, action, config):
        # The remote helper uses Linux descriptors. Simulate that command on Windows while running the real
        # scheduler, transfer caps, checksum checks, job wake, receipt persistence and copy everywhere.
        if os.name != 'nt':
            subprocess.run([sys.executable, str(site.remote(f'{folder}/remote-data.py')), str(site.remote('')),
                            folder, action, config], check=True, timeout=30)
            return
        data = json.loads(site.remote(f'{folder}/{config}').read_text(encoding='utf-8'))
        files = [_row(name, site.remote(data['data'] + '/' + name).read_bytes()) for name in contents]
        if action == 'verify':
            assert data['files'] == files
        else:
            assert action == 'scan'
            data.update(files=files, directories=['results'], bytes=sum(row['bytes'] for row in files))
            site.remote(f'{folder}/remote-data.json').write_text(json.dumps(data), encoding='utf-8')

    monkeypatch.setattr(ht, '_remote_data_command', scanning_fixture)

    async def adapter_run(adapter, ctx):
        ctx.before_spawn()
        if ctx.task.meta['step_id'] == 'consumer':
            assert all(Path(directory) != source for directory in ctx.extra_dirs)
            reads.extend((ctx.workdir / 'inputs/producer' / name).read_bytes() for name in contents)
        elif not ctx.task.resume_session_id:
            site.workdir = ctx.workdir
            job = await runner._on_submit_raw({'task_id': ctx.task.id, 'agent_id': agent.id, 'request_id': 'r',
                                               'script': str(write_script(site))})
            assert job.get('submitted'), job
            for name, data in contents.items():
                target = site.remote(job['transfer']['data'] + '/' + name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, session_id='producer-session')
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text='done')

    monkeypatch.setattr(MockAdapter, 'run', adapter_run)
    producer_meta = {'kind': 'step', 'step_id': 'producer', 'revision': 2, 'outputs': ['outputs/results'],
                     'execution_location': {'mode': 'hpc', 'runner_id': settings.runner.id}}
    try:
        pending = await runner.run_task(Task(agent_id=agent.id, request_id='r', prompt='produce', meta=producer_meta))
        assert pending.ok and len(pending.pending_jobs) == 1
        state_file = site.tmp / 'slurm.json'
        state = json.loads(state_file.read_text())
        state['jobs'][pending.pending_jobs[0]]['state'] = 'COMPLETED'
        state_file.write_text(json.dumps(state))
        await runner._poll_jobs()
        assert runner.jobs[pending.pending_jobs[0]].get('fetched'), runner.jobs[pending.pending_jobs[0]]
        runner = _restart(runner, settings, agent, monkeypatch)
        result = await runner.run_task(Task(agent_id=agent.id, request_id='r', prompt='finish', meta=producer_meta,
                                            resume_session_id='producer-session'))
        assert result.ok, result.error  # remote-only completion is still valid for the producer
        assert set(result.outputs) == set(row_paths)
        source = Path(result.workdir)
        artifact = {'task_id': result.task_id, 'workdir': str(source), 'outputs': result.outputs,
                    'output_sha256': result.output_sha256}
        consumer = Task(agent_id=agent.id, request_id='r', prompt='consume', meta={
            'kind': 'step', 'step_id': 'consumer', 'execution_location': {'mode': 'local'},
            'upstream_dirs': [str(source)], 'upstream_steps': {'producer': str(source)},
            'upstream_artifacts': {'producer': artifact}, 'hpc_ancestor_revisions': {'producer': 2},
        })
        runner = _restart(runner, settings, agent, monkeypatch)
        monkeypatch.setattr(runner.scheduler, 'transfer', lambda: pytest.fail('PC handoff called the scheduler'))
        if oversize:
            results = []
            emit = runner.emit

            async def capture(event):
                if event.type == 'task.result':
                    results.append(event)
                await emit(event)

            monkeypatch.setattr(runner, 'emit', capture)
            await runner._run_guarded(consumer)
            [event] = results
            assert event.request_id == 'r' and event.task_id == consumer.id and event.data['ok'] is False
            assert 'outputs/results/z.tsv (1048577 bytes): fetch size limit' in event.data['error']
            assert reads == []
            assert not (runner.workspaces[consumer.id].dir / 'inputs/producer/results/a.tsv').exists()
        else:
            outcome = await runner.run_task(consumer)
            assert outcome.ok, outcome.error
            assert reads == list(contents.values())
    finally:
        runner.store.close()


@pytest.mark.parametrize('problem, reason', [
    ('request', 'successful step of this request'),
    ('step', 'successful step of this request'),
    ('task', 'successful step of this request'),
    ('revision', 'completed step revision'),
    ('pending', 'completed step revision'),
    ('legacy', 'receipt is missing'),
    ('subset', 'outputs differ'),
    ('duplicate', 'ambiguous'),
    ('path_alias', 'colliding HPC output path'),
    ('remote_hash', 'local copy not verified'),
    ('local_only', 'local.tsv: absent from the verified HPC inventory'),
    ('partial_directory', 'name not allowed'),
    ('deleted', 'z.tsv: received file is missing'),
    ('size', 'z.tsv: received file size differs'),
    ('changed', 'z.tsv: input hash differs'),
    ('protected', 'artifact path is protected'),
    ('hard_link', 'z.tsv: input file is hard-linked'),
    ('per_file_cap', 'z.tsv: input file is 3 bytes; the per-file limit is 2'),
    ('total_cap', 'cumulative input budget'),
])
def test_invalid_hpc_source_removes_any_old_or_partial_snapshot(tmp_path, problem, reason):
    root = tmp_path / 'runs'
    source = root / 'source'
    (source / 'outputs/results').mkdir(parents=True)
    manifest = {'request_id': 'r', 'data': ht.job_spec('t', 'work', 'r')['data'],
                'directories': ['results'], 'files': [_row('results/a.tsv', b'a'), _row('results/z.tsv', b'zzz')],
                'bytes': 4}
    hashes = {'outputs/' + row['path']: row['sha256'] for row in manifest['files']}
    run = {'request_id': 'r', 'step_id': 'producer', 'kind': 'step', 'revision': 2, 'ok': True,
           'pending_jobs': [], 'outputs': list(hashes), 'output_sha256': hashes, 'hpc_remote_outputs': [manifest],
           'hpc_local_receipt': {'sha256': dict(hashes), 'omissions': []}}
    record = {'task': {'request_id': 'r', 'meta': {'step_id': 'producer'}}, 'runs': {'current': run}}
    record_path = source / 'manifest.json'
    record_path.write_text(json.dumps(record))
    (source / 'outputs/results/a.tsv').write_bytes(b'a')
    last = source / 'outputs/results/z.tsv'
    last.write_bytes(b'zzz')
    artifact = {'task_id': 'current', 'workdir': str(source), 'outputs': list(hashes), 'output_sha256': dict(hashes)}
    task = Task(agent_id='worker', request_id='r', prompt='consume')
    ws = TaskWorkspace(root, task, AgentSpec(id='worker', name='Worker', role='test'))

    def stage(max_file=10, max_total=10, private=()):
        return stage_inputs(ws, {'producer': artifact}, root, [], private, max_file, max_total,
                            hpc_revisions={'producer': 2})

    staged, refused = stage()
    assert staged and not refused
    assert (ws.dir / 'inputs/producer/results/z.tsv').read_bytes() == b'zzz'
    max_file, max_total, private = 10, 10, []
    if problem == 'request':
        run['request_id'] = 'another'
    elif problem == 'step':
        run['step_id'] = 'another'
    elif problem == 'task':
        artifact['task_id'] = 'previous'
    elif problem == 'revision':
        run['revision'] = 1
    elif problem == 'pending':
        run['pending_jobs'] = ['1000']
    elif problem == 'legacy':
        run.pop('hpc_local_receipt')
    elif problem == 'subset':
        artifact['outputs'].pop()
    elif problem == 'duplicate':
        run['hpc_remote_outputs'].append({**manifest, 'data': ht.job_spec('other', 'work', 'r')['data']})
    elif problem == 'path_alias':
        artifact['outputs'].append('outputs/Results/alias.tsv')
    elif problem == 'remote_hash':
        manifest['files'][-1]['sha256'] = _sha(b'xyz')
    elif problem == 'local_only':
        path = 'outputs/local.tsv'
        (source / path).write_bytes(b'local')
        run['outputs'].append(path)
        artifact['outputs'].append(path)
        run['output_sha256'][path] = _sha(b'local')
        artifact['output_sha256'][path] = _sha(b'local')
    elif problem == 'partial_directory':
        run['hpc_local_receipt']['omissions'] = [
            {'path': 'outputs/results/part 3.tsv', 'reason': 'name not allowed'}]
    elif problem == 'deleted':
        last.unlink()
    elif problem == 'size':
        last.write_bytes(b'zzzz')
    elif problem == 'changed':
        last.write_bytes(b'xyz')
    elif problem == 'protected':
        private = [str(last)]
    elif problem == 'hard_link':
        os.link(last, source / 'alias.tsv')
    elif problem == 'per_file_cap':
        max_file = 2
    elif problem == 'total_cap':
        max_total = 3
    record_path.write_text(json.dumps(record))
    staged, refused = stage(max_file, max_total, private)
    assert not staged and reason in refused['producer']
    assert not list((ws.dir / 'inputs/producer').rglob('*.tsv'))


@pytest.mark.parametrize('mapping', [None, [], {'../producer': 0}, {'producer': False}, {'producer': -1}])
def test_invalid_hpc_requirement_cannot_fall_back_to_local_copy(mapping):
    task = Task(agent_id='worker', request_id='r', prompt='consume', meta={
        'execution_location': {'mode': 'local'}, 'hpc_ancestor_revisions': mapping})
    with pytest.raises(ValueError, match='invalid HPC-to-PC ancestor mapping'):
        required_hpc_revisions(task, whole_hpc=False, artifact_contract=True)
