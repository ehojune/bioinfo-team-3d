"""Paper advice contracts with synthetic metadata/files and fake dispatch only."""
import asyncio
import copy
import hashlib
from unittest.mock import AsyncMock

import pytest

from labhq.models import AgentSpec, Task, TaskResult
from labhq.recruit import paper_skills as ps
from labhq.runner.workspace import TaskWorkspace
from labhq.settings import Settings
from labhq.orchestrator.cso import Orchestrator
from labhq.research import continuation as continuation
from labhq.research.contract import plan_sha256
from tests.test_paper_skill_attach import SELECTED, PRECEDENTS
from tests.test_research_protocol import MiniHub, valid_plan
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401


@pytest.fixture(autouse=True)
def fake_host(monkeypatch):
    monkeypatch.setattr('labhq.runner.workspace.platform.node', lambda: 'fixture-host')
    monkeypatch.setattr('labhq.adapters.claude_code._git', lambda *args: None)


async def deferred():
    run = AsyncMock(side_effect=AssertionError('CP1 must not build or fetch'))
    plan = {'steps': [{'id': 's1', 'agent_id': 'worker'}], 'paper_skills': [copy.deepcopy(SELECTED)]}
    out = await ps.prepare_plan(plan, PRECEDENTS, [{'id': 'worker'}], 'r', Settings(), run)
    run.assert_not_awaited()
    return out


async def prepared_during_lead_time(orch, plan, sid='s1'):
    """Fake approved preparation completes before dispatch; dispatch itself must never await it."""
    batch = await orch._start_paper_builds('r', ps.attachments(plan, sid, 'worker', 'r'))
    try:
        await asyncio.gather(*batch.jobs.values())
    finally:
        await orch._close_paper_builds('r', batch)
    orch.paper_builds['r'] = batch
    return batch


@pytest.mark.asyncio
async def test_cp1_preparation_is_metadata_only_and_seals_selection():
    plan = await deferred()
    row = plan['paper_skill_results'][0]
    assert row['status'] == 'deferred' and not row['built']
    assert len(row['selection_sha256']) == 64
    assert '승인 후' in ps.card_text(plan)
    assert not ps.attachments(plan, 's2', 'worker', 'r')
    assert not ps.attachments(plan, 's1', 'other', 'r')


@pytest.mark.asyncio
async def test_dispatch_builds_only_assigned_selection_without_mutating_approved_plan():
    plan = await deferred()
    before = copy.deepcopy(plan)
    build = AsyncMock(side_effect=lambda t: TaskResult(task_id=t.id, agent_id=t.agent_id, ok=True,
        structured={'status': 'ready', 'paper_id': 'pmid:12345678', 'skill_sha256': 'a' * 64,
                    'fulltext_receipt_sha256': ps.evidence_sha256(t.meta['paper_skill']['fulltext_receipt'])}))
    rows = await ps.resolve_attachments(ps.attachments(plan, 's1', 'worker', 'r'), Settings(), build)
    assert len(rows) == 1 and rows[0]['status'] == 'ready'
    assert build.await_count == 1 and build.call_args.args[0].meta['max_attempts'] == 1
    assert plan == before


@pytest.mark.asyncio
async def test_build_cancellation_propagates():
    plan = await deferred()
    with pytest.raises(asyncio.CancelledError):
        await ps.resolve_attachments(ps.attachments(plan, 's1', 'worker', 'r'), Settings(),
                                     AsyncMock(side_effect=asyncio.CancelledError))


def workspace(tmp_path):
    from labhq.recruit.provenance import folder_sha256
    src = tmp_path / 'skill'
    src.mkdir()
    (src / 'SKILL.md').write_text('# Synthetic advice\n', encoding='utf-8')
    row = {'paper_id': 'pmid:12345678', 'skill_sha256': folder_sha256(src), 'skill_dir': str(src),
           'paper2agent_commit': 'a' * 40, 'runner_id': 'runner', 'request_id': 'r',
           'step_id': 's1', 'agent_id': 'worker'}
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={'step_id': 's1'})
    ws = TaskWorkspace(tmp_path / 'work', task, AgentSpec(id='worker', name='Worker', role='fixture'))
    plugin, warnings = ws.install_paper_skills([row], 'runner')
    assert plugin and not warnings
    return ws, row


@pytest.mark.parametrize('evidence,expected', [('none', False), ('exact', True), ('wrong', False),
                                             ('changed', False), ('failed', False), ('binary', False),
                                             ('missing', False), ('plugin', False)])
def test_called_requires_exact_result_reference_and_unchanged_owned_input(tmp_path, evidence, expected):
    ws, row = workspace(tmp_path)
    installed = ws.provenance()['runs'][ws.task.id]['paper_skills'][0]
    assert installed['called'] is False  # installing is not calling
    path = ws.dir / installed['input_ref']
    assert path.relative_to(ws.dir).as_posix().startswith('inputs/paper_skills/')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == installed['document_sha256']
    token = ps.reference_token(installed)
    text = token if evidence not in {'none', 'wrong'} else token.replace(row['skill_sha256'], 'f' * 64)
    if evidence == 'none':
        text = 'Assigned, but no result reference'
    if evidence == 'changed':
        path.write_text('changed', encoding='utf-8')
    elif evidence == 'binary':
        path.write_bytes(b'\xff')
    elif evidence == 'missing':
        path.unlink()
    elif evidence == 'plugin':
        (ws.dir / '.agents/skills' / installed['skill_id'] / 'SKILL.md').write_text('changed', encoding='utf-8')
    result = TaskResult(task_id=ws.task.id, agent_id='worker', ok=evidence != 'failed', text=text)
    ws.record_paper_skill_usage(result)
    receipt = ws.provenance()['runs'][ws.task.id]['paper_skills'][0]
    assert receipt['called'] is expected and receipt['call_status'] != 'pending'
    assert receipt.get('evidence') == (token if expected else None)
    ws.clear_paper_skills()
    assert not path.exists()


def test_assignment_receipt_alone_is_not_called_or_unknown():
    plan = {'paper_skill_results': [{'paper_id': 'pmid:1', 'title': 'fixture', 'status': 'ready'}]}
    result = {'s1': {'provenance': {'runs': {'t': {'paper_skills': [{'paper_id': 'pmid:1'}]}}}}}
    row = ps.records(plan, result)[0]
    assert row['called'] is False and row['call_status'] == 'unverified'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['step', 'result_correction', 'slot_repair'])
@pytest.mark.parametrize('approval,mutate', [(True, False), (False, False), (True, True)])
async def test_approved_preparation_and_dispatch_plan_drift_stops_before_step(approval, mutate, kind):
    plan = await ps.prepare_plan({**valid_plan(), 'paper_skills': [copy.deepcopy(SELECTED)]}, PRECEDENTS,
                                 [{'id': 'worker'}], 'r', Settings(), AsyncMock())
    before = copy.deepcopy(plan)
    async def reply(task):
        if task.meta['kind'] == 'paper_skill':
            if mutate:
                plan['paper_skills'][0]['reason'] = 'changed while building'
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={'status': 'unavailable', 'error': 'fixture unavailable'})
        assert task.meta['paper_skills'][0]['status'] == 'unavailable'
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text='fixture result')
    hub = MiniHub(Settings(), reply)
    hub.agents['recruiter'] = {'id': 'recruiter', 'engine': 'mock'}
    req = hub.requests['r']
    req['plan'] = plan
    req['research_contract'] = {'plan_sha256': plan_sha256(plan), 'approval': {
        'approved': approval, 'target_sha256': plan_sha256(plan)}}
    orch = Orchestrator(hub)
    task = Task(agent_id='worker', request_id='r', prompt='approved fixture step', meta={
        'kind': kind, 'step_id': 's1', 'paper_skills': ps.attachments(plan, 's1', 'worker', 'r')})
    if approval:
        await prepared_during_lead_time(orch, plan)
    if not approval or mutate:
        with pytest.raises(continuation.ReuseApprovalRequired):
            await orch.run_step(task)
        assert not req['research_contract']['approval']['approved']
        assert [t.meta['kind'] for t in hub.calls] == (['paper_skill'] if mutate else [])
    else:
        result = await orch.run_step(task)
        assert result.ok and [t.meta['kind'] for t in hub.calls] == ['paper_skill', kind]
        assert plan == before and req['research_contract']['plan_sha256'] == plan_sha256(plan)


def test_deferred_method_never_proves_reuse_but_independent_node_keeps_proof():
    from tests.test_continuation_797 import archive, AGENTS
    old = archive()
    plan = old['plan']
    plan['paper_skill_results'] = [{'paper_id': 'pmid:1', 'status': 'deferred', 'step_ids': ['s1']}]
    old['plan_sha256'] = plan_sha256(plan)
    for result in old['results'].values():
        result['structured']['plan_sha256'] = old['plan_sha256']
    carry = continuation.carry_over(old, plan, {}, [])
    problems = continuation.proof_problems(old, plan, carry, continuation.tool_signatures(plan, AGENTS))
    assert set(problems) == {'s1', 's3'} and 'content proof' in problems['s1']


@pytest.mark.parametrize('level', [None, 'metadata', 'abstract'])
@pytest.mark.asyncio
async def test_missing_fulltext_proof_never_schedules_build(level):
    papers = {'papers': [{'title': 'fixture', 'citations': ['PMID:12345678'], 'access_level': level}]}
    plan = {'steps': [{'id': 's1', 'agent_id': 'worker'}], 'paper_skills': [copy.deepcopy(SELECTED)]}
    out = await ps.prepare_plan(plan, papers, [{'id': 'worker'}], 'r', Settings(), AsyncMock())
    assert out['paper_skill_results'][0]['status'] == 'skipped'
    assert out['warnings'] and not ps.attachments(out, 's1', 'worker', 'r')


@pytest.mark.asyncio
async def test_reuse_proof_drift_during_build_stops_before_research_dispatch(monkeypatch):
    from tests.test_continuation_review_807 import partial_hub, approve
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    req['plan']['paper_skills'] = [{**SELECTED, 'step_ids': ['s2']}]
    req['plan'] = await ps.prepare_plan(req['plan'], PRECEDENTS, [{'id': 'worker'}], 'r', hub.s, AsyncMock())
    contract = req['research_contract']
    contract['plan_sha256'] = contract['continuation']['plan_sha256'] = plan_sha256(req['plan'])
    await orch._prepare_research_reuse('r', req['plan'])
    assert contract['continuation']['reuse'] == ['s1']
    approve(req)
    hub.agents['recruiter'] = {'id': 'recruiter', 'engine': 'mock'}
    async def reply(task):
        assert task.meta['kind'] == 'paper_skill'
        contract['rounds'][0]['results']['s1']['output_sha256']['outputs/result1.tsv'] = 'f' * 64
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured={'status': 'unavailable', 'error': 'fixture'})
    hub.reply = reply
    await prepared_during_lead_time(orch, req['plan'], 's2')
    with pytest.raises(continuation.ReuseApprovalRequired):
        await orch.run_step(Task(agent_id='worker', request_id='r', prompt='approved fixture', meta={
            'kind': 'step', 'step_id': 's2', 'paper_skills': ps.attachments(req['plan'], 's2', 'worker', 'r')}))
    assert [t.meta['kind'] for t in hub.calls] == ['paper_skill']
    assert not contract['approval']['approved'] and req['outcome'] == 'reuse_approval_required'


def test_result_reference_is_reported_with_hash_and_no_private_source_path(tmp_path):
    ws, row = workspace(tmp_path)
    receipt = ws.provenance()['runs'][ws.task.id]['paper_skills'][0]
    result = TaskResult(task_id=ws.task.id, agent_id='worker', ok=True, text=ps.reference_token(receipt))
    ws.record_paper_skill_usage(result)
    plan = {'paper_skill_results': [{**SELECTED, 'paper_id': row['paper_id'], 'status': 'deferred'}]}
    results = {'s1': {'provenance': ws.provenance()}}
    record = ps.records(plan, results)[0]
    assert record['called'] is True and record['built'] and record['attached_steps'] == ['s1']
    assert 'skill_dir' not in record['usage'][0] and row['skill_dir'] not in str(record)
    assert row['skill_sha256'] in ps.appendix(plan, results) and '결과 참조: 확인(도구 호출·효용 증거 아님)' in ps.appendix(plan, results)


def test_unwritable_owned_input_never_counts_as_installed(tmp_path):
    ws, row = workspace(tmp_path)
    ws.clear_paper_skills()
    # A plain blocking entry exercises the same fail-closed owned-path boundary without symlink privileges.
    import shutil
    shutil.rmtree(ws.dir / 'inputs')
    (ws.dir / 'inputs').write_text('blocking fixture', encoding='utf-8')
    plugin, warnings = ws.install_paper_skills([row], 'runner')
    assert plugin is None and warnings
    receipt = ws.provenance()['runs'][ws.task.id]['paper_skills'][0]
    assert receipt['called'] is False and receipt['reason'] == 'installation_failed'
    assert not list(ws.dir.glob('.agents/skills/paper-*'))


@pytest.mark.asyncio
async def test_correction_reference_survives_the_final_research_ledger():
    from tests.test_research_cp2 import _settings, _invalid_result
    from tests.test_research_protocol import _cp2_result
    plan = await ps.prepare_plan({**valid_plan(), 'paper_skills': [copy.deepcopy(SELECTED)]}, PRECEDENTS,
                                 [{'id': 'worker'}], 'r', Settings(), AsyncMock())
    for step in plan['steps']:
        step['outputs'] = ['outputs/result1.tsv']
    receipt = {'paper_id': 'pmid:12345678', 'skill_id': 'paper-' + 'a' * 24, 'skill_sha256': 'b' * 64,
               'document_sha256': 'c' * 64, 'input_ref': 'inputs/paper_skills/fixture.md',
               'selection_sha256': plan['paper_skill_results'][0]['selection_sha256'],
               'status': 'ready', 'called': True, 'call_status': 'referenced'}
    receipt['evidence'] = ps.reference_token(receipt)
    async def reply(task):
        if task.meta['kind'] == 'paper_skill':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={'paper_id': 'pmid:12345678', 'status': 'ready',
                                  'skill_sha256': receipt['skill_sha256'],
                                  'fulltext_receipt_sha256': ps.evidence_sha256(task.meta['paper_skill']['fulltext_receipt'])})
        structured = _invalid_result(hub, task) if task.meta['kind'] == 'step' else _cp2_result(hub, task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=structured,
            workdir='fixture/s1', outputs=['outputs/result1.tsv'], output_sha256={'outputs/result1.tsv': 'd' * 64},
            provenance={'runs': {task.id: {'paper_skills': [receipt]}}} if task.meta['kind'] == 'result_correction' else {})
    hub = MiniHub(_settings(), reply, mode='orchestrate', work_kind='research')
    hub.agents['recruiter'] = {'id': 'recruiter', 'engine': 'mock'}
    hub.requests['r'].update(plan=plan, research_contract={'execution_enabled': True,
        'plan_sha256': plan_sha256(plan), 'approval': {
        'approved': True, 'target_sha256': plan_sha256(plan)}})
    results = {}
    orch = Orchestrator(hub)
    run = orch._run_dag
    async def after_fake_lead_time(*args, **kwargs):
        await asyncio.gather(*orch.paper_builds['r'].jobs.values())
        return await run(*args, **kwargs)
    orch._run_dag = after_fake_lead_time
    await orch.run_dag('r', 'fixture', plan['steps'], results)
    assert results['s1'].ok, results['s1'].error
    assert any(t.meta['kind'] == 'result_correction' for t in hub.calls)
    assert ps.records(plan, {'s1': results['s1'].model_dump(mode='json')})[0]['called'] is True


@pytest.mark.asyncio
async def test_slot_repair_cannot_swallow_renewed_approval_required():
    from tests.test_research_cp2 import _settings
    from tests.test_research_protocol import _cp2_result
    plan = await ps.prepare_plan({**valid_plan(), 'paper_skills': [copy.deepcopy(SELECTED)]}, PRECEDENTS,
                                 [{'id': 'worker'}], 'r', Settings(), AsyncMock())
    plan['steps'][0]['outputs'] = ['outputs/result1.tsv']
    builds = []
    async def reply(task):
        if task.meta['kind'] == 'paper_skill':
            builds.append(task)
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0,
                              structured={'status': 'unavailable', 'error': 'fixture'})
        assert task.meta['kind'] == 'step'  # a changed plan must never reach the repair dispatch
        structured = _cp2_result(hub, task)
        structured.update(claims=[], evidence=[], links=[], artifact_refs=[])
        plan['paper_skills'][0]['reason'] += ' changed before slot repair'
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0,
            session_id='fixture-session', structured=structured, workdir='fixture/s1',
            outputs=['outputs/result1.tsv'], output_sha256={'outputs/result1.tsv': 'd' * 64})
    hub = MiniHub(_settings(), reply, mode='orchestrate', work_kind='research')
    hub.supports_resume = lambda _: True
    hub.agents['recruiter'] = {'id': 'recruiter', 'engine': 'mock'}
    hub.requests['r'].update(plan=plan, research_contract={'execution_enabled': True,
        'plan_sha256': plan_sha256(plan), 'approval': {'approved': True, 'target_sha256': plan_sha256(plan)}})
    stopped = None
    orch = Orchestrator(hub)
    run = orch._run_dag
    async def after_fake_lead_time(*args, **kwargs):
        await asyncio.gather(*orch.paper_builds['r'].jobs.values())
        return await run(*args, **kwargs)
    orch._run_dag = after_fake_lead_time
    try:
        await orch.run_dag('r', 'fixture', plan['steps'], {})
    except continuation.ReuseApprovalRequired as exc:
        stopped = exc
    assert len(builds) == 1 and not any(t.meta['kind'] == 'slot_repair' for t in hub.calls)
    assert stopped is not None and not hub.requests['r']['research_contract']['approval']['approved']


@pytest.mark.asyncio
async def test_selection_is_rechecked_after_the_post_build_budget_await():
    plan = await ps.prepare_plan({**valid_plan(), 'paper_skills': [copy.deepcopy(SELECTED)]}, PRECEDENTS,
                                 [{'id': 'worker'}], 'r', Settings(), AsyncMock())
    hub = MiniHub(Settings(), AsyncMock())
    hub.requests['r'].update(plan=plan, research_contract={'plan_sha256': plan_sha256(plan), 'approval': {
        'approved': True, 'target_sha256': plan_sha256(plan)}})
    orch = Orchestrator(hub)
    orch.run_step = AsyncMock(return_value=TaskResult(task_id='build', agent_id='recruiter', ok=True,
        structured={'status': 'unavailable'}))
    def change_after_build(*args):
        plan['paper_skills'][0]['reason'] += ' changed while waiting for budget'
    orch._check_budget = AsyncMock(side_effect=change_after_build)
    with pytest.raises(continuation.ReuseApprovalRequired):
        await orch._resolve_step_paper_skills(Task(agent_id='worker', request_id='r', prompt='fixture', meta={
            'kind': 'step', 'step_id': 's1', 'paper_skills': ps.attachments(plan, 's1', 'worker', 'r')}))
    assert not hub.calls and not hub.requests['r']['research_contract']['approval']['approved']
