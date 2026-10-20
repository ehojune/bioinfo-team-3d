"""Review regressions: synthetic files, fake dispatch and in-process coroutines only."""
import copy
import json

import pytest

from labhq.orchestrator.cso import Orchestrator
from labhq.models import Task, TaskResult
from labhq.research import continuation as c
from labhq.research.contract import plan_sha256
from tests.test_continuation_797 import archive, AGENTS
from tests.test_research_continue import CP1, APPROVE, _hub, _steps, _ledger
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401
from tests.test_research_protocol import MiniHub
from tests.test_research_cp2 import _settings


@pytest.mark.asyncio
async def test_dispatched_branch_context_is_the_exact_node_identity_context(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE], reviews=[{'verdict': 'accept', 'issues': []}])
    reply = hub.reply
    async def branched(task):
        result = await reply(task)
        if task.meta['kind'] == 'plan':
            result.structured['branches'] = [
                {'id': b, 'question': 'question ' + b, 'evidence_type': 'fixture', 'data_boundary': 'public'}
                for b in ('a', 'b')]
            for step, branch in zip(result.structured['steps'], ('a', 'b')):
                step['branch'] = branch
            result.structured['steps'][1]['depends_on'] = []
            third = copy.deepcopy(result.structured['steps'][1])
            third.update(id='s3', branch='synthesis', instruction='synthesize branches', depends_on=['s1', 's2'],
                         claim_ids=['c3'], evidence_slots=[{**third['evidence_slots'][0], 'id': 'e3'}],
                         outputs=['result3.tsv'])
            result.structured['steps'].append(third)
        return result
    hub.reply = branched
    await Orchestrator(hub).run_request('r')
    assert 'plan' in hub.requests['r'], hub.requests['r'].get('report')
    plan = hub.requests['r']['plan']
    for step in plan['steps']:
        task = next(t for t in hub.calls if t.meta.get('step_id') == step['id'])
        marker = 'Frozen question, hypotheses and protocol, approved by the PI at CP1:\n'
        delivered, _ = json.JSONDecoder().raw_decode(task.prompt.split(marker)[1])
        assert delivered == json.loads(c.node_context(plan, step))
    changed = copy.deepcopy(plan)
    changed['branches'][1]['question'] = 'different question b'
    assert c.node_context(plan, plan['steps'][0]) == c.node_context(changed, changed['steps'][0])


@pytest.mark.asyncio
@pytest.mark.parametrize('configuration', ['agent', 'engine'])
async def test_cp1_pending_configuration_is_not_historical_execution_proof(tmp_path, configuration):
    hub = _hub(tmp_path, [CP1, APPROVE], reviews=[{'verdict': 'accept', 'issues': []}])
    if configuration == 'engine':
        hub.agents['worker']['engine'] = 'codex'
    original_agent = copy.deepcopy(hub.agents['worker'])
    original_args = list(hub.s.engines.codex.extra_args)
    before = c.tool_signatures({'steps': [{'id': 's1', 'agent_id': 'worker'}]}, hub.agents, hub.s.engines)
    def change():
        if configuration == 'agent':
            hub.agents['worker']['tools'] = ['fixture-B']
        else:
            hub.s.engines.codex.extra_args = [*original_args, '--fixture-B']
    hub.hooks[1] = change
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    actual = c.tool_signatures(req['plan'], hub.agents, hub.s.engines)
    assert actual['s1'] != before['s1']
    assert req['reuse_tools'] == actual
    # Returning to A must not retroactively label B-produced results as A.
    hub.agents['worker'] = original_agent
    hub.s.engines.codex.extra_args = original_args
    archived = c.archive_round(req, req['results'])
    carry = c.carry_over(archived, req['plan'], req['research_contract']['pack_snapshot'], [])
    assert set(c.proof_problems(archived, req['plan'], carry,
               c.tool_signatures(req['plan'], hub.agents, hub.s.engines))) == set(carry.reuse)


def partial_hub(monkeypatch):
    old = archive()
    plan = copy.deepcopy(old['plan'])
    plan['steps'][1]['instruction'] += ' corrected in current round'
    plan['steps'][2]['instruction'] += ' pending work'
    async def reply(task):
        pytest.fail('preparing or verifying reuse must not dispatch')
    hub = MiniHub(_settings(), reply, mode='orchestrate', work_kind='research')
    hub.agents = copy.deepcopy(AGENTS)
    req = hub.requests['r']
    req.update(plan=plan, research_contract={'round': 2, 'plan_sha256': plan_sha256(plan),
        'pack_snapshot': {}, 'rounds': [old], 'continuation': {
            'plan_sha256': plan_sha256(plan), 'round': 2}})
    monkeypatch.setattr(c, 'verify_reuse', lambda *a: {})
    return hub, Orchestrator(hub)


def approve(req):
    contract = req['research_contract']
    contract['approval'] = {'approved': True, 'status': 'approved',
        'target_sha256': contract['plan_sha256'],
        'reuse_snapshot_sha256': contract['continuation']['reuse_snapshot']['sha256']}


@pytest.mark.asyncio
async def test_reapproval_keeps_completed_current_round_and_seals_it(monkeypatch):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    await orch._prepare_research_reuse('r', req['plan'])
    approve(req)
    carried = req['research_contract']['continuation']
    completed = copy.deepcopy(req['research_contract']['rounds'][0]['results']['s2'])
    completed['task_id'] = 'current-round-s2'
    completed['structured'] = _ledger(plan_sha256(req['plan']), req['plan']['steps'][1])
    req['results']['s2'] = completed
    req.setdefault('reuse_tools', {})['s2'] = c.tool_signatures(req['plan'], hub.agents, hub.s.engines)['s2']
    carried.update(dispatch_started=True, verified=True)
    # A changed execution configuration invalidates the old CP1, after s2 completed.
    hub.agents['worker']['tools'] = ['changed']
    assert not await orch._verify_research_reuse('r', req['plan'])
    assert not orch._continuation_unstarted(req)  # no rollback of actual partial progress
    hub.agents = copy.deepcopy(AGENTS)
    await orch._prepare_research_reuse('r', req['plan'])
    assert req['results']['s2'] == completed
    assert carried['reuse'] == ['s1', 's2'] and set(carried['rerun']) == {'s3'}
    assert carried['reused_from']['s2']['round'] == 2
    assert set(carried['reuse_snapshot']['nodes']) == {'s1', 's2'}
    approve(req)
    assert await orch._verify_research_reuse('r', req['plan'])
    req['results']['s2']['output_sha256']['outputs/result2.tsv'] = 'e' * 64
    assert not await orch._verify_research_reuse('r', req['plan'])
    assert not hub.calls


@pytest.mark.asyncio
async def test_partial_round_drift_preserves_new_result_and_never_dispatches_next(tmp_path, monkeypatch):
    from tests.test_research_continue import CONTINUE
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, CP1, APPROVE])
    reply = hub.reply
    async def three_steps(task):
        result = await reply(task)
        if task.meta['kind'] == 'plan':
            third = copy.deepcopy(result.structured['steps'][1])
            third.update(id='s3', instruction='work 3', depends_on=['s2'],
                         claim_ids=['c3'], evidence_slots=[{**third['evidence_slots'][0], 'id': 'e3'}],
                         outputs=['result3.tsv'])
            result.structured['steps'].append(third)
        return result
    hub.reply = three_steps
    changed = {'value': False, 'armed': True}
    monkeypatch.setattr(c, 'verify_reuse', lambda results, _: {'s1': 'changed'} if changed['value'] else {})
    publish = hub.publish
    async def drift(event):
        if (changed['armed'] and event.get('type') == 'request.step_attempt'
                and event.get('data', {}).get('step_id') == 's3'
                and hub.requests['r'].get('research_contract', {}).get('round') == 2):
            changed['value'] = True
        await publish(event)
    hub.publish = drift
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    assert req['outcome'] == 'reuse_approval_required'
    assert req['research_contract']['round'] == 2
    assert req['results']['s2']['structured']['plan_sha256'] == plan_sha256(req['plan'])
    assert req['results']['s2']['task_id'] != req['research_contract']['rounds'][0]['results']['s2']['task_id']
    assert _steps(hub).count('s2') == 2 and _steps(hub).count('s3') == 1
    completed = copy.deepcopy(req['results']['s2'])
    # A dependent completed result cannot be admitted while its upstream proof is still missing.
    with pytest.raises(c.ReuseApprovalRequired):
        await Orchestrator(hub)._prepare_research_reuse('r', req['plan'])
    assert req['results']['s2'] == completed
    changed.update(value=False, armed=False)  # fixture proof restored, then explicit new CP1
    req['status'] = 'interrupted'
    before = len(hub.approvals)
    await Orchestrator(hub).run_request('r', resume=True)
    assert [card['kind'] for card in hub.approvals[before:]] == ['research_plan', 'research_evidence']
    assert hub.approvals[before]['detail']['continuation']['reuse'] == ['s1', 's2']
    assert TaskResult.model_validate(req['results']['s2']) == TaskResult.model_validate(completed)
    assert req['results']['s2']['status'] == 'done' and _steps(hub).count('s2') == 2
    assert _steps(hub).count('s3') == 2 and req['outcome'] == 'research_reported'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'exception', 'recovered'])
@pytest.mark.parametrize('kind', ['step', 'result_correction', 'slot_repair'])
async def test_unproved_or_mixed_dispatch_configuration_cannot_become_reuse_proof(outcome, kind):
    async def reply(task):
        hub.agents['worker']['tools'] = ['changed during dispatch']
        if outcome == 'exception':
            raise ValueError('synthetic failed dispatch')
        return TaskResult(task_id='older-task' if outcome == 'recovered' else task.id,
                          agent_id=task.agent_id, ok=True)
    hub = MiniHub(_settings(), reply, mode='orchestrate', work_kind='research')
    hub.s.orchestrator.step_max_attempts = 1
    plan = archive()['plan']
    hub.requests['r'].update(plan=plan, research_contract={'plan_sha256': plan_sha256(plan)})
    await Orchestrator(hub).run_step(Task(agent_id='worker', request_id='r', prompt='fixture',
                                         meta={'kind': kind, 'step_id': 's1'}))
    assert hub.requests['r']['reuse_tools']['s1'] is None


def test_prescoped_execution_signature_cannot_prove_the_new_prompt_contract():
    old = archive()
    old['reuse_tools'] = {sid: c._digest(AGENTS['worker']) for sid in old['results']}
    carry = c.carry_over(old, old['plan'], {}, [])
    assert set(c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))) == set(carry.reuse)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['tool', 'ledger', 'file', 'unavailable'])
async def test_failed_current_round_proof_keeps_the_original_record(monkeypatch, failure):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    await orch._prepare_research_reuse('r', req['plan'])
    row = copy.deepcopy(req['research_contract']['rounds'][0]['results']['s2'])
    row['structured'] = _ledger(plan_sha256(req['plan']), req['plan']['steps'][1])
    row['task_id'] = 'completed-current-s2'
    req['results']['s2'] = row
    req['reuse_tools']['s2'] = c.tool_signatures(req['plan'], hub.agents, hub.s.engines)['s2']
    req['research_contract']['continuation']['dispatch_started'] = True
    if failure == 'tool':
        req['reuse_tools']['s2'] = None
    elif failure == 'ledger':
        row['structured']['plan_sha256'] = 'wrong'
    else:
        def verify(results, _):
            if 's2' in results:
                if failure == 'unavailable':
                    raise OSError('synthetic unavailable verifier')
                return {'s2': 'changed'}
            return {}
        monkeypatch.setattr(c, 'verify_reuse', verify)
    before = copy.deepcopy(req['results'])
    with pytest.raises(c.ReuseApprovalRequired):
        await orch._prepare_research_reuse('r', req['plan'])
    assert req['results'] == before and not hub.calls
