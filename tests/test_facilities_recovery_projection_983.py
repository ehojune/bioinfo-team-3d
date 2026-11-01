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
        hub.requests['r']['facilities_fixes']['s1']['status'] = 'cancelled'
        hub.store.put('task', 'retry', {'request_id': 'r', 'step_id': 's1', 'accepted': True,
                      'completed': True, 'abandoned': True, 'result': {'ok': False},
                      'payload': {'meta': {'facilities_rerun': True}}})
        hub.save_request('r')
        p = hub.facilities_projection('r')['s1']
        assert (p['fix_status'], p['rerun_status']) == ('cancelled', 'unknown')
        assert hub.snapshot()['data']['requests'][0]['facilities_projection']['s1'] == p
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


async def test_unrelated_task_events_do_not_scan_ledgers(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    hub.save_request('r')
    original = hub.store.all
    scans = []
    def counted(kind):
        if kind in {'task', 'ask'}:
            scans.append(kind)
        return original(kind)
    monkeypatch.setattr(hub.store, 'all', counted)
    try:
        for typ in ('task.dispatched', 'task.accepted', 'task.result'):
            await hub.publish({'type': typ, 'request_id': 'r', 'data': {}})
        assert scans == []
    finally:
        hub.store.close()


async def test_direct_terminal_commit_publishes_final_step(tmp_path, monkeypatch):
    import copy
    hub = make_hub(tmp_path)
    hub.requests['r'].update(mode='direct', plan=None, facilities_fixes={'direct': {'status': 'applied'}})
    hub.save_request('r')
    hub.store.put('task', 'retry', {'request_id': 'r', 'step_id': None, 'kind': 'direct',
                  'completed': True, 'result': {'ok': True},
                  'payload': {'meta': {'facilities_rerun': True}}})
    before = copy.deepcopy(hub.requests['r'])
    proposed = {**before, 'status': 'done', 'results': {'direct': {'ok': True}}}
    monkeypatch.setattr(hub, '_bundle_outcome', lambda *args: None)
    try:
        await hub.aschedule_terminal('r', 'request.completed', {'ok': True}, request=proposed, before=before)
        event = [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]
        assert event['data']['direct']['step_status'] == 'succeeded'
        assert event['data']['direct']['rerun_status'] == 'succeeded'
    finally:
        hub.store.close()


def test_snapshot_calculates_each_projection_once(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    hub.save_request('r')
    original = hub.facilities_projection
    calls = []
    def counted(rid, *args):
        calls.append(rid)
        return original(rid, *args)
    monkeypatch.setattr(hub, 'facilities_projection', counted)
    try:
        hub.snapshot()
        assert calls == ['r']
    finally:
        hub.store.close()


@pytest.mark.parametrize('step_id', [None, 'direct'])
async def test_direct_ordinary_ask_is_visible_live_and_in_snapshot(tmp_path, step_id):
    hub = make_hub(tmp_path)
    hub.requests['r'].update(mode='direct', plan=None)
    hub.save_request('r')
    hub.store.put('task', 't', {'request_id': 'r', 'kind': 'direct', 'step_id': step_id,
                  'payload': {'meta': {'facilities_rerun': True}}})
    ask = AskRequest(id='a', request_id='r', task_id='t', agent_id='worker',
                     to='facilities', question='help', why_blocked='environment')
    hub.store.put('ask', 'a', {'ask': ask.model_dump(mode='json'), 'state': 'pending'})
    try:
        await hub.publish({'type': 'agent.ask', 'request_id': 'r', 'data': ask.model_dump(mode='json')})
        live = [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]['data']['direct']
        assert live['requested_to'] == 'facilities' and live['answer_status'] == 'waiting'
        assert live['rerun_status'] == 'waiting'
        assert hub.snapshot()['data']['requests'][0]['facilities_projection']['direct'] == live
        await hub.resolve_ask(ask, 'absent', {'status': 'answered', 'answer': 'advice', 'from': 'cso'})
        assert [e for e in hub.events if e['type'] == 'request.facilities_projection'][-1]['data']['direct']['answer'] == 'advice'
    finally:
        hub.store.close()
