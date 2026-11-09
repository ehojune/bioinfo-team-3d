"""Request remote data boundaries. Fake gateway/local helper only; never contact HPC."""
import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import hpc_transfer as ht
from tests.test_hpc_transfer_1308 import cluster, site, write_script


def inventory(request='r', data=b'large', path='tx2gene.tsv'):
    spec = ht.job_spec('t', 'align', request)
    return {'request_id': request, 'data': spec['data'], 'bytes': len(data),
            'files': [{'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]}


def helper_transfer(cluster, **limits):
    """Run the Linux helper against the gateway sandbox, using local Python instead of any remote shell."""
    transfer = cluster.transfer(**limits)
    run = transfer.run

    def remote(command):
        if command.startswith('python3 '):
            if os.name == 'nt':
                pytest.skip('remote POSIX descriptor helper is exercised by Linux CI')
            args = shlex.split(command)
            args[0] = sys.executable
            args[1] = str(cluster.path(args[1][len('/hpc/work/'):]))
            args[2] = str(cluster.path(''))
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            return SimpleNamespace(exit_code=result.returncode, output=result.stdout + result.stderr)
        return run(command)

    transfer.run = remote
    return transfer


def prepare(cluster, transfer, tmp_path, contents=None):
    spec = ht.job_spec('t', 'align', 'r')
    ht.upload_job(transfer, spec, '#!/bin/bash\necho test\n', tmp_path)
    for name, data in (contents or {'tx2gene.tsv': b'x' * 2000, 'small.csv': b'a,b\n1,2\n'}).items():
        path = cluster.path(spec['work'] + '/' + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return spec


def test_request_folder_is_stable_but_job_folders_are_fresh():
    a, b = ht.job_spec('t', 'a', 'r'), ht.job_spec('t', 'b', 'r')
    assert a['base'] == b['base'] and a['dir'] != b['dir']
    assert a['base'] != ht.job_spec('t', 'a', 'other')['base']
    assert ht.job_spec('t', 'a')['base'] == 't'  # legacy non-request submissions stay isolated


def test_persisted_job_folder_replaced_by_in_root_link_is_refused_before_any_write(cluster, tmp_path):
    transfer = cluster.transfer()
    m = inventory()
    folder = str(Path(m['data']).parent).replace('\\', '/')
    transfer.ensure_dir(str(Path(folder).parent).replace('\\', '/'))
    transfer.ensure_dir('other-job')
    try:
        cluster.path(folder).symlink_to(cluster.path('other-job'), target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(ht.PathRejected): ht.verify_remote_data(transfer, m, 'r')
    assert not list(cluster.path('other-job').iterdir())


@pytest.mark.parametrize('change', ['request', 'folder', 'hash', 'size', 'total', 'duplicate', 'traversal'])
def test_invalid_manifest_never_becomes_an_input(change):
    m = inventory()
    if change == 'request': m['request_id'] = 'other'
    if change == 'folder': m['data'] = ht.job_spec('t', 'a', 'other')['data']
    if change == 'hash': m['files'][0]['sha256'] = 'bad'
    if change == 'size': m['files'][0]['bytes'] = -1
    if change == 'total': m['bytes'] += 1
    if change == 'duplicate': m['files'] *= 2
    if change == 'traversal': m['files'][0]['path'] = '../secret'
    with pytest.raises(ht.TransferError): ht.validate_remote_manifest(m, 'r')


def test_large_outputs_stay_remote_small_outputs_and_manifest_come_home(cluster, tmp_path):
    transfer = helper_transfer(cluster, fetch_max_bytes=1000)
    spec = prepare(cluster, transfer, tmp_path)
    result = ht.fetch_results(transfer, spec, tmp_path / 'logs', tmp_path / 'outputs')
    assert [x['path'] for x in result['fetched']] == ['small.csv']
    assert result['remote']['bytes'] == 2008
    assert not (tmp_path / 'outputs/tx2gene.tsv').exists()
    assert cluster.path(spec['data'] + '/tx2gene.tsv').read_bytes() == b'x' * 2000
    assert ht.verify_remote_data(transfer, result['remote'], 'r').endswith(spec['data'])
    assert json.loads((tmp_path / 'logs/align.fetch.json').read_text())['remote'] == result['remote']
    assert cluster.logins() == 1


@pytest.mark.parametrize('problem', ['changed', 'missing', 'extra', 'link', 'directory_link'])
def test_next_job_rejects_remote_changes_before_scheduler_submit(cluster, tmp_path, problem):
    transfer = helper_transfer(cluster)
    spec = prepare(cluster, transfer, tmp_path, {'result.txt': b'original'})
    m = ht.fetch_results(transfer, spec, tmp_path / 'logs', tmp_path / 'outputs')['remote']
    path = cluster.path(spec['data'] + '/result.txt')
    path.chmod(0o600)
    if problem == 'changed': path.write_bytes(b'changed')
    if problem == 'missing': path.unlink()
    if problem == 'extra': path.with_name('extra.txt').write_bytes(b'extra')
    if problem == 'link':
        path.unlink(); path.symlink_to(tmp_path / 'secret')
    if problem == 'directory_link':
        cluster.path(spec['data'] + '/jump').symlink_to(tmp_path, target_is_directory=True)
    consumer = ht.job_spec('next', 'consume', 'r')
    consumer['remote_inputs'] = {'s1': m}
    with pytest.raises(ht.TransferError): ht.upload_job(transfer, consumer, 'echo consume', tmp_path)
    assert not cluster.path(consumer['script']).exists()


def test_fetch_retry_recovers_after_snapshot_was_already_moved(cluster, tmp_path):
    transfer = helper_transfer(cluster)
    spec = prepare(cluster, transfer, tmp_path)
    # A process killed while writing an older manifest must not reserve the next attempt's temporary name.
    cluster.path(spec['dir'] + '/remote-data.tmp').write_bytes(b'interrupted')
    first = ht.fetch_results(transfer, spec, tmp_path / 'logs', tmp_path / 'outputs')
    # A restarted runner knows only the persisted spec. No in-memory transfer state is required.
    second = ht.fetch_results(helper_transfer(cluster), json.loads(json.dumps(spec)),
                              tmp_path / 'logs', tmp_path / 'outputs')
    assert first['remote'] == second['remote']


def test_remote_executable_stays_executable_and_read_only(cluster, tmp_path):
    transfer = helper_transfer(cluster)
    spec = prepare(cluster, transfer, tmp_path, {'tool': b'#!/bin/sh\necho ok\n'})
    cluster.path(spec['work'] + '/tool').chmod(0o700)
    ht.fetch_results(transfer, spec, tmp_path / 'logs', tmp_path / 'outputs')
    assert cluster.path(spec['data'] + '/tool').stat().st_mode & 0o777 == 0o500


def test_total_fetch_budget_does_not_limit_remote_completion(cluster, tmp_path):
    transfer = helper_transfer(cluster, fetch_total_bytes=1)
    spec = prepare(cluster, transfer, tmp_path)
    result = ht.fetch_results(transfer, spec, tmp_path / 'logs', tmp_path / 'outputs')
    assert result['fetched'] == [] and len(result['remote']['files']) == 2


def test_only_successful_same_request_ancestor_inventory_is_admitted(tmp_path):
    from labhq.runner.step_inputs import remote_ancestor_inputs
    root = tmp_path / 'root'; source = root / 'producer'; source.mkdir(parents=True)
    m = inventory()
    record = {'task': {'request_id': 'r', 'meta': {'step_id': 's1'}},
              'runs': {'producer': {'ok': True, 'request_id': 'r', 'step_id': 's1', 'hpc_remote_outputs': [m]}}}
    (source / 'manifest.json').write_text(json.dumps(record))
    ws = SimpleNamespace(dir=root / 'consumer', task=SimpleNamespace(request_id='r'))
    artifact = {'task_id': 'producer', 'workdir': str(source), 'outputs': ['outputs/tx2gene.tsv'],
                'output_sha256': {'outputs/tx2gene.tsv': m['files'][0]['sha256']}}
    assert remote_ancestor_inputs(ws, {'s1': artifact}, root) == {'s1': [m]}
    for change in ('request', 'failed', 'hash'):
        bad, bad_artifact = copy.deepcopy(record), copy.deepcopy(artifact)
        if change == 'request': bad['task']['request_id'] = 'other'
        if change == 'failed': bad['runs']['producer']['ok'] = False
        if change == 'hash': bad_artifact['output_sha256']['outputs/tx2gene.tsv'] = '0' * 64
        (source / 'manifest.json').write_text(json.dumps(bad))
        with pytest.raises(ValueError): remote_ancestor_inputs(ws, {'s1': bad_artifact}, root)


async def test_runner_accepts_remote_required_output_and_passes_it_to_next_step_after_restart(site, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    from labhq.models import AgentSpec, Engine, Task, TaskResult
    from labhq.runner.daemon import Runner
    from tests.test_workspace_boundary import _settings

    settings = _settings(site.tmp)
    settings.hpc = site.settings.hpc
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    settings.runner.mock_delay_s = 0
    runner = Runner(settings)
    agent = AgentSpec(id='worker', name='Worker', role='test', engine=Engine.mock, builtin_mcp=['hpc'])
    seen = []

    def verify(transfer, manifest, request_id):
        ht.validate_remote_manifest(manifest, request_id)
        seen.append(manifest)
        return transfer.path(manifest['data'])

    monkeypatch.setattr(ht, 'verify_remote_data', verify)

    async def adapter_run(adapter, ctx):
        ctx.before_spawn()
        step = ctx.task.meta['step_id']
        if ctx.task.resume_session_id:
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text='done')
        if step == 's2':
            remote = ctx.task.meta['hpc_remote_inputs']['s1/0']
            assert remote in seen
            assert runner.scheduler.transfer().path(remote['data']) in ctx.task.context
            assert not (ctx.workdir / 'inputs/s1/tx2gene.tsv').exists()
        site.workdir = ctx.workdir
        path = write_script(site)
        submitted = await runner._on_submit_raw({'task_id': ctx.task.id, 'agent_id': agent.id,
                                                 'request_id': 'r', 'script': str(path)})
        assert submitted.get('submitted'), submitted
        job = runner.jobs[submitted['job_id']]
        m = inventory(path='tx2gene.tsv' if step == 's1' else 'de.csv', data=b'x' * 20000000)
        m['data'] = submitted['transfer']['data']
        # Stand in only for remote compute and hashing; submission/tracking/result admission remain real runner code.
        job.update(terminal=True, state='completed', exit_status=0, fetched=True, remote_manifest=m)
        runner.store.put('job', job['job_id'], job)
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text='submitted', session_id='session-' + step)

    monkeypatch.setattr(MockAdapter, 'run', adapter_run)
    results = []
    try:
        for step, output in [('s1', 'tx2gene.tsv'), ('s2', 'de.csv')]:
            monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
            upstream = {} if not results else {'s1': {'task_id': results[0].task_id,
                'workdir': results[0].workdir, 'outputs': results[0].outputs,
                'output_sha256': results[0].output_sha256}}
            meta = {
                'kind': 'step', 'step_id': step, 'execution_location': {'mode': 'hpc', 'runner_id': settings.runner.id},
                'upstream_artifacts': upstream, 'upstream_steps': {s: x['workdir'] for s, x in upstream.items()},
                'outputs': ['outputs/' + output]}
            pending = await runner.run_task(Task(agent_id=agent.id, request_id='r', prompt='test', meta=meta))
            assert pending.ok and pending.pending_jobs
            runner.store.close()
            runner = Runner(settings)
            monkeypatch.setattr(runner.registry, 'get', lambda _: agent)
            result = await runner.run_task(Task(agent_id=agent.id, request_id='r', prompt='wake', meta=meta,
                                               resume_session_id='session-' + step))
            assert result.ok, result.error
            assert not result.missing_outputs
            assert 'outputs/' + output in result.output_sha256
            assert not (Path(result.workdir) / 'outputs' / output).exists()
            assert result.provenance['hpc_remote_data'][0]['bytes'] == 20000000
            results.append(result)
            if step == 's1':
                runner.store.close()
                runner = Runner(settings)  # re-admit inventory from persisted run and job rows
    finally:
        runner.store.close()
