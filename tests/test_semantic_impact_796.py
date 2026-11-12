"""Preserved core evidence assertions; removed impact metrics do not judge research utility."""
import pytest
from tests.test_cp1_input_fit_796 import no_native_workers, run_plans


@pytest.mark.asyncio
async def test_checklist_correction_keeps_the_actual_missing_item_and_review_boundary():
    hub, req = await run_plans(['raw_counts', 'raw_counts'], missing_check=True)
    assert len(hub.calls) == 2 and len(hub.approvals) == 1
    trace = req['plan_validation']['semantic_checks']
    initial = trace[0]['checklist']
    final = trace[-1]['checklist']
    assert len(initial['missing']) == 1
    assert set(initial['missing']) <= set(initial['required']) & set(final['required'])
    assert final['missing'] == []
    assert all(call.meta['kind'] == 'plan' for call in hub.calls)
    assert req['outcome'] == 'plan_approved'


@pytest.mark.asyncio
async def test_disappearing_type_cannot_become_a_proven_input_fit_correction():
    hub, req = await run_plans(['sequence_reads', 'not_a_type'])
    trace = req['plan_validation']['semantic_checks']
    assert trace[0]['input_fit']['mismatch'] == 1
    assert trace[-1]['resolved_mismatches'] == []
    assert trace[-1]['input_fit'] == {'fit': 0, 'mismatch': 0, 'unknown': 2}
