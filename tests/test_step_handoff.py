"""A completed HPC run can supply PC inputs only with matching receipts."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import hpc_transfer as ht
from labhq.adapters.mock import MockAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.orchestrator.step_execution import upstream_inputs
from labhq.runner.daemon import Runner
from labhq.runner.step_handoff import project_hpc_outputs, required_hpc_revisions
from labhq.runner.step_inputs import stage_inputs
from labhq.runner.workspace import TaskWorkspace
from tests.test_cso import FakeHub
from tests.test_hpc_transfer_1308 import site, write_script
from tests.test_step_execution_contract import approved, placed_plan
from tests.test_workspace_boundary import _settings


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _row(path, contents):
    return {'path': path, 'bytes': len(contents), 'sha256': _sha(contents)}


def test_direct_and_nested_remote_names_cannot_claim_the_same_pc_input():
    inventory = {'files': [_row('table.tsv', b'1\n'), _row('outputs/table.tsv', b'1\n')]}
    with pytest.raises(ValueError, match='outputs/table.tsv: ambiguous HPC output'):
        project_hpc_outputs([inventory], ['outputs/table.tsv'])


def _restart(runner, settings, agent, monkeypatch):
    runner.store.close()
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
    return runner


@pytest.mark.parametrize(('mixed', 'case'), [
    (False, 'ok'), (False, 'oversize'), (True, 'ok'), (True, 'oversize'), (True, 'wrap_up'),
    (True, 'nested'), (True, 'nested_with_copy'), (True, 'nested_tampered'), (True, 'nested_oversize'),
    (True, 'nested_optional_oversize'), (True, 'nested_optional_only'), (True, 'local_optional_only'),
], ids=['receipt-only', 'receipt-oversize', 'mixed-dag', 'mixed-oversize', 'mixed-wrap-up',
        'mixed-nested', 'mixed-nested-with-copy', 'mixed-nested-tampered', 'mixed-nested-oversize',
        'mixed-nested-optional-oversize', 'mixed-nested-optional-only', 'mixed-local-optional-only'])
async def test_fake_hpc_receipt_survives_restart_and_controls_pc_adapter(site, monkeypatch, mixed, case):
    optional_only = case in {'nested_optional_only', 'local_optional_only'}
    optional_oversize = case in {'nested_optional_oversize', 'nested_optional_only'}
    oversize = case in {'oversize', 'nested_oversize'}
    nested = case.startswith('nested')
    expected_error = ('outputs/results/z.tsv.*fetch size limit' if oversize else
                      'outputs/results/z.tsv: input hash differs' if case == 'nested_tampered' else None)
    settings = _settings(site.tmp)
    settings.hpc = site.settings.hpc
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    agent = AgentSpec(id='worker', name='Worker', role='test', engine=Engine.mock, builtin_mcp=['hpc'])
    contents = {'results/a.tsv': b'a\n', 'results/z.tsv': b'z' * (1024 * 1024 + 1) if oversize else b'z\n'}
    if optional_only:
        contents = {}
    remote_contents = {'outputs/' + name: data for name, data in contents.items()} if nested else contents
    if optional_oversize:
        remote_contents['outputs/preview.tsv'] = b'p' * (1024 * 1024 + 1)
    reads = []
    row_paths = ['outputs/' + name for name in contents]
    refs = site.tmp / 'reference'
    refs.mkdir()
    (refs / 'note.txt').write_text('local only', encoding='utf-8')
    settings.runner.reference_roots = [str(refs)]
    observed = {}
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
        files = [_row(name, site.remote(data['data'] + '/' + name).read_bytes()) for name in remote_contents
                 if action == 'scan' or name in {row['path'] for row in data['files']}]
        if action == 'verify':
            assert data['files'] == files
        else:
            assert action == 'scan'
            directories = sorted({Path(name).parent.as_posix() for name in remote_contents} - {'.'})
            if nested:
                directories = sorted(set(directories) | {'outputs'})
            data.update(files=files, directories=directories, bytes=sum(row['bytes'] for row in files))
            site.remote(f'{folder}/remote-data.json').write_text(json.dumps(data), encoding='utf-8')

    monkeypatch.setattr(ht, '_remote_data_command', scanning_fixture)

    async def adapter_run(adapter, ctx):
        ctx.before_spawn()
        if ctx.task.meta['step_id'] == 'consumer':
            assert all(Path(directory) != source for directory in ctx.extra_dirs)
            if mixed:
                assert str(refs) in ctx.read_dirs and str(refs) not in ctx.extra_dirs
                assert (refs / 'note.txt').read_text(encoding='utf-8') == 'local only'
                assert ctx.task.meta['hpc_ancestor_revisions'] == {'producer': 2}
                assert ctx.task.meta['execution_location']['mode'] == 'local'
            if case == 'wrap_up' and ctx.task.meta['kind'] == 'step':
                return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=False, error='turn limit',
                                  error_kind='error_max_turns', session_id='consumer-session')
            reads.extend((ctx.workdir / 'inputs/producer' / name).read_bytes() for name in contents)
            assert not (ctx.workdir / 'inputs/producer/slurm_job.log').exists()
            if optional_oversize:
                assert not (ctx.workdir / 'inputs/producer/preview.tsv').exists()
            if optional_only:
                assert all(Path(directory) != ctx.workdir / 'inputs/producer' for directory in ctx.read_dirs)
            if case == 'wrap_up':
                assert ctx.task.meta['kind'] == 'wrap_up'
                (ctx.workdir / 'outputs/PARTIAL_STATUS.md').write_text('saved', encoding='utf-8')
        elif not ctx.task.resume_session_id:
            site.workdir = ctx.workdir
            job = await runner._on_submit_raw({'task_id': ctx.task.id, 'agent_id': agent.id, 'request_id': 'r',
                                               'script': str(write_script(site))})
            assert job.get('submitted'), job
            for name, data in remote_contents.items():
                target = site.remote(job['transfer']['data'] + '/' + name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, session_id='producer-session')
        elif nested or optional_only:
            if case == 'nested_with_copy':
                for name in contents:
                    target = ctx.workdir / 'outputs' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((ctx.workdir / 'outputs/outputs' / name).read_bytes())
            (ctx.workdir / 'outputs/slurm_job.log').write_bytes(b'local diagnostic\n')
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text='done')

    monkeypatch.setattr(MockAdapter, 'run', adapter_run)
    producer_meta = {'kind': 'step', 'step_id': 'producer', 'revision': 2, 'outputs': ['outputs/results'],
                     'execution_location': {'mode': 'hpc', 'runner_id': settings.runner.id}}
    try:
        if mixed:
            async def dispatch(task):
                if task.meta['step_id'] == 'producer':
                    assert task.meta['execution_location']['mode'] == 'hpc'
                    assert task.meta['session_runner'] == settings.runner.id
                    assert 'reference_dirs' not in task.meta and 'project_dirs' not in task.meta
                    assert 'precedent_texts' not in task.meta
                    assert str(refs) not in task.model_dump_json() and refs.as_posix() not in task.model_dump_json()
                    outcome = await runner.run_task(task)
                    observed[task.id] = outcome
                    return outcome
                if not expected_error:
                    return await runner.run_task(task)
                with pytest.raises(ValueError, match=expected_error) as exc:
                    await runner.run_task(task)
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error=str(exc.value))

            async def wait_jobs(task_id):
                nonlocal runner
                [job_id] = observed[task_id].pending_jobs
                state_file = site.tmp / 'slurm.json'
                state = json.loads(state_file.read_text())
                state['jobs'][job_id]['state'] = 'COMPLETED'
                state_file.write_text(json.dumps(state))
                await runner._poll_jobs()
                assert runner.jobs[job_id].get('fetched'), runner.jobs[job_id]
                runner = _restart(runner, settings, agent, monkeypatch)
                return {'jobs': [runner.jobs[job_id]]}

            hub = FakeHub(dispatch)
            hub.agents['worker'].update(runner_id=settings.runner.id, whole_request_hpc=True,
                                       hpc_tools=True, builtin_mcp=['hpc'])
            hub.wait_jobs = wait_jobs
            hub.supports_resume = lambda _: True
            hub.step_revision_floor = lambda _rid, sid: 2 if sid == 'producer' else 0
            plan = placed_plan(runner=settings.runner.id, count=2)
            producer, consumer = plan['steps']
            producer.update(id='producer', execution='hpc', outputs=[] if optional_only else ['outputs/results'],
                            instruction='prepare the mock data; PC input is ' + refs.as_posix())
            if nested or optional_only:
                producer['optional_outputs'] = ['outputs/slurm_job.log']
            if optional_oversize:
                producer['optional_outputs'].append('outputs/preview.tsv')
            consumer.update(id='consumer', depends_on=['producer'], outputs=[])
            request = hub.requests['r']
            request.update(plan=plan, research_contract=approved(plan),
                           execution_location={'mode': 'local', 'approval_id': 'choice', 'source': 'fixture',
                                               'mixed_hpc_runner_id': settings.runner.id},
                           references=[{'kind': 'path', 'value': str(refs)}],
                           pi_notes=[{'text': 'Keep this file local: ' + refs.as_posix()}])
            results = {}
            orchestrator = Orchestrator(hub)
            await orchestrator.run_dag('r', 'PC reference ' + refs.as_posix(), plan['steps'], results,
                                       only={'producer'})
            assert results['producer'].ok, results['producer'].error
            assert results['producer'].provenance['hpc_step_revision'] == 2
            source = Path(results['producer'].workdir)
            if case == 'nested_tampered':
                (source / 'outputs/outputs/results/z.tsv').write_bytes(b'x\n')
            # Both sides restart. The PC adapter receives only the verified copies and the local reference.
            runner = _restart(runner, settings, agent, monkeypatch)
            monkeypatch.setattr(runner.scheduler, 'transfer', lambda: pytest.fail('PC handoff called the scheduler'))
            persisted = json.loads(json.dumps({key: row.model_dump(mode='json') for key, row in results.items()}))
            results = {key: TaskResult.model_validate(row) for key, row in persisted.items()}
            await Orchestrator(hub).run_dag('r', 'PC reference ' + refs.as_posix(), plan['steps'], results,
                                           only={'consumer'})
            assert results['consumer'].ok == (case != 'wrap_up' and expected_error is None), results['consumer'].error
            assert reads == ([] if expected_error else list(contents.values()))
            expected = ['step', 'step', 'step']
            if case == 'wrap_up':
                assert results['consumer'].partial_results
                assert 'outputs/PARTIAL_STATUS.md' in results['consumer'].outputs
                expected.append('wrap_up')
            assert [task.meta['kind'] for task in hub.calls] == expected
            return
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


@pytest.mark.parametrize('mode', ['hpc', 'local'])
async def test_mixed_step_accepts_approved_workspace_cache_fix(tmp_path, monkeypatch, mode):
    settings = _settings(tmp_path)
    runner = Runner(settings)
    agent = AgentSpec(id='worker', name='Worker', role='test', engine=Engine.mock)
    monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
    cache = runner.ws_root / 'example' / '.cache'
    cache.mkdir(parents=True)
    (cache / 'cache-data').write_bytes(b'x')
    task = Task(agent_id=agent.id, request_id='r', prompt='', meta={
        'kind': 'facilities_fix', 'step_id': 'step', 'workdir': str(cache.parent),
        'execution_location': {'scope': 'step', 'mode': mode, 'runner_id': settings.runner.id, 'workspace': 'r'},
        'facilities_fix': {'fix_id': 'clear_workspace_cache', 'signature_id': 'disk_full',
                           'action': 'clear fixture cache', 'execution': 'workspace_cache'},
    })
    try:
        outcome = await runner.run_task(task)
        assert outcome.ok, outcome.error
        assert outcome.facilities_fix['ok'] and not cache.exists()
    finally:
        runner.store.close()


@pytest.mark.parametrize('kind', ['analysis_draft', 'audit_repair'])
async def test_compact_follow_up_requires_the_mixed_hpc_receipt(tmp_path, monkeypatch, kind):
    settings = _settings(tmp_path)
    runner = Runner(settings)
    agent = AgentSpec(id='worker', name='Worker', role='test', engine=Engine.mock)
    monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
    source = runner.ws_root / 'source'
    outputs = source / 'outputs'
    outputs.mkdir(parents=True)
    (outputs / 'table.tsv').write_bytes(b'table\n')
    row = _row('table.tsv', b'table\n')
    hashes = {'outputs/table.tsv': row['sha256']}
    inventory = {'request_id': 'r', 'data': ht.job_spec('t', 'work', 'r')['data'],
                 'directories': [], 'files': [row], 'bytes': row['bytes']}
    run = {'request_id': 'r', 'step_id': 's1', 'kind': 'step', 'revision': 2, 'ok': True,
           'pending_jobs': [], 'outputs': list(hashes), 'output_sha256': hashes, 'hpc_remote_outputs': [inventory],
           'hpc_local_receipt': {'sha256': hashes, 'omissions': []}}
    record = {'task': {'request_id': 'r', 'meta': {'step_id': 's1'}}, 'runs': {'producer': run}}
    manifest = source / 'manifest.json'
    manifest.write_text(json.dumps(record), encoding='utf-8')
    results = {'s1': TaskResult(task_id='producer', agent_id=agent.id, ok=True, workdir=str(source),
                                outputs=list(hashes), output_sha256=hashes, provenance={'hpc_step_revision': 2})}
    hub = FakeHub(None)
    plan = placed_plan(runner=settings.runner.id, count=2)
    plan['steps'][0]['execution'] = 'hpc'
    hub.requests['r'].update(plan=plan, research_contract=approved(plan),
                             execution_location={'mode': 'local', 'approval_id': 'choice',
                                                 'mixed_hpc_runner_id': settings.runner.id})
    orchestrator = Orchestrator(hub)
    monkeypatch.setattr(orchestrator, 'run_step', runner.run_task)
    calls = []

    async def adapter_run(_adapter, ctx):
        assert ctx.task.meta['kind'] == kind and ctx.task.meta['session_runner'] == settings.runner.id
        assert ctx.task.meta['execution_location']['mode'] == 'local'
        assert ctx.task.meta['hpc_ancestor_revisions'] == {'s1': 2}
        calls.append((ctx.workdir / 'inputs/s1/table.tsv').read_bytes())
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True)

    monkeypatch.setattr(MockAdapter, 'run', adapter_run)
    draft = SimpleNamespace(step_id='s2')

    def follow_up(**meta):
        task = Task(agent_id=agent.id, request_id='r', prompt='read the table',
                    meta={'kind': kind, **upstream_inputs(results, results), **meta})
        return orchestrator._run_compact_handoff(task, draft, results)

    try:
        refused = await follow_up(session_runner='different-runner')
        assert not refused.ok and 'approved PC runner' in refused.error
        outcome = await follow_up()
        assert outcome.ok, outcome.error
        run.pop('hpc_local_receipt')
        manifest.write_text(json.dumps(record), encoding='utf-8')
        with pytest.raises(ValueError, match='HPC output receipt is missing'):
            await follow_up()
        assert calls == [b'table\n']
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
