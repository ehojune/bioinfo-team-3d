"""REMOTE CI ONLY for this task: fake async lifecycle; never builds papers or starts a runner."""
import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from labhq.interruption import ShutdownInterrupted
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.recruit import paper_skills as ps
from labhq.research.continuation import ReuseApprovalRequired
from labhq.research.contract import plan_sha256
from labhq.settings import Settings


def row(number=1, sid='s1'):
    return {'paper_id': f'pmid:{number}', 'selection_sha256': str(number) * 64,
            'title': f'Synthetic {number}', 'reason': 'Synthetic method',
            'step_ids': [sid], 'agent_ids': ['worker'], 'status': 'deferred',
            'fulltext_receipt': {'text_sha256': 'a' * 64}}


class Hub:
    def __init__(self, rows):
        self.s = Settings()
        self.s.runner.task_timeout_s = 30
        self.agents = {'recruiter': {'engine': 'mock'}}
        steps = [{'id': sid, 'agent_id': 'worker'} for sid in dict.fromkeys(
            sid for r in rows for sid in r['step_ids'])]
        self.plan = {'steps': steps, 'paper_skill_results': rows}
        sha = plan_sha256(self.plan)
        self.requests = {'r': {'plan': self.plan, 'budget_usd': 10, 'research_contract': {
            'plan_sha256': sha, 'approval': {'approved': True, 'target_sha256': sha}}}}
        self.events = []
        self.cancelled = []

    async def publish(self, event):
        self.events.append(event)

    async def cancel_task(self, tid):
        self.cancelled.append(tid)
        return True

    def save_request(self, rid):
        pass

    def supports_resume(self, agent_id):
        return False


def ready(task):
    selected = task.meta['paper_skill']
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0.25,
        structured={'status': 'ready', 'paper_id': selected['paper_id'], 'skill_sha256': 'a' * 64,
                    'fulltext_receipt_sha256': ps.evidence_sha256(selected['fulltext_receipt'])})


@pytest.mark.asyncio
async def test_approved_dag_prepares_both_papers_concurrently_and_deduplicates_assigned_steps():
    first = row()
    first['step_ids'].append('s3')
    hub = Hub([first, row(2, 's2')])
    before = copy.deepcopy(hub.plan)
    orch = Orchestrator(hub)
    entered = {pid: asyncio.Event() for pid in ('pmid:1', 'pmid:2')}
    release = asyncio.Event()
    calls = []

    async def convert(task):
        calls.append(task)
        entered[task.meta['paper_skill']['paper_id']].set()
        await release.wait()
        return ready(task)

    async def dag(*_):
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), hub.s.runner.task_timeout_s)
        assert len(calls) == 2 and orch._request_budget_remaining('r') == 0
        assert all(t.budget_usd == t.request_budget_remaining_usd == 5 for t in calls)
        release.set()
        for sid in ('s1', 's2', 's3'):
            rows = await orch.paper_builds['r'].resolve(ps.attachments(hub.plan, sid, 'worker', 'r'))
            assert rows[0]['status'] == 'ready' and rows[0]['step_id'] == sid
            rows[0]['title'] = 'consumer mutation'
        assert len(calls) == 2
    orch.run_step = convert
    orch._run_dag = dag
    await orch.run_dag('r', 'fixture', hub.plan['steps'], {})
    assert hub.plan == before and not hub.cancelled and not orch.paper_builds
    assert orch._budget_bound('r', 10) == (0.5, 0, 0.5)
    assert any('2/2' in e['data'].get('text', '') for e in hub.events if e['type'] == 'agent.log')


@pytest.mark.parametrize('limiter', ['runner', 'orchestrator'])
@pytest.mark.asyncio
async def test_paper_parallelism_never_exceeds_either_existing_limit(limiter):
    hub = Hub([row(), row(2, 's2')])
    if limiter == 'runner':
        hub.s.runner.max_parallel = 1
    else:
        hub.s.orchestrator.max_parallel_steps = 1
    orch = Orchestrator(hub)
    first, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def convert(task):
        calls.append(task)
        if len(calls) == 1:
            first.set()
            await release.wait()
        return ready(task)
    orch.run_step = convert
    rows = [*ps.attachments(hub.plan, 's1', 'worker', 'r'),
            *ps.attachments(hub.plan, 's2', 'worker', 'r')]
    batch = await orch._start_paper_builds('r', rows)
    await first.wait()
    assert len(calls) == 1
    release.set()
    assert all(r['status'] == 'ready' for r in await batch.resolve(rows))
    await orch._close_paper_builds('r', batch)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_late_result_never_replaces_the_frozen_step_input_even_if_cancel_races_completion():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    entered = asyncio.Event()
    async def convert(task):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ready(task)  # remote completion races the cancellation request
    orch.run_step = convert
    batch = await orch._start_paper_builds('r', ps.attachments(hub.plan, 's1', 'worker', 'r'))
    await entered.wait()
    batch.deadline = asyncio.get_running_loop().time() - 1
    selected = ps.attachments(hub.plan, 's1', 'worker', 'r')
    delivered = await batch.resolve(selected)
    assert delivered[0]['status'] == 'unavailable' and delivered[0]['error'] == 'paper_skill_late'
    assert not delivered[0].get('skill_sha256') and not delivered[0].get('called')
    assert await batch.resolve(selected) == delivered
    await orch._close_paper_builds('r', batch)
    assert hub.cancelled == [next(iter(batch.tasks.values())).id]


@pytest.mark.parametrize('interruption', [ShutdownInterrupted, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_build_interruption_propagates_through_approved_dispatch(interruption):
    hub = Hub([row()])
    orch = Orchestrator(hub)
    failure = ShutdownInterrupted({'stopping': True}) if interruption is ShutdownInterrupted else interruption()
    orch.run_step = AsyncMock(side_effect=failure)
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'step_id': 's1', 'paper_skills': ps.attachments(hub.plan, 's1', 'worker', 'r')})
    with pytest.raises(interruption):
        await orch._resolve_step_paper_skills(task)


@pytest.mark.asyncio
async def test_replan_or_request_end_cancels_remote_build_and_retains_uncertain_dollar_hold():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    entered = asyncio.Event()
    async def convert(task):
        entered.set()
        await asyncio.Event().wait()
    async def dag(*_):
        await entered.wait()
        raise RuntimeError('synthetic replan')
    orch.run_step, orch._run_dag = convert, dag
    with pytest.raises(RuntimeError, match='synthetic replan'):
        await orch.run_dag('r', 'fixture', hub.plan['steps'], {})
    assert len(hub.cancelled) == 1 and not orch.paper_builds
    assert orch._budget_bound('r', 10) == (0, 0, 5)  # cancellation is not a zero-cost receipt
    tid = hub.cancelled[0]
    hub.requests['r']['cost_items'] = {tid: {'status': 'unknown'}}
    orch._count_recorded_costs('r')
    assert orch._budget_bound('r', 10) == (0, 1, 5)  # no duplicate hold after durable classification


@pytest.mark.asyncio
async def test_request_cancel_that_already_cancelled_the_waiter_still_sends_durable_runner_cancel():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    entered = asyncio.Event()
    async def convert(task):
        entered.set()
        await asyncio.Event().wait()
    orch.run_step = convert
    batch = await orch._start_paper_builds('r', ps.attachments(hub.plan, 's1', 'worker', 'r'))
    await entered.wait()
    await asyncio.gather(*orch.cancel_steps('r'), return_exceptions=True)
    await orch._close_paper_builds('r', batch)
    assert len(hub.cancelled) == 1


@pytest.mark.asyncio
async def test_exhausted_budget_records_unavailable_without_dispatch_or_new_budget_approval():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    orch.cost['r'] = 10
    orch.run_step = AsyncMock(side_effect=AssertionError('no budget grant or dispatch'))
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'step_id': 's1', 'paper_skills': ps.attachments(hub.plan, 's1', 'worker', 'r')})
    resolved = await orch._resolve_step_paper_skills(task)
    assert resolved.meta['paper_skills'][0]['error'] == 'paper_skill_budget_unavailable'
    orch.run_step.assert_not_awaited()
    assert hub.requests['r']['budget_usd'] == 10


@pytest.mark.asyncio
async def test_unapproved_plan_does_not_prefetch_and_post_build_plan_drift_still_requires_cp1():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    orch.run_step = AsyncMock()
    hub.requests['r']['research_contract']['approval']['approved'] = False
    with pytest.raises(ReuseApprovalRequired):
        await orch.run_dag('r', 'fixture', hub.plan['steps'], {})
    orch.run_step.assert_not_awaited()
    hub.requests['r']['research_contract']['approval']['approved'] = True
    async def changed(task):
        hub.plan['paper_skill_results'][0]['reason'] = 'changed after approval'
        return ready(task)
    orch.run_step = changed
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'step_id': 's1', 'paper_skills': ps.attachments(hub.plan, 's1', 'worker', 'r')})
    with pytest.raises(ReuseApprovalRequired):
        await orch._resolve_step_paper_skills(task)
    assert hub.requests['r']['research_contract']['approval']['approved'] is False


@pytest.mark.asyncio
async def test_approval_is_rechecked_after_waiting_for_the_budget_admission_lock():
    hub = Hub([row()])
    orch = Orchestrator(hub)
    orch.run_step = AsyncMock(side_effect=AssertionError('revoked approval cannot build'))
    lock = orch.budget_locks['r'] = asyncio.Lock()
    await lock.acquire()
    entering = asyncio.Event()
    start = orch._start_paper_builds
    async def observed(rid, rows):
        entering.set()
        return await start(rid, rows)
    orch._start_paper_builds = observed
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'step_id': 's1', 'paper_skills': ps.attachments(hub.plan, 's1', 'worker', 'r')})
    waiting = asyncio.create_task(orch._resolve_step_paper_skills(task))
    await entering.wait()
    hub.requests['r']['research_contract']['approval']['approved'] = False
    lock.release()
    with pytest.raises(ReuseApprovalRequired):
        await waiting
    orch.run_step.assert_not_awaited()
    assert not orch.paper_budget_holds


@pytest.mark.asyncio
async def test_converter_rechecks_approval_after_its_attempt_event_before_dispatch():
    hub = Hub([row()])
    hub.dispatch = AsyncMock(side_effect=AssertionError('revoked paper must not dispatch'))
    publish = hub.publish
    async def revoke(event):
        await publish(event)
        if event['type'] == 'request.step_attempt':
            hub.requests['r']['research_contract']['approval']['approved'] = False
    hub.publish = revoke
    task = ps.build_task(ps.attachments(hub.plan, 's1', 'worker', 'r')[0], hub.s)
    with pytest.raises(ReuseApprovalRequired):
        await Orchestrator(hub).run_step(task)
    hub.dispatch.assert_not_awaited()


def test_late_step_receipt_reports_unavailable_without_claiming_read_or_call():
    selected = {**row(), 'status': 'unavailable', 'error': 'paper_skill_late'}
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta={'paper_skills': [selected]})
    original = TaskResult(task_id=task.id, agent_id='worker', ok=True, text='No paper advice available')
    result = ps.with_preparation_outcome(original, task)
    data = {'s1': result.model_dump()}
    record = ps.records({'paper_skill_results': [row()]}, data)[0]
    assert record['status'] == 'unavailable' and not record['called'] and not record['built']
    assert record['usage'][0]['preparation_error'] == 'paper_skill_late'
    assert 'paper_skill_late' in ps.appendix({'paper_skill_results': [row()]}, data)
    assert original.provenance == {}
