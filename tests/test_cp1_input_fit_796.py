"""CP1 correction with mock dispatch/approval only; no research execution."""
import copy
import threading

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research import input_fit as fit
from labhq.settings import Settings
from tests.test_research_protocol import PACK, MiniHub, valid_pack_values, valid_plan


@pytest.fixture(autouse=True)
def no_native_workers(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('mock/static validation must not start a real thread')
    monkeypatch.setattr(threading.Thread, 'start', denied)


async def run_plans(types, *, missing_check=False, budget=None, orchestrator=Orchestrator, observer=None):
    settings = Settings()
    if 'semantics' in Settings.model_fields:  # the optional switch is absent after full removal
        settings.semantics = observer
    settings.research.enabled = True
    settings.research.active_packs = [PACK]
    settings.plan.declare_output_types = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    drafts = []
    for data_type in types:
        plan = valid_plan(steps=2, pack_values=valid_pack_values())
        plan['steps'][0]['output_types'] = [{'name': 'result1.tsv', 'data_type': data_type, 'format': 'tsv'}]
        plan['steps'][1].update(input_refs=['step:s1/outputs/result1.tsv'], depends_on=['s1'])
        drafts.append(plan)
    if missing_check:
        drafts[0]['checklist'].pop(next(iter(drafts[0]['checklist'])))
    async def reply(task):
        assert task.meta['kind'] == 'plan', 'no employee/research dispatch is authorized'
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(drafts.pop(0)),
                          cost_usd=0.6 if budget else 0)
    hub = MiniHub(settings, reply, mode='orchestrate', work_kind='research', text='compare conditions')
    if budget:
        hub.requests['r']['budget_usd'] = budget
        async def deny(**kwargs):
            hub.approvals.append(kwargs)
            return {'approved': False, 'note': 'fixture denial', 'approval_id': 'budget_no', 'decided_at': 1.0}
        hub.request_approval = deny
    await orchestrator(hub).run_request('r')
    return hub, hub.requests['r']


@pytest.mark.asyncio
async def test_cp1_corrects_real_mismatch_with_diagnostic_and_warning_history():
    hub, req = await run_plans(['sequence_reads', 'raw_counts'])
    assert len(hub.calls) == 2 and len(hub.approvals) == 1
    assert 's2' in hub.calls[1].prompt and 'sequence_reads' in hub.calls[1].prompt
    assert 'de_analysis' in hub.calls[1].prompt and 'step:s1/outputs/result1.tsv' in hub.calls[1].prompt
    trace = req['plan_validation']['semantic_checks']
    assert [row['input_fit'] for row in trace] == [
        {'fit': 0, 'mismatch': 1, 'unknown': 1}, {'fit': 1, 'mismatch': 0, 'unknown': 1}]
    assert trace[0]['mismatches'][0]['step_id'] == 's2'
    assert len(trace[1]['resolved_mismatches']) == 1
    assert any('input_fit' in warning for warning in hub.approvals[0]['detail']['warnings'])
    assert any('sequence_reads' in warning for warning in req['plan_warnings'])
    assert hub.approvals[0]['detail']['plan_validation']['semantic_checks'] == trace
    assert req['outcome'] == 'plan_approved'


@pytest.mark.asyncio
@pytest.mark.parametrize('data_type', ['not_a_type', 'http://edamontology.org/data_2044'])
async def test_unrecognized_type_is_unverified_not_a_fabricated_mismatch(data_type):
    hub, req = await run_plans([data_type])
    assert len(hub.calls) == 1 and len(hub.approvals) == 1
    check = req['plan_validation']['semantic_checks'][0]
    assert check['input_fit'] == {'fit': 0, 'mismatch': 0, 'unknown': 2}
    assert not check['mismatches'] and not check['resolved_mismatches']


@pytest.mark.asyncio
async def test_repeated_mismatch_stops_after_the_correction_bound_before_cp1():
    hub, req = await run_plans(['sequence_reads'] * 4)  # first plan + 3 corrections (#948)
    assert len(hub.calls) == 4 and not hub.approvals
    assert req['outcome'] == 'plan_invalid'
    assert len(req['plan_validation']['semantic_checks']) == 4


@pytest.mark.asyncio
async def test_lost_declaration_is_not_counted_as_a_proven_correction():
    hub, req = await run_plans(['sequence_reads', 'not_a_type'])
    trace = req['plan_validation']['semantic_checks']
    assert len(hub.calls) == 2 and len(hub.approvals) == 1
    assert trace[-1]['input_fit']['unknown'] == 2
    assert trace[-1]['resolved_mismatches'] == []


def test_diagnostics_share_verdict_rules_without_changing_aggregate_contract():
    def plan(data_type):
        return {'protocol': {'packs': [{'id': 'single_cell_de', 'version': '2'}]}, 'steps': [
            {'id': 'source', 'output_types': [] if data_type is None else [
                {'name': 'outputs/source.tsv', 'data_type': data_type, 'vocab': fit.current().sha256}]},
            {'id': 'analysis', 'agent_id': 'analyst', 'input_refs': ['step:source/outputs/source.tsv']}]}
    draft = plan('sequence_reads')
    rows = fit.mismatch_diagnostics(draft)
    assert fit.evaluate(draft) == {'fit': 0, 'mismatch': 1, 'unknown': 1}
    assert rows == [{'step_id': 'analysis', 'input_ref': 'step:source/outputs/source.tsv',
                     'data_type': 'sequence_reads', 'operation': 'de_analysis'}]
    assert fit.resolved_mismatches(plan('raw_counts'), rows) == rows
    assert fit.resolved_mismatches(plan(None), rows) == []


@pytest.mark.asyncio
async def test_fit_correction_keeps_budget_denial_before_cp1():
    hub, req = await run_plans(['sequence_reads', 'raw_counts'], budget=1.0)
    assert len(hub.calls) == 2 and [card['kind'] for card in hub.approvals] == ['budget']
    assert req['status'] == 'failed' and '예산 승인 거부' in req['report']
