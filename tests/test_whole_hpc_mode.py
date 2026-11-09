"""Whole-HPC DAG concurrency over one fake gateway session. Never contact HPC."""
import asyncio

import pytest

from labhq import hpc_transfer as ht
from labhq.orchestrator.cso import Orchestrator
from tests.test_cso import FakeHub, result
from tests.test_hpc_transfer_1308 import cluster


@pytest.mark.parametrize('failed', [False, True])
async def test_parallel_roots_keep_dependencies_outputs_and_one_login(cluster, tmp_path, failed):
    started = set()
    both = asyncio.Event()
    completed = set()
    specs = {}

    async def dispatch(task):
        sid = task.meta['step_id']
        if sid == 'join':
            assert completed == {'left', 'right'}
            assert set(task.meta['upstream_artifacts']) == {'left', 'right'}
            return result(task, text='joined')
        assert task.meta['upstream_artifacts'] == {}
        started.add(sid)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 3)  # fails if whole-HPC still serializes independent steps
        spec = ht.job_spec(task.id, sid, 'r')
        specs[sid] = spec

        def files():
            transfer = cluster.transfer()
            ht.upload_job(transfer, spec, '#!/bin/sh\necho mock\n', tmp_path)
            transfer.upload_bytes(spec['work'] + '/result.txt', sid.encode())
            transfer.fetch_file(spec['work'] + '/result.txt', tmp_path / sid, name='result.txt')

        await asyncio.to_thread(files)
        completed.add(sid)
        return result(task, ok=not (failed and sid == 'left'), text=sid,
                      error='mock compute failed' if failed and sid == 'left' else None,
                      workdir=str(tmp_path / sid), outputs=['outputs/result.txt'])

    hub = FakeHub(dispatch)
    hub.agents['worker'].update(runner_id='cluster', hpc_tools=True, whole_request_hpc=True, builtin_mcp=['hpc'])
    hub.agent_runner = {'worker': 'cluster'}
    hub.s.orchestrator.max_parallel_steps = 2
    hub.requests['r']['execution_location'] = {'mode': 'hpc', 'runner_id': 'cluster'}
    steps = [dict(id=sid, agent_id='worker', instruction=sid, depends_on=deps,
                  outputs=[] if sid == 'join' else ['outputs/result.txt'])
             for sid, deps in [('left', []), ('right', []), ('join', ['left', 'right'])]]
    results = {}
    await Orchestrator(hub)._run_dag('r', 'mock research', steps, results)
    assert started == completed == {'left', 'right'}, {s: r.error for s, r in results.items()}
    assert results['join'].ok is (not failed)
    assert [t.meta['step_id'] for t in hub.calls].count('join') == (0 if failed else 1)
    assert specs['left']['base'] == specs['right']['base']
    assert specs['left']['work'] != specs['right']['work']
    for sid in started:
        assert (tmp_path / sid / 'result.txt').read_text() == sid
        assert cluster.path(specs[sid]['work'] + '/result.txt').read_text() == sid
    assert cluster.logins() == 1
