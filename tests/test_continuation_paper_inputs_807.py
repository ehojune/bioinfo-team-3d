"""Paper method inputs in reuse: synthetic receipts only, no conversion/install or source reads."""
import copy
import json

import pytest

from labhq.recruit import paper_skills
from labhq.research import continuation as c
from labhq.research.contract import plan_sha256
from tests.test_continuation_797 import archive, AGENTS
from tests.test_continuation_review_807 import partial_hub, approve
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401


def paper_archive():
    old = archive()
    plan = old['plan']
    plan['paper_skills'] = [{'paper_id': f'doi:10.1234/fixture{i}', 'title': f'method {i}',
        'reason': 'declared scientific method', 'step_ids': [f's{i}']} for i in (1, 2)]
    plan['paper_skill_results'] = [{**row, 'agent_ids': ['worker'], 'status': 'ready',
        'skill_dir': f'fixture-skills/method{i}', 'skill_sha256': str(i) * 64,
        'paper2agent_commit': 'a' * 40} for i, row in enumerate(plan['paper_skills'], 1)]
    old['plan_sha256'] = plan_sha256(plan)
    for row in old['results'].values():
        row['structured']['plan_sha256'] = old['plan_sha256']
    return old


@pytest.mark.parametrize('field,value', [
    ('paper_id', 'doi:10.1234/replacement'), ('title', 'different method'),
    ('reason', 'different scientific rationale'), ('step_ids', ['s2']),
])
def test_changed_own_selection_refuses_node_and_downstream(field, value):
    old = paper_archive()
    plan = copy.deepcopy(old['plan'])
    plan['paper_skills'][0][field] = value
    carry = c.carry_over(old, plan, {}, [])
    assert 's1' not in carry.reuse and 's3' not in carry.reuse


@pytest.mark.parametrize('field,value', [
    ('skill_sha256', 'f' * 64), ('skill_dir', 'fixture-skills/replacement'),
    ('paper2agent_commit', 'b' * 40), ('status', 'failed'),
    ('agent_ids', ['different-worker']), ('step_ids', ['s2']),
    ('method_options', {'normalization': 'different'}),
])
def test_changed_attached_receipt_refuses_node_and_downstream(field, value):
    old = paper_archive()
    plan = copy.deepcopy(old['plan'])
    plan['paper_skill_results'][0][field] = value
    # Actual dispatch metadata changes too: this is not an inferred identity from aggregate counts.
    assert paper_skills.attachments(old['plan'], 's1', 'worker', 'r') != paper_skills.attachments(
        plan, 's1', 'worker', 'r')
    carry = c.carry_over(old, plan, {}, [])
    assert 's1' not in carry.reuse and 's3' not in carry.reuse


def test_unrelated_paper_change_keeps_independent_node_and_its_dependency_chain():
    old = paper_archive()
    plan = copy.deepcopy(old['plan'])
    plan['paper_skills'][1]['reason'] += ' changed'
    plan['paper_skill_results'][1]['skill_sha256'] = 'f' * 64
    carry = c.carry_over(old, plan, {}, [])
    assert carry.reuse == ['s1', 's3'] and set(carry.rerun) == {'s2'}
    assert not c.proof_problems(old, plan, carry, c.tool_signatures(plan, AGENTS))
    before = c.reuse_snapshot(old['plan'], {}, c.carry_over(old, old['plan'], {}, []),
                              c.tool_signatures(old['plan'], AGENTS))
    after = c.reuse_snapshot(plan, {}, carry, c.tool_signatures(plan, AGENTS))
    assert before['nodes']['s1']['identity_sha256'] == after['nodes']['s1']['identity_sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['paper_skills', 'paper_skill_results'])
@pytest.mark.parametrize('mutate', ['current', 'archive'])
async def test_cp1_excludes_changed_method_and_post_approval_mutation_stops(monkeypatch, field, mutate):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    old = paper_archive()
    req['research_contract']['rounds'] = [old]
    req['plan'] = copy.deepcopy(old['plan'])
    plan = req['plan']
    plan[field][1]['reason'] += ' revised method'
    req['research_contract']['plan_sha256'] = plan_sha256(plan)
    req['research_contract']['continuation']['plan_sha256'] = plan_sha256(plan)
    await orch._prepare_research_reuse('r', plan)
    carried = req['research_contract']['continuation']
    assert carried['reuse'] == carried['reuse_snapshot']['reuse'] == ['s1', 's3']
    assert set(carried['reuse_snapshot']['nodes']) == {'s1', 's3'}
    approve(req)
    assert await orch._verify_research_reuse('r', plan)
    before = copy.deepcopy(req['results'])
    target = plan if mutate == 'current' else old['plan']
    target[field][0]['reason'] += ' changed after approval'
    if mutate == 'archive':
        # Keep the archived ledger/hash internally consistent: the changed method identity itself
        # must invalidate the seal, not merely a generic stale plan-hash check.
        old['plan_sha256'] = plan_sha256(old['plan'])
        for row in old['results'].values():
            row['structured']['plan_sha256'] = old['plan_sha256']
    assert not await orch._verify_research_reuse('r', plan)
    assert not req['research_contract']['approval']['approved']
    assert req['outcome'] == 'reuse_approval_required' and req['results'] == before
    assert not hub.calls  # no silent rerun, conversion, installation or research dispatch


def test_context_digest_covers_delivered_receipt_without_exposing_its_values():
    plan = paper_archive()['plan']
    step = plan['steps'][0]
    attached = paper_skills.attachments(plan, step['id'], step['agent_id'], 'r')
    delivered = [{k: v for k, v in row.items() if k not in {'request_id', 'agent_id', 'step_id'}}
                 for row in attached]
    context = c.node_context(plan, step)
    assert json.loads(context)['paper_method_sha256'] == c._digest({
        'selections': [plan['paper_skills'][0]], 'results': delivered})
    assert plan['paper_skill_results'][0]['skill_dir'] not in context


def test_older_execution_signature_cannot_prove_the_paper_input_contract():
    old = paper_archive()
    old['reuse_tools'] = {sid: c._digest({'node_context': 1, 'configuration': AGENTS['worker']})
                          for sid in old['results']}
    carry = c.carry_over(old, old['plan'], {}, [])
    assert set(c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))) == set(carry.reuse)
