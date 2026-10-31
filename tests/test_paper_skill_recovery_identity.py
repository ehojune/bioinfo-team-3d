"""REMOTE CI ONLY: in-memory gateway recovery, no gateway instance, store, runner or process."""
import asyncio

import pytest

from labhq.gateway.server import Hub as GatewayHub
from labhq.models import Task
from labhq.orchestrator.cso import Orchestrator
from labhq.recruit import paper_skills as ps
from tests.test_paper_skill_waiting import Hub, ready, row


class RecoveredHub(Hub):
    dispatch = GatewayHub.dispatch
    dispatch_with_identity = GatewayHub.dispatch_with_identity
    recovery_attempt = GatewayHub.recovery_attempt
    _recovery_choice = GatewayHub._recovery_choice  # dispatch's own choice of the adopted earlier task (#862)
    _recovery_matches = GatewayHub._recovery_matches
    _matches_recovery = staticmethod(GatewayHub._matches_recovery)
    _same_runner_generation = staticmethod(GatewayHub._same_runner_generation)
    _pin_wsl_request = GatewayHub._pin_wsl_request  # dispatch pins a request that chose WSL before it picks a runner (#612)

    def __init__(self, tmp_path):
        selected = row()
        selected['step_ids'].append('s2')
        super().__init__([selected])
        self.s.gateway.state_dir = self.s.runner.state_dir = str(tmp_path / 'state')
        self.recovery_steps, self.recovery_started, self.recovered_tasks = {'r'}, {'r': 2}, set()
        self.agent_runner, self.runner_incarnations = {'recruiter': 'runner'}, {'runner': 'generation'}
        self.original = ps.build_task(ps.attachments(self.plan, 's1', 'worker', 'r')[0], self.s)
        self.entry = {'request_id': 'r', 'agent_id': 'recruiter', 'kind': 'paper_skill',
                      'step_id': self.original.meta['step_id'], 'attempt': 1, 'dispatched_at': 1,
                      'runner_id': 'runner', 'runner_incarnation': 'generation', 'accepted': True,
                      'payload': self.original.model_dump(mode='json')}
        class Store:
            def all(_self, kind):
                assert kind == 'task'
                return {'durable-converter': self.entry}
            def get(_self, kind, key):
                if kind == 'pi_translation_reservations':
                    assert key == 'r'
                    return {}
                assert kind == 'step_checkpoint'
                return None
        self.store = Store()
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def _await_prior_task(self, tid, entry, agent_id, request_id, sid):
        assert tid == 'durable-converter' and entry is self.entry
        self.entered.set()
        await self.release.wait()
        return ready(self.original).model_copy(update={'task_id': tid})

    async def cancel_task(self, tid):
        self.cancelled.append(tid)
        return tid == 'durable-converter'  # a newly generated ID would miss the real converter


@pytest.mark.parametrize('outcome', ['cancel', 'complete'])
@pytest.mark.asyncio
async def test_gateway_selected_recovery_identity_owns_cancel_and_cost_not_the_new_task_id(tmp_path, outcome):
    hub = RecoveredHub(tmp_path)
    orch = Orchestrator(hub)
    selected = ps.attachments(hub.plan, 's1', 'worker', 'r')
    batch = await orch._start_paper_builds('r', selected)
    orch.paper_builds['r'] = batch
    await hub.entered.wait()  # actual Hub.dispatch selected and bound the durable ID before its await
    fresh_id = next(iter(batch.tasks.values())).id
    assert fresh_id != 'durable-converter'
    assert orch.paper_task_ids['r'][fresh_id] == 'durable-converter'
    assert orch.paper_budget_holds['r'] == {'durable-converter': 5}
    task = Task(agent_id='worker', request_id='r', prompt='ready fixture step',
                meta={'step_id': 's1', 'paper_skills': selected})
    frozen = await orch._resolve_step_paper_skills(task)
    assert frozen.meta['paper_skills'][0]['status'] == 'unavailable' and not hub.cancelled
    if outcome == 'cancel':
        await orch._close_paper_builds('r', batch)
        assert hub.cancelled == ['durable-converter']
        assert orch._budget_bound('r', 10) == (0, 0, 5)  # cancel request is not termination/cost proof
        hub.requests['r']['cost_items'] = {'durable-converter': {'status': 'unknown'}}
        orch._count_recorded_costs('r')
        assert orch._budget_bound('r', 10) == (0, 1, 5)  # actual unknown cost replaces its hold once
    else:
        hub.release.set()
        await asyncio.gather(*batch.jobs.values())
        assert (await batch.resolve(selected))[0]['status'] == 'unavailable'  # no late upgrade of s1
        assert (await batch.resolve(ps.attachments(hub.plan, 's2', 'worker', 'r')))[0]['status'] == 'ready'
        await orch._close_paper_builds('r', batch)
        assert not hub.cancelled and orch.paper_budget_holds['r'] == {}
        assert orch.cost_tasks['r'] == {'durable-converter'}
        assert orch._budget_bound('r', 10) == (0.25, 0, 0.25)
