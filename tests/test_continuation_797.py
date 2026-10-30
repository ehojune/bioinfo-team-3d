"""Continuation approval contracts using fake dispatch/transport and synthetic ledgers only."""
import asyncio
import copy
import hashlib
import json
import threading

import pytest

from labhq.orchestrator.cso import Orchestrator
from labhq.research import continuation as c
from labhq.research.contract import plan_sha256
from tests.test_research_continue import (CP1, APPROVE, CONTINUE, _hub, _round_one, _steps, _plan)
from tests.test_research_protocol import MiniHub, valid_plan


@pytest.fixture(autouse=True)
def mock_workers(monkeypatch):
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    def denied(*args, **kwargs):
        pytest.fail('mock contract must not start a native thread')
    monkeypatch.setattr(threading.Thread, 'start', denied)


AGENTS = {'worker': {'id': 'worker', 'engine': 'mock', 'tools': ['fixture@1']}}


def archive():
    plan = valid_plan(steps=3)
    for step in plan['steps']:
        step['input_refs'] = []
    plan['steps'][2].update(depends_on=['s1'], input_refs=['step:s1/result1.tsv'])
    old = _round_one(plan)
    old['reuse_tools'] = c.tool_signatures(plan, AGENTS)
    old['reuse_no_request_inputs'] = {s['id']: True for s in plan['steps']}  # synthetic input-free executions
    for sid, result in old['results'].items():
        path = 'outputs/result' + sid[1:] + '.tsv'
        result.update(outputs=[path], output_sha256={path: sid[1:] * 64})
    return old


def test_normalized_node_defaults_and_independent_edit_keep_reuse():
    old = archive()
    new = copy.deepcopy(old['plan'])
    new['steps'][0]['optional_outputs'] = []  # model default, not a scientific edit
    new['steps'][1]['instruction'] += ' changed independent work'
    carry = c.carry_over(old, new, {}, [])
    assert carry.reuse == ['s1', 's3'] and carry.rerun == {'s2': 'spec changed'}
    assert not c.proof_problems(old, new, carry, c.tool_signatures(new, AGENTS))
    initial = c.reuse_snapshot(old['plan'], {}, c.carry_over(old, old['plan'], {}, []),
                               c.tool_signatures(old['plan'], AGENTS))
    changed = c.reuse_snapshot(new, {}, carry, c.tool_signatures(new, AGENTS))
    for sid in ('s1', 's3'):
        assert initial['nodes'][sid]['identity_sha256'] == changed['nodes'][sid]['identity_sha256']


def test_an_unrelated_branch_edit_keeps_independent_branch_identity():
    old = archive()
    plan = old['plan']
    plan['branches'] = [{'id': b, 'question': b, 'evidence_type': 'fixture', 'data_boundary': 'public'}
                        for b in ('a', 'b')]
    for step, branch in zip(plan['steps'], ['a', 'b', 'synthesis']):
        step['branch'] = branch
    plan['steps'][2]['depends_on'] = ['s1', 's2']
    new = copy.deepcopy(plan)
    new['branches'][1]['question'] = 'different scientific question'
    carry = c.carry_over(old, new, {}, [])
    assert carry.reuse == ['s1'] and set(carry.rerun) == {'s2', 's3'}


@pytest.mark.parametrize('field', ['scope', 'purpose', 'subject', 'primary_hypothesis', 'study_type'])
def test_scientific_brief_changes_are_never_fuzzy_equivalence(field):
    old = archive()
    new = copy.deepcopy(old['plan'])
    new['brief'][field] = 'exploratory' if field == 'study_type' else new['brief'][field] + ' different'
    assert c.carry_over(old, new, {}, []).reuse == []


@pytest.mark.parametrize('change', ['own_input', 'instruction', 'agent', 'scope', 'pack'])
def test_changed_own_contract_invalidates_candidate(change):
    old = archive()
    new = copy.deepcopy(old['plan'])
    if change == 'own_input':
        new['steps'][0]['input_refs'] = ['unproved-input']
    elif change == 'instruction':
        new['steps'][0]['instruction'] += ' use another tool/version'
    elif change == 'agent':
        new['steps'][0]['agent_id'] = 'other'
    elif change == 'scope':
        new['brief']['scope'] += ' another population'
    else:
        new['pack_values'] = {'different': {'fields': {'unit': 'cell'}, 'validators': {}, 'acceptance': {}}}
    carry = c.carry_over(old, new, {}, [])
    assert 's1' not in carry.reuse and 's3' not in carry.reuse


@pytest.mark.parametrize('bad', ['external', 'missing_hash', 'directory', 'not_dependency', 'missing_tools'])
def test_missing_or_ambiguous_proof_fails_closed(bad):
    old = archive()
    if bad == 'external':
        old['plan']['steps'][0]['input_refs'] = ['public-input']
    elif bad == 'missing_hash':
        old['results']['s1']['output_sha256'] = {}
    elif bad == 'directory':
        old['plan']['steps'][0]['outputs'] = ['folder']
        old['results']['s1'].update(outputs=['outputs/folder'],
                                    output_sha256={'outputs/folder/child.txt': 'a' * 64})
    elif bad == 'not_dependency':
        old['plan']['steps'][0]['input_refs'] = ['step:s2/result2.tsv']
    else:
        old.pop('reuse_tools')
    carry = c.carry_over(old, old['plan'], {}, [])
    assert 's1' in c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))


def test_upstream_content_and_tool_signature_change_node_identity():
    old = archive()
    plan = old['plan']
    tools = c.tool_signatures(plan, AGENTS)
    carry = c.carry_over(old, plan, {}, [])
    before = c.reuse_snapshot(plan, {}, carry, tools)
    carry.results['s1']['output_sha256']['outputs/result1.tsv'] = 'e' * 64
    after = c.reuse_snapshot(plan, {}, carry, tools)
    assert before['nodes']['s3']['identity_sha256'] != after['nodes']['s3']['identity_sha256']
    changed = c.tool_signatures(plan, {'worker': {**AGENTS['worker'], 'tools': ['fixture@2']}})
    assert set(c.proof_problems(old, plan, carry, changed)) == {'s1', 's2', 's3'}


def test_an_unrelated_agent_configuration_does_not_change_independent_identity():
    old = archive()
    plan = old['plan']
    carry = c.carry_over(old, plan, {}, [])
    before = c.reuse_snapshot(plan, {}, carry, c.tool_signatures(plan, AGENTS))
    agents = {**AGENTS, 'unrelated': {'engine': 'mock', 'tools': ['different@2']}}
    assert before == c.reuse_snapshot(plan, {}, carry, c.tool_signatures(plan, agents))


def test_declared_engine_arguments_and_environment_are_part_of_the_tool_signature():
    from labhq.settings import Settings
    old = archive()
    agents = {'worker': {**AGENTS['worker'], 'engine': 'codex'}}
    engines = Settings().engines
    before = c.tool_signatures(old['plan'], agents, engines)
    engines.codex.extra_args = ['--fixture-mode']
    changed = c.tool_signatures(old['plan'], agents, engines)
    assert before != changed
    engines.codex.env = {'FIXTURE_MODE': 'synthetic'}
    assert changed != c.tool_signatures(old['plan'], agents, engines)


@pytest.mark.parametrize('field', ['plan_sha256', 'agent_id', 'step_id'])
def test_archived_result_identity_is_required(field):
    old = archive()
    row = old['results']['s1']
    if field == 'agent_id':
        row['agent_id'] = 'other'
    else:
        row['structured'][field] = 'different'
    carry = c.carry_over(old, old['plan'], {}, [])
    # A malformed result may already be refused by ledger validation; otherwise proof must refuse it.
    assert 's1' not in carry.reuse or 's1' in c.proof_problems(old, old['plan'], carry,
                                                          c.tool_signatures(old['plan'], AGENTS))


def test_directory_verification_remains_fail_closed(monkeypatch):
    from labhq.evidence import audit
    monkeypatch.setattr(audit, 'verify_request', lambda *a: {'reasons': [], 'files': [
        {'step_id': 's1', 'path': 'outputs/folder', 'status': audit.UNRECORDED}]})
    assert 's1' in c.verify_reuse({'s1': {'outputs': ['outputs/folder'], 'output_sha256': {}}}, object())


def test_tiny_directory_fixture_is_declined_when_present_changed_or_missing(tmp_path):
    from tests.test_research_cp2 import _settings
    settings = _settings()
    settings.runner.workspace_root = str(tmp_path / 'ws')
    workdir = tmp_path / 'ws' / '2026-10-20' / 'wd_fixture'
    child = workdir / 'outputs' / 'folder' / 'child.txt'
    child.parent.mkdir(parents=True)
    child.write_text('synthetic fixture', encoding='utf-8')
    result = {'outputs': ['outputs/folder'], 'output_sha256': {
                  'outputs/folder/child.txt': hashlib.sha256(child.read_bytes()).hexdigest()},
              'workdir': str(workdir), 'workdir_id': 'wd_fixture', 'task_id': 'fixture'}
    assert 's1' in c.verify_reuse({'s1': result}, settings)
    child.write_text('changed synthetic fixture', encoding='utf-8')
    assert 's1' in c.verify_reuse({'s1': result}, settings)
    child.unlink()
    assert 's1' in c.verify_reuse({'s1': result}, settings)


@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['before_cp1', 'after_cp1', 'dispatch'])
async def test_card_uses_verified_candidates_and_drift_never_silently_reruns(tmp_path, monkeypatch, when):
    drift = {'changed': when == 'before_cp1'}
    checked = []
    def verify(results, settings):
        checked.append(list(results))
        return {'s1': 'fixture bytes mismatch'} if drift['changed'] and 's1' in results else {}
    monkeypatch.setattr(c, 'verify_reuse', verify)
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])
    def cp1():
        card = hub.approvals[3]['detail']['continuation']
        assert checked, 'verification must precede CP1'
        assert card['reuse_snapshot']['reuse'] == card['reuse']
        assert set(card['reuse_snapshot']['nodes']) == set(card['reuse'])
        assert card['reuse_snapshot']['plan_sha256'] == hub.approvals[3]['detail']['target_sha256']
        assert card['reuse'] == ([] if when == 'before_cp1' else ['s1'])
        if when != 'dispatch':
            drift['changed'] = True
    hub.hooks[4] = cp1
    publish = hub.publish
    async def dispatch_drift(event):
        if (when == 'dispatch' and event.get('type') == 'request.step_attempt'
                and event.get('data', {}).get('step_id') == 's2'
                and hub.requests['r'].get('research_contract', {}).get('round') == 2):
            drift['changed'] = True
        await publish(event)
    hub.publish = dispatch_drift
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    if when in {'after_cp1', 'dispatch'}:
        assert _steps(hub)[4:] == ['plan']
        assert req['research_contract']['continuation']['ended_before_dispatch']['outcome'] == 'reuse_approval_required'
        assert '새 CP1' in req['report']
    else:
        assert _steps(hub)[4:7] == ['plan', 's1', 's2']  # explicitly approved reruns only
        assert req['outcome'] == 'research_reported'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['file', 'tools', 'record', 'missing_seal', 'plan'])
@pytest.mark.parametrize('dispatched', [False, True])
async def test_resume_revalidates_even_previously_verified_snapshot(monkeypatch, mutation, dispatched):
    old = archive()
    plan = old['plan']
    async def reply(task):
        pytest.fail('snapshot validation must not dispatch')
    from tests.test_research_cp2 import _settings
    hub = MiniHub(_settings(), reply, mode='orchestrate', work_kind='research')
    hub.agents = copy.deepcopy(AGENTS)
    req = hub.requests['r']
    req.update(plan=plan, research_contract={'plan_sha256': plan_sha256(plan), 'pack_snapshot': {},
        'rounds': [old], 'continuation': {'plan_sha256': plan_sha256(plan), 'round': 2}})
    monkeypatch.setattr(c, 'verify_reuse', lambda *a: {})
    orch = Orchestrator(hub)
    await orch._prepare_research_reuse('r', plan)
    carried = req['research_contract']['continuation']
    req['research_contract']['approval'] = {'approved': True, 'target_sha256': plan_sha256(plan),
        'reuse_snapshot_sha256': carried['reuse_snapshot']['sha256']}
    assert await orch._verify_research_reuse('r', plan)
    assert not carried['verified']  # proof alone cannot cross the CP1 budget gate
    assert orch._continuation_unstarted(req)
    carried.update(verified=True, dispatch_started=dispatched)  # approved execution/resume boundary
    if mutation == 'file':
        monkeypatch.setattr(c, 'verify_reuse', lambda *a: {'s1': 'mismatch'})
    elif mutation == 'tools':
        hub.agents['worker']['tools'] = ['fixture@2']
    elif mutation == 'record':
        req['results']['s1']['output_sha256']['outputs/result1.tsv'] = 'e' * 64
    elif mutation == 'plan':
        req['plan']['brief']['scope'] += ' another population'
    else:
        carried.pop('reuse_snapshot')
    assert not await orch._verify_research_reuse('r', plan)
    assert req['outcome'] == 'reuse_approval_required'
    assert not req['research_contract']['approval']['approved'] and not hub.calls
    assert orch._continuation_unstarted(req) is (not dispatched)  # never roll back a dispatched round


def test_unreceipted_references_come_from_the_reused_steps_proofs_only():
    """#1061: the reuse card and report name DOI/PMID references pinned by identifier, for the steps actually reused."""
    from labhq.orchestrator.cso import unreceipted_references

    contract = {'rounds': [{'reuse_input_proof': {'s1': {'unreceipted': ['pmid:2', 'doi:10.1/a']}, 's2': None,
                                                    's3': {'unreceipted': ['doi:10.1/a', 'doi:10.1/z']}}}]}
    assert unreceipted_references(contract, ['s1', 's2']) == ['doi:10.1/a', 'pmid:2']
    assert unreceipted_references(contract, ['s3', 's1']) == ['doi:10.1/a', 'doi:10.1/z', 'pmid:2']
    assert unreceipted_references(contract, []) == [] and unreceipted_references({}, ['s1']) == []


@pytest.mark.asyncio
async def test_the_continuation_card_and_report_say_the_content_of_pinned_references_was_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'proof_problems', lambda *args, **kwargs: {})  # the identifier receipt itself is #1061's PR 1
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])

    def pin_references():
        hub.requests['r'].setdefault('reuse_input_proof', {})['s1'] = {'unreceipted': ['doi:10.1/a', 'pmid:7']}
    hub.hooks[3] = pin_references  # the continue card is open: s1's proof is archived with the round that follows
    await Orchestrator(hub).run_request('r')

    cp1 = hub.approvals[3]
    assert cp1['detail']['continuation']['reuse'] == ['s1']
    note = '재사용한 단계가 쓴 DOI·PMID 참조 2건은 식별자만 고정했고 내용은 확인하지 않았습니다.'
    assert cp1['summary'].endswith(note)  # after the 699-char cut with the budget line, so a long card cannot drop it
    assert 'pinned by identifier only; their content was not checked (doi:10.1/a, pmid:7)' in json.dumps(
        hub.requests['r'], ensure_ascii=False)
    assert '참고: 재사용한 단계가 쓴 DOI·PMID 참조 2건은 식별자만 고정했고 내용은 확인하지 않았습니다(doi:10.1/a, pmid:7).' in (
        hub.requests['r']['report'])  # the report the PI reads, not only the execution record


@pytest.mark.asyncio
async def test_without_pinned_references_the_card_text_is_unchanged(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])
    await Orchestrator(hub).run_request('r')
    assert '식별자만 고정' not in hub.approvals[3]['summary']
    assert '식별자만 고정' not in hub.requests['r']['report']
