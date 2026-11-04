"""Recovery boundaries using existing fake hubs; no real runner or installer."""
import pytest

from labhq.models import Task
from labhq.orchestrator.cso import Orchestrator
from tests.test_facilities_diagnosis import make_hub, fake_dispatch, step
from tests.test_facilities_fixes import FixHub


async def test_diagnosis_answer_is_not_recovery(tmp_path):
    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    try:
        outcome = await hub.orchestrator.run_step(step())
        ask_id = hub.requests['r']['facilities_diagnoses']['s1']
        entry = hub.store.get('ask', ask_id)
        assert [t.agent_id for t in calls] == ['worker', 'facilities']
        assert entry['answer']['status'] == 'answered'
        assert not outcome.ok and outcome.facilities_fix is None
        assert outcome.environment['id'] == 'docker_daemon_down'
    finally:
        hub.store.close()


@pytest.mark.parametrize('decision', [
    {'approved': False, 'status': 'declined'},
    {'approved': False, 'status': 'timed_out'},
])
async def test_unapproved_fix_never_resumes(decision):
    hub = FixHub(decision=decision)
    outcome = await Orchestrator(hub).run_step(step())
    assert not outcome.ok and outcome.facilities_fix['status'] == decision['status']
    assert len(hub.calls) == 1 and not hub.fix_records


@pytest.mark.parametrize('rerun_ok', [True, False])
async def test_approved_fix_records_distinct_rerun_result(rerun_ok):
    hub = FixHub(rerun_ok=rerun_ok)
    outcome = await Orchestrator(hub).run_step(step())
    assert len(hub.fix_requests) == 1 and len(hub.calls) == 2
    assert hub.calls[-1].meta['facilities_rerun'] is True
    assert outcome.ok is rerun_ok
    assert outcome.facilities_fix['status'] == ('succeeded' if rerun_ok else 'failed')
    assert [x[2]['status'] for x in hub.fix_records] == ['applied', outcome.facilities_fix['status']]
