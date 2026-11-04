"""Request-level input evidence: metadata only, fake dispatch, no external file access."""
import copy

import pytest

from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research import continuation as c
from tests.test_continuation_review_807 import partial_hub, approve
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['references', 'project_dirs'])
async def test_request_inputs_omitted_from_steps_are_declined_before_cp1(monkeypatch, field):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    assert all(not s['input_refs'] for s in req['plan']['steps'][:2])
    req[field] = ([{'kind': 'path', 'path': 'synthetic-input'}] if field == 'references'
                  else ['synthetic-input'])  # no file is opened, even when output hashes match
    await orch._prepare_research_reuse('r', req['plan'])
    snapshot = req['research_contract']['continuation']['reuse_snapshot']
    assert snapshot['reuse'] == [] and snapshot['nodes'] == {}
    assert 'request-level input' in snapshot['rerun']['s1']
    assert not hub.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['references', 'project_dirs'])
async def test_adding_request_input_after_cp1_stops_without_rerun(monkeypatch, field):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    await orch._prepare_research_reuse('r', req['plan'])
    approve(req)
    assert await orch._verify_research_reuse('r', req['plan'])
    before = copy.deepcopy(req['results'])
    req[field] = ['synthetic-input']
    assert not await orch._verify_research_reuse('r', req['plan'])
    assert not req['research_contract']['approval']['approved']
    assert req['outcome'] == 'reuse_approval_required'
    assert req['results'] == before and not hub.calls


def test_legacy_archive_without_request_input_proof_is_not_reusable():
    from tests.test_continuation_797 import archive, AGENTS
    old = archive()
    old.pop('reuse_no_request_inputs', None)
    carry = c.carry_over(old, old['plan'], {}, [])
    problems = c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))
    assert set(problems) == set(carry.reuse)
    assert all('request-level input' in reason for reason in problems.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['step', 'result_correction', 'slot_repair'])
@pytest.mark.parametrize('source', ['references', 'project_dirs', 'reference_dirs', 'meta_project_dirs',
                                   'none', 'during_dispatch'])
async def test_actual_dispatch_records_sticky_input_absence_not_current_metadata(monkeypatch, kind, source):
    hub, orch = partial_hub(monkeypatch)
    req = hub.requests['r']
    # No continuation seal here: isolate first-round execution evidence, with synthetic results only.
    req['research_contract'].pop('continuation')
    hub.s.orchestrator.step_max_attempts = 1
    meta = {'kind': kind, 'step_id': 's1'}
    if source in ('references', 'project_dirs'):
        req[source] = ['synthetic-input']
    elif source == 'reference_dirs':
        meta[source] = ['synthetic-input']
    elif source == 'meta_project_dirs':
        meta['project_dirs'] = ['synthetic-input']
    async def reply(task):
        if source == 'during_dispatch':
            req['references'] = ['synthetic-input']
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True)
    hub.reply = reply
    task = Task(agent_id='worker', request_id='r', prompt='fixture', meta=meta)
    await orch.run_step(task)
    assert req.get('reuse_no_request_inputs', {}).get('s1') is (source == 'none')
    req.pop('references', None)
    req.pop('project_dirs', None)
    task.meta.pop('reference_dirs', None)
    task.meta.pop('project_dirs', None)
    # Clearing metadata and dispatching again cannot rewrite an unproved execution as input-free.
    hub.reply = reply_without_inputs
    await orch.run_step(task)
    assert req['reuse_no_request_inputs']['s1'] is (source == 'none')
    archived = c.archive_round(req, {})
    assert archived['reuse_no_request_inputs']['s1'] is (source == 'none')
    from tests.test_continuation_797 import archive, AGENTS
    old = archive()
    old['reuse_no_request_inputs'] = archived['reuse_no_request_inputs']
    carry = c.carry_over(old, old['plan'], {}, [])
    problems = c.proof_problems(old, old['plan'], carry, c.tool_signatures(old['plan'], AGENTS))
    assert ('s1' in problems) is (source != 'none')


async def reply_without_inputs(task):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True)
