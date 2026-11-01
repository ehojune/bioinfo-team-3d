import asyncio

import pytest

from labhq.orchestrator.cso import Orchestrator
from tests.test_cso import FakeHub, result


@pytest.mark.asyncio
@pytest.mark.parametrize('budget,per_task,first_cost,second_sent,scout_cap', [
    (0.5, 5.0, 0.1, True, 0.4), (0.5, 5.0, 0.5, False, None),
    (5.019, 5.015, 0.1, True, 4.91),
])
async def test_preplan_waits_for_small_budget_hold_to_settle(budget, per_task, first_cost, second_sent, scout_cap):
    started, release = asyncio.Event(), asyncio.Event()

    async def dispatch(task):
        if task.meta.get('kind') == 'briefing':
            started.set()
            await release.wait()
            return result(task, text='brief', cost_usd=first_cost)
        return result(task, text='done', cost_usd=0.0)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.s.policy.budget.per_task_usd = per_task
    hub.requests['r']['budget_usd'] = budget
    hub.s.orchestrator.chief_of_staff_agent = 'chief'
    hub.s.orchestrator.precedent_agent = 'scout'
    for name in ('chief', 'scout'):
        hub.agents[name] = {'id': name, 'name': name, 'role': 'test', 'engine': 'mock'}
    orch = Orchestrator(hub)
    run = asyncio.create_task(orch.run_request('r'))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.sleep(0.05)
        assert not hub.approvals
        assert [t.meta.get('kind') for t in hub.calls] == ['briefing']
        release.set()
        await asyncio.wait_for(run, 5)
        scouts = [t for t in hub.calls if t.meta.get('kind') == 'precedent']
        assert bool(scouts) is second_sent
        assert not hub.requests['r'].get('budget_holds')
        if second_sent:
            assert scouts[0].budget_usd == pytest.approx(scout_cap)
        else:
            assert any(card['kind'] == 'budget' for card in hub.approvals)
    finally:
        release.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_preplan_still_runs_in_parallel_when_budget_has_room():
    started, scout_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def dispatch(task):
        if task.meta.get('kind') == 'briefing':
            started.set()
            await release.wait()
        elif task.meta.get('kind') == 'precedent':
            scout_started.set()
        return result(task, text='done', cost_usd=0.0)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.requests['r']['budget_usd'] = 40.0
    hub.s.orchestrator.chief_of_staff_agent = 'chief'
    hub.s.orchestrator.precedent_agent = 'scout'
    for name in ('chief', 'scout'):
        hub.agents[name] = {'id': name, 'name': name, 'role': 'test', 'engine': 'mock'}
    run = asyncio.create_task(Orchestrator(hub).run_request('r'))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(scout_started.wait(), 5)
        assert not hub.approvals
    finally:
        release.set()
        await asyncio.wait_for(run, 5)
