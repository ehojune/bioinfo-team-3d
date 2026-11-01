"""Projection evidence stays separate; all inputs are isolated fakes."""
import subprocess
from pathlib import Path

import pytest

from tests.test_facilities_diagnosis import make_hub, step
from tests.support.node_binary import node_binary
from labhq.models import TaskResult
from labhq.models import AskRequest


def test_shared_reducer_in_node():
    node = node_binary()
    if not node:
        pytest.skip('Node is optional')
    result = subprocess.run([node, str(Path(__file__).with_suffix('.js'))], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'shared 2D/3D labels passed' in result.stdout


async def test_offline_diagnosis_publishes_and_snapshot_matches(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests['r']['plan'] = {'steps': [{'id': 's1'}]}
    hub.agents.pop('facilities')
    hub.save_request('r')
    try:
        await hub.request_facilities_diagnosis(step(), TaskResult(task_id='t', agent_id='worker', ok=False), 's1')
        event = [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]
        p = event['data']['s1']
        assert p['answer_status'] == 'rejected'
        assert 'offline' in p['answer']
        assert p['rerun_status'] == p['step_status'] == 'unknown'
        snapshot = hub.snapshot()
        assert snapshot['data']['requests'][0]['facilities_projection']['s1'] == p
    finally:
        hub.store.close()


async def test_fallback_answer_emits_separate_routing(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests['r']['plan'] = {'steps': [{'id': 's1'}]}
    hub.save_request('r')
    hub.store.put('task', 't', {'request_id': 'r', 'step_id': 's1'})
    ask = AskRequest(id='ask', request_id='r', task_id='t', agent_id='worker',
                     to='facilities', question='help', why_blocked='offline')
    hub.store.put('ask', 'ask', {'ask': ask.model_dump(mode='json'), 'state': 'pending', 'routed_to': 'cso'})
    try:
        await hub.resolve_ask(ask, 'absent', {'status': 'answered', 'answer': 'advice',
                                              'from': 'cso', 'routed_to': 'cso'})
        p = [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]['data']['s1']
        assert (p['requested_to'], p['routed_to'], p['answered_by'], p['answer']) == ('facilities', 'cso', 'cso', 'advice')
        assert p['fix_status'] == p['rerun_status'] == p['step_status'] == 'unknown'
    finally:
        hub.store.close()


async def test_approval_timeout_publishes_after_decision(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests['r']['plan'] = {'steps': [{'id': 's1'}]}
    hub.save_request('r')
    hub.s.policy.approvals.timeout_s = 1
    try:
        await hub.request_facilities_fix('r', 's1', {'fix_id': 'cache', 'signature_id': 'disk',
                                                     'action': 'clear', 'execution': 'workspace_cache'})
        projections = [e['data']['s1'] for e in hub.events if e['type'] == 'request.facilities_projection'
                       and 's1' in e['data']]
        assert projections[0]['approval_status'] == 'waiting'
        assert projections[-1]['approval_status'] == 'timed_out'
        assert hub.snapshot()['data']['requests'][0]['facilities_projection']['s1'] == projections[-1]
    finally:
        hub.store.close()


async def test_separate_durable_evidence_and_missing_ledger(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests['r']['plan'] = {'steps': [{'id': 's1'}]}
    hub.requests['r']['facilities_fixes'] = {'s1': {'status': 'applied', 'approval_id': 'a'}}
    hub.save_request('r')
    hub.store.put('approval_decision', 'a', {'approved': False, 'state': 'timed_out', 'note': 'timeout'})
    try:
        p = hub.facilities_projection('r')['s1']
        assert (p['approval_status'], p['fix_status'], p['rerun_status'], p['step_status']) == ('timed_out', 'applied', 'unknown', 'unknown')
        hub.store.put('task', 'retry', {'request_id': 'r', 'step_id': 's1', 'accepted': True,
                      'completed': True, 'result': {'ok': False}, 'payload': {'meta': {'facilities_rerun': True}}})
        hub.requests['r']['results'] = {'s1': {'ok': False}}
        hub.save_request('r')
        p = hub.facilities_projection('r')['s1']
        assert (p['rerun_status'], p['step_status']) == ('failed', 'failed')
        hub.store.put('task', 'retry', {'request_id': 'r', 'step_id': 's1', 'accepted': True,
                      'completed': True, 'result': {'ok': True, 'pending_asks': ['a']},
                      'payload': {'meta': {'facilities_rerun': True}}})
        hub.requests['r']['results'] = {'s1': {'ok': True, 'pending_jobs': ['j']}}
        hub.save_request('r')
        p = hub.facilities_projection('r')['s1']
        assert (p['rerun_status'], p['step_status']) == ('waiting', 'unknown')
    finally:
        hub.store.close()


async def test_direct_without_plan_is_published_and_snapshotted(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests['r']['plan'] = None
    hub.requests['r']['facilities_fixes'] = {'direct': {'status': 'applied'}}
    hub.save_request('r')
    try:
        await hub.publish_facilities_projection('r')
        p = [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]['data']['direct']
        assert (p['fix_status'], p['rerun_status'], p['step_status']) == ('applied', 'unknown', 'unknown')
        assert hub.snapshot()['data']['requests'][0]['facilities_projection']['direct'] == p
    finally:
        hub.store.close()
