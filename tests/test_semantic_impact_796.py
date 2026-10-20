"""Impact counts are process evidence, never invented scientific improvement."""
import copy

import pytest

from labhq.research.semantic_device_report import build_report, render_report
from tests.test_semantic_device_report import fixed_request
from tests.test_cp1_input_fit_796 import no_native_workers, run_plans


@pytest.mark.asyncio
async def test_metrics_use_cp1_trace_and_do_not_equate_mapped_types_with_fit():
    hub, req = await run_plans(['sequence_reads', 'raw_counts'])
    report = build_report(req, hub.s)
    metrics = report['impact_metrics']
    assert metrics['input_fit']['fit_steps']['value'] == 1
    assert metrics['input_fit']['unverified_steps']['value'] == 1
    assert metrics['input_fit']['pre_plan_corrections']['value'] == 1
    assert metrics['input_fit']['pre_plan_corrections']['denominator'] == 1
    assert metrics['input_fit']['declared_outputs']['value'] == 1
    assert metrics['input_fit']['declared_outputs']['denominator'] == 2
    assert metrics['scientific_performance_gain']['status'] == 'unmeasured'
    assert metrics['packs']['independently_checked_fulfillment']['value'] is None
    assert metrics['packs']['frozen_protocol_values']['value'] > 0
    assert metrics['checklist']['initial_required']['value'] > 0
    assert '과학 성능' in render_report(report)


def test_unavailable_metrics_are_not_zero_or_success_and_reuse_is_only_plan_adoption(tmp_path):
    settings, req = fixed_request(tmp_path)
    metrics = build_report(req, settings)['impact_metrics']
    assert metrics['checklist']['initial_missing']['value'] is None
    assert metrics['checklist']['corrected']['status'] == 'unmeasured'
    assert metrics['input_fit']['pre_plan_corrections']['value'] is None
    assert metrics['packs']['independently_checked_fulfillment']['status'] == 'unmeasured'
    assert metrics['reuse']['candidates']['value'] == 0
    assert metrics['reuse']['candidates']['status'] == 'measured'
    req['semantics_ab'] = {'arm': 'advisory', 'status': 'ok', 'offered': ['sem:aa', 'sem:bb']}
    req['plan']['steps'][0]['input_refs'] = ['sem:bb', 'sem:bb', 'sem:unoffered']
    req['semantics_observation']['provenance']['candidates'] = 5
    metrics = build_report(req, settings)['impact_metrics']
    assert metrics['reuse']['candidates']['value'] == 5
    assert metrics['reuse']['adopted_in_plan']['value'] == 1
    assert metrics['reuse']['adopted_in_plan']['denominator'] == 2
    assert metrics['reuse']['actual_use']['status'] == 'unmeasured'


def test_checklist_corrections_require_same_initially_required_item_and_review_evidence(tmp_path):
    settings, req = fixed_request(tmp_path)
    req['plan_validation'] = {'semantic_checks': [
        {'checklist': {'required': ['a', 'b'], 'missing': ['a', 'b']}},
        {'checklist': {'required': ['a', 'c'], 'missing': []}},
    ]}
    metrics = build_report(req, settings)['impact_metrics']['checklist']
    assert metrics['initial_required']['value'] == 2
    assert metrics['initial_missing']['value'] == 2
    assert metrics['corrected']['value'] == 1  # Removing b is not a fulfilled requirement.
    assert metrics['corrected']['denominator'] == 2
    assert metrics['review_pass']['value'] == 1
    assert metrics['review_fail']['value'] == 0  # assumption/n/a are not failed performed checks.
    req['review']['checklist'][0]['evidence'] = ''
    metric = build_report(req, settings)['impact_metrics']['checklist']['review_pass']
    assert metric['status'] == 'unmeasured' and metric['value'] is None


@pytest.mark.asyncio
async def test_disappearing_type_does_not_inflate_pre_plan_corrections():
    hub, req = await run_plans(['sequence_reads', 'not_a_type'])
    metrics = build_report(req, hub.s)['impact_metrics']
    assert metrics['input_fit']['pre_plan_corrections']['value'] == 0
    assert metrics['input_fit']['unverified_steps']['value'] == 2


def test_bench_comparison_preserves_metrics_and_does_not_invent_baseline_utility(tmp_path):
    from labhq import bench
    from tests.test_bench_legacy_report import _legacy_score
    settings, req = fixed_request(tmp_path)
    row = _legacy_score('labhq', True)
    row['semantic_impact_metrics'] = build_report(req, settings)['impact_metrics']
    result = {'case_id': 'fixture', 'mode': 'real', 'rows': [row, _legacy_score('baseline', True)]}
    rendered = bench._comparison_markdown(copy.deepcopy(result))
    assert result['rows'][0]['semantic_impact_metrics']['scientific_performance_gain']['value'] is None
    assert 'pre_plan_corrections' in rendered and 'saved impact evidence unavailable' in rendered
    assert '과학 성능 향상은 미측정' in rendered


@pytest.mark.asyncio
async def test_real_checklist_correction_is_measured_from_validation_trace():
    hub, req = await run_plans(['raw_counts', 'raw_counts'], missing_check=True)
    metrics = build_report(req, hub.s)['impact_metrics']['checklist']
    assert len(hub.calls) == 2 and len(hub.approvals) == 1
    assert metrics['initial_missing']['value'] == 1
    assert metrics['corrected']['value'] == 1 and metrics['corrected']['denominator'] == 1


def test_absent_observation_fields_do_not_become_measured_zero_counts(tmp_path):
    settings, req = fixed_request(tmp_path)
    req['semantics_observation'].pop('input_fit')
    req['semantics_observation']['provenance'].pop('candidates')
    metrics = build_report(req, settings)['impact_metrics']
    assert metrics['input_fit']['fit_steps']['status'] == 'unmeasured'
    assert metrics['reuse']['candidates']['value'] is None
