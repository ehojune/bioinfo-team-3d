"""Remote reuse with synthetic runner records and a fake remote hash service."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from labhq import hpc_transfer as ht
from labhq.orchestrator.cso import Orchestrator
from labhq.research import continuation as c
from labhq.research.contract import plan_sha256
from labhq.runner.step_inputs import verify_reuse_outputs
from tests.test_continuation_797 import AGENTS, archive
from tests.test_hpc_remote_data import inventory
from tests.test_research_cp2 import _settings
from tests.test_research_protocol import MiniHub


@pytest.fixture
def remote_case(tmp_path, monkeypatch):
    settings = _settings()
    root = tmp_path / 'workspaces'
    settings.runner.workspace_root = str(root)
    source = root / '2026-11-14' / 'wd'
    (source / 'outputs').mkdir(parents=True)
    old = archive()
    old['results'] = {'s1': old['results']['s1']}
    result = old['results']['s1']
    manifest = inventory(request='r', data=b'synthetic', path='result1.tsv')
    result.update(workdir=str(source), workdir_id='wd', output_sha256={},
                  provenance={'hpc_remote_data': [manifest]})
    record = {'task': {'request_id': 'r', 'meta': {'step_id': 's1'}},
              'runs': {result['task_id']: {'ok': True, 'request_id': 'r', 'step_id': 's1',
                                          'hpc_remote_outputs': [copy.deepcopy(manifest)]}}}
    (source / 'manifest.json').write_text(json.dumps(record), encoding='utf-8')
    remote = {manifest['data'] + '/result1.tsv': b'synthetic'}

    def verify(transfer, candidate, rid):
        ht.validate_remote_manifest(candidate, rid)
        for row in candidate['files']:
            data = remote.get(candidate['data'] + '/' + row['path'])
            if data is None or len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ht.TransferError('remote content differs')
    monkeypatch.setattr(ht, 'verify_remote_data', verify)
    return settings, old, record, source, remote


def test_remote_proof_is_reused_only_after_runner_rechecks(remote_case):
    settings, old, _, _, remote = remote_case
    result = old['results']['s1']
    carry = c.carry_over(old, old['plan'], {}, [])
    assert 's1' in carry.reuse
    assert 's1' not in c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))
    assert not verify_reuse_outputs('r', {'s1': result}, settings, object())
    key = next(iter(remote))
    remote[key] = b'changed'
    with pytest.raises(ht.TransferError):
        verify_reuse_outputs('r', {'s1': result}, settings, object())
    remote.pop(key)
    with pytest.raises(ht.TransferError):
        verify_reuse_outputs('r', {'s1': result}, settings, object())


@pytest.mark.parametrize('states', [['failed', 'completed'], ['completed', 'completed']])
def test_successful_job_manifests_remain_bound_to_reuse(remote_case, tmp_path, states):
    from tests.test_hpc_whole_request import _attempts

    settings, old, record, source, remote = remote_case
    runner, ws = _attempts(tmp_path, states)
    ws.task.meta['outputs'] = ['result1.tsv', 'result2.tsv']
    for index, job in enumerate(runner.jobs.values()):
        job['name'] = f'job{index}' if states[0] == 'completed' else 'same'
        if not job.get('remote_manifest'):
            continue
        name = f'result{index + 1 if states[0] == "completed" else 1}.tsv'
        data = f'synthetic{index}'.encode()
        manifest = inventory(request='r', data=data, path=name)
        manifest['data'] = ht.job_spec(f'task{index}', 'align', 'r')['data']
        job['remote_manifest'] = manifest
        remote[manifest['data'] + '/' + name] = data
    manifests = runner._remote_step_outputs(ws)
    for manifest in manifests:
        manifest.pop('_local_copy_sha256', None)
        manifest.pop('_nested_candidates', None)
    result = old['results']['s1']
    result['outputs'] = ['outputs/' + row['path'] for m in manifests for row in m['files']]
    result['provenance']['hpc_remote_data'] = manifests
    record['runs'][result['task_id']]['hpc_remote_outputs'] = copy.deepcopy(manifests)
    (source / 'manifest.json').write_text(json.dumps(record), encoding='utf-8')
    assert len(manifests) == (1 if states[0] == 'failed' else 2)
    assert c._artifacts(old['plan']['steps'][0], result)['outputs/result1.tsv']
    assert not verify_reuse_outputs('r', {'s1': result}, settings, object())


@pytest.mark.parametrize('mutation', ['run', 'request', 'inventory', 'duplicate', 'local_conflict'])
def test_remote_proof_refuses_wrong_run_and_conflicts(remote_case, mutation):
    settings, old, record, source, _ = remote_case
    result = old['results']['s1']
    if mutation == 'run':
        record['runs'][result['task_id']]['ok'] = False
    elif mutation == 'request':
        record['task']['request_id'] = 'another'
    elif mutation == 'inventory':
        record['runs'][result['task_id']]['hpc_remote_outputs'][0]['files'][0]['sha256'] = 'f' * 64
    elif mutation == 'duplicate':
        result['provenance']['hpc_remote_data'].append(copy.deepcopy(result['provenance']['hpc_remote_data'][0]))
    else:
        (source / 'outputs' / 'result1.tsv').write_bytes(b'conflict')
    (source / 'manifest.json').write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError):
        verify_reuse_outputs('r', {'s1': result}, settings, object())


@pytest.mark.asyncio
async def test_runner_verify_dispatch_uses_no_adapter(remote_case, monkeypatch):
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    settings, old, _, _, remote = remote_case
    settings.hpc.scheduler = 'mock'
    runner = Runner(settings)
    runner.scheduler = SimpleNamespace(transfer=lambda: object())
    async def no_adapter(*args, **kwargs):
        pytest.fail('reuse verification must not start an adapter')
    monkeypatch.setattr(runner, '_run_task', no_adapter)
    task = Task(agent_id='worker', request_id='r', prompt='', meta={
        'kind': 'hpc_reuse_verify', 'execution_location': {'mode': 'hpc', 'runner_id': settings.runner.id},
        'verification_id': 'a' * 32, 'plan_sha256': 'b' * 64, 'results': {'s1': old['results']['s1']}})
    result = await runner.run_task(task)
    assert result.ok and result.structured['failures'] == {}
    remote[next(iter(remote))] = b'changed'
    assert not (await runner.run_task(task)).ok
    task.meta['execution_location']['runner_id'] = 'other'
    assert not (await runner.run_task(task)).ok


@pytest.mark.asyncio
async def test_card_and_resume_require_fresh_runner_observation(remote_case):
    from labhq.runner.daemon import Runner

    settings, old, _, _, remote = remote_case
    settings.hpc.scheduler = 'mock'
    settings.runner.id = 'runner'
    runner = Runner(settings)
    runner.scheduler = SimpleNamespace(transfer=lambda: object())
    plan = old['plan']
    stale = []
    async def reply(task):
        if stale:
            return stale[0]
        result = await runner.run_task(task)
        stale.append(result)
        return result
    hub = MiniHub(settings, reply, mode='orchestrate', work_kind='research')
    hub.agents = {'worker': {**AGENTS['worker'], 'whole_request_hpc': True, 'hpc_tools': True,
                             'builtin_mcp': ['hpc']}}
    hub.agent_runner = {'worker': 'runner'}
    hub.runners = {'runner': object()}
    hub.runner_capabilities = {'runner': {'hpc_reuse_verify': True}}
    req = hub.requests['r']
    req.update(execution_location={'mode': 'hpc', 'runner_id': 'runner'}, plan=plan,
               research_contract={'plan_sha256': plan_sha256(plan), 'pack_snapshot': {}, 'rounds': [old],
                                  'continuation': {'plan_sha256': plan_sha256(plan), 'round': 2}})
    orch = Orchestrator(hub)
    orch._research_reuse_tools = lambda rid, plan: c.tool_signatures(plan, AGENTS)
    await orch._prepare_research_reuse('r', plan)
    carried = req['research_contract']['continuation']
    assert carried['reuse'] == ['s1']
    req['research_contract']['approval'] = {'approved': True, 'target_sha256': plan_sha256(plan),
                                            'reuse_snapshot_sha256': carried['reuse_snapshot']['sha256']}
    remote[next(iter(remote))] = b'changed'
    assert not await orch._verify_research_reuse('r', plan)
    assert req['outcome'] == 'reuse_approval_required'
    assert len(hub.calls) == 2 and hub.calls[0].id != hub.calls[1].id
