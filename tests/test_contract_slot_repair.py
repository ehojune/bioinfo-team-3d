"""Missing evidence remains visible; independent research work still reaches review (#657)."""

import json

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, failed_request_summary
from tests.test_research_protocol import valid_plan
from tests.test_research_report import _hub


def slot_hub(answer):
    hub = _hub()
    original = hub.reply
    hub.supports_resume = lambda _agent: True

    async def reply(task):
        if task.meta['kind'] == 'slot_repair':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=answer)
        res = await original(task)
        if task.meta['kind'] in {'step', 'result_correction'}:
            res.structured['evidence'][0]['slots'] = []
            res.session_id = 'slot-session'
        return res

    hub.reply = reply
    return hub


def budget_slot_hub(*, cap, step_cost):
    hub = slot_hub({'e1': {'status': 'attempted_none', 'reason': 'search attempt recorded'}})
    original = hub.reply
    hub.requests['r']['budget_usd'] = cap
    hub.s.policy.budget.per_task_usd = 0.10

    async def reply(task):
        result = await original(task)
        cost = step_cost if task.meta['kind'] == 'step' else 0.0
        return result.model_copy(update={'cost_usd': cost, 'cost_known': True})

    hub.reply = reply
    return hub


@pytest.mark.asyncio
@pytest.mark.parametrize(('step_cost', 'expected_questions'), [(1.0, 0), (0.95, 0), (0.80, 1)])
async def test_slot_question_only_runs_when_its_budget_reservation_fits(step_cost, expected_questions):
    hub = budget_slot_hub(cap=1.0, step_cost=step_cost)
    await Orchestrator(hub).run_request('r')

    req = hub.requests['r']
    calls = [task for task in hub.calls if task.meta['kind'] == 'slot_repair']
    assert len(calls) == expected_questions
    if calls:
        assert calls[0].budget_usd == pytest.approx(0.10)
    assert req['status'] == 'done'
    assert req['results']['s1']['ok']
    assert req['results']['s1']['structured']['evidence'][-1]['origin'] == (
        'slot_repair' if expected_questions else 'omitted_by_output')
    assert not any(approval['kind'] == 'budget' for approval in hub.approvals)
    assert float(req.get('cost_usd') or 0) <= req['budget_usd']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['attempted_found', 'attempted_none', 'not_attempted'])
async def test_missing_slots_get_one_small_question_without_whole_result_correction(status):
    hub = slot_hub({'e1': {'status': status, 'reason': 'search attempt recorded'}})
    await Orchestrator(hub).run_request('r')
    calls = [t for t in hub.calls if t.meta['kind'] == 'slot_repair']
    assert len(calls) == 1
    assert calls[0].resume_session_id == 'slot-session'
    assert calls[0].meta['required_outputs'] == []
    assert calls[0].meta['optional_outputs'] == []
    assert set(calls[0].output_schema['properties']) == {'e1'}
    assert not any(t.meta['kind'] == 'result_correction' for t in hub.calls)
    row = hub.requests['r']['results']['s1']['structured']['evidence'][-1]
    assert row['slots'] == ['e1'] and row['origin'] == 'slot_repair'
    assert row['attempt_status'] == status and row['status'] == 'unavailable'
    assert hub.requests['r']['status'] == 'done'


def test_slot_repair_fields_are_not_part_of_the_initial_step_schema():
    from labhq.research import contract as rc

    properties = rc.RESEARCH_RESULT_SCHEMA['$defs']['Evidence']['properties']
    assert 'origin' not in properties and 'attempt_status' not in properties


@pytest.mark.asyncio
async def test_unanswered_slot_is_warning_in_cp2_reviewer_and_report_limitations():
    hub = slot_hub({})
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    assert req['results']['s1']['ok'] and req['status'] == 'done'
    card = next(a['detail'] for a in hub.approvals if a['kind'] == 'research_evidence')
    row = card['results']['s1']['evidence'][-1]
    assert row['origin'] == 'omitted_by_output' and row['source'] is None
    assert 'omitted_by_output' in json.dumps(card['warnings'])
    assert 'omitted_by_output' in next(t.prompt for t in hub.calls if t.meta['kind'] == 'review')
    assert 'omitted_by_output' in req['report'].split('## 한계', 1)[1]


@pytest.mark.asyncio
async def test_s2_failure_only_blocks_descendants_and_independent_results_reach_cp2(tmp_path):
    hub = _hub()
    original = hub.reply
    hub.s.gateway.state_dir = str(tmp_path / 'state')

    async def reply(task):
        if task.meta['kind'] == 'plan':
            plan = valid_plan(steps=4)
            plan['steps'][2]['depends_on'] = ['s2']
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)
        sid = task.meta.get('step_id')
        if task.meta['kind'] == 'step' and sid == 's2':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error='internal worker failure')
        res = await original(task)
        if task.meta['kind'] == 'step' and sid == 's4':
            path = hub.requests['r']['plan']['steps'][3]['outputs'][0]
            res.outputs = [path]
            res.output_sha256 = {path: 'a' * 64}
            res.structured.update(claims=[], evidence=[], links=[], artifact_refs=[])
            res.structured['evidence'] = [{**(await original(task)).structured['evidence'][0], 'slots': ['e4']}]
            res.structured['artifact_refs'] = [{'artifact_id': 'a1', 'path': res.outputs[0]}]
        return res

    hub.reply = reply
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    assert req['results']['s4']['ok'] and not req['results']['s3']['ok']
    assert not any(t.meta.get('step_id') == 's3' for t in hub.calls)
    assert req['status'] == 'done', req.get('error')
    card = next(a['detail'] for a in hub.approvals if a['kind'] == 'research_evidence')
    assert set(card['results']) == {'s1', 's4'}
    assert 's2' in json.dumps(card['warnings']) and 's3' in req['report'].split('## 한계')[1]


@pytest.mark.asyncio
async def test_pi_rejected_independent_step_stops_request_before_cp2():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        if task.meta['kind'] == 'plan':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=valid_plan(steps=2))
        if task.meta['kind'] == 'step' and task.meta.get('step_id') == 's2':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error_kind='ask_rejected', error='PI rejected the blocking decision')
        return await original(task)

    hub.reply = reply
    await Orchestrator(hub).run_request('r')

    req = hub.requests['r']
    assert req['results']['s1']['ok'] and not req['results']['s2']['ok']
    assert req['status'] == 'failed' and req['outcome'] == 'research_failed'
    assert not any(a['kind'] == 'research_evidence' for a in hub.approvals)
    assert not any(t.meta['kind'] == 'review' for t in hub.calls)


@pytest.mark.asyncio
async def test_contract_failed_independent_step_still_reaches_cp2():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        if task.meta['kind'] == 'plan':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=valid_plan(steps=2))
        if task.meta['kind'] == 'step' and task.meta.get('step_id') == 's2':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error_kind='contract', error='invalid research result contract')
        return await original(task)

    hub.reply = reply
    await Orchestrator(hub).run_request('r')

    req = hub.requests['r']
    assert req['results']['s1']['ok'] and not req['results']['s2']['ok']
    assert req['status'] == 'done'
    card = next(a['detail'] for a in hub.approvals if a['kind'] == 'research_evidence')
    assert set(card['results']) == {'s1'}
    assert 's2' in json.dumps(card['warnings'])


@pytest.mark.asyncio
async def test_internal_failure_records_signature_and_no_developer_task_for_pi(tmp_path):
    hub = _hub()
    original = hub.reply
    hub.s.gateway.state_dir = str(tmp_path / 'state')

    async def reply(task):
        if task.meta['kind'] == 'step':
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error='internal error: schema failure')
        return await original(task)

    hub.reply = reply
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    assert '개발자에게 알리기' not in req['report']
    assert '개발팀에 자동 보고됨' in req['report']
    records = [json.loads(line) for line in (tmp_path / 'state' / 'dev-reports.jsonl').read_text('utf-8').splitlines()]
    assert any(r['request_id'] == 'r' and r['signature'] for r in records)


def test_failure_summary_has_no_manual_developer_escalation():
    summary, _ = failed_request_summary({}, {}, 'invalid research result contract')
    assert '개발자에게 알리기' not in summary


@pytest.mark.asyncio
async def test_environment_still_uses_facilities_instead_of_slot_repair(tmp_path):
    from tests.test_facilities_diagnosis import make_hub, fake_dispatch, step
    from labhq.orchestrator.cso import failure_kind

    hub = make_hub(tmp_path)
    calls = fake_dispatch(hub)
    try:
        outcome = await hub.orchestrator.run_step(step())
        assert failure_kind(outcome) == 'environment'
        assert [t.meta['kind'] for t in calls] == ['step', 'consult']
        assert calls[-1].agent_id == 'facilities'
        assert not hub.requests['r'].get('dev_reports')
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', ['worker exited', 'HTTP 503 overloaded', 'authentication required', 'quota exceeded'])
async def test_slot_question_failure_and_no_resume_become_honest_gaps(error):
    for resume in (True, False):
        hub = slot_hub({})
        hub.supports_resume = lambda _agent: resume
        original = hub.reply

        async def reply(task):
            if task.meta['kind'] == 'slot_repair':
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error=error)
            return await original(task)

        hub.reply = reply
        await Orchestrator(hub).run_request('r')
        req = hub.requests['r']
        assert req['status'] == 'done'
        assert req['results']['s1']['structured']['evidence'][-1]['origin'] == 'omitted_by_output'
        assert sum(t.meta['kind'] == 'slot_repair' for t in hub.calls) == int(resume)


def test_generated_gap_cannot_be_promoted_to_support_or_fabricated_source():
    from pydantic import ValidationError
    from labhq.research.contract import repair_result_slots, ResearchResult
    from tests.test_research_cp2 import _standalone_result

    _plan, value = _standalone_result()
    value['evidence'][0]['slots'] = []
    result = repair_result_slots(value, ['e1'], {})
    assert not result.evidence[-1].counts
    broken = result.model_dump(mode='json')
    broken['links'][0]['evidence_id'] = broken['evidence'][-1]['id']
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(broken)
    broken = result.model_dump(mode='json')
    broken['evidence'][-1]['status'] = 'observed'
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(broken)


@pytest.mark.asyncio
async def test_salvage_that_removes_only_required_row_asks_about_its_gap():
    hub = slot_hub({})
    hub.s.research.result_corrections = 0
    original = hub.reply

    async def reply(task):
        res = await original(task)
        if task.meta['kind'] == 'step':
            res.structured['evidence'][0]['slots'] = ['e1']
            del res.structured['evidence'][0]['assessment_reason']
        return res

    hub.reply = reply
    await Orchestrator(hub).run_request('r')
    card = next(a['detail'] for a in hub.approvals if a['kind'] == 'research_evidence')
    assert card['results']['s1']['claims'] == []
    assert card['results']['s1']['evidence'][0]['origin'] == 'omitted_by_output'
    assert card['refused_rows'] and card['unsupported_claims']
    assert sum(t.meta['kind'] == 'slot_repair' for t in hub.calls) == 1


@pytest.mark.asyncio
async def test_arbitrary_bad_plan_binding_never_enters_slot_repair():
    hub = slot_hub({})
    hub.s.research.result_corrections = 0
    original = hub.reply

    async def reply(task):
        res = await original(task)
        if task.meta['kind'] == 'step':
            res.structured['plan_sha256'] = '1' * 64
        return res

    hub.reply = reply
    await Orchestrator(hub).run_request('r')
    assert hub.requests['r']['status'] == 'failed'
    assert not any(t.meta['kind'] == 'slot_repair' for t in hub.calls)


async def test_configured_dev_log_keeps_report_and_local_fallback_deduplicates(tmp_path):
    from labhq.gateway.server import Hub
    from tests.test_round_records import settings, round_request

    hub = Hub(settings(tmp_path))
    try:
        round_request(hub, 'r', 'failed')
        (await hub.orchestrator._record_dev_failure('r', 's2', 'schema error', 'contract'))
        (await hub.orchestrator._record_dev_failure('r', 's2', 'schema error', 'contract'))
        assert len((tmp_path / 'state' / 'dev-reports.jsonl').read_text('utf-8').splitlines()) == 1
        hub.s.dev_log.enabled = True
        hub.s.dev_log.repo = 'example/private-log'
        (await hub.orchestrator._record_dev_failure('r', 's4', 'internal error', 'terminal'))
        record = hub.rounds.write('r')
        assert len(record['result']['dev_reports']) == 2
        assert record['result']['dev_reports'][-1]['step_id'] == 's4'
    finally:
        hub.store.close()


# ---------- #1054: one wrong group beside an out-of-step claim must not fail the whole step ----------

def _recited_result(extra_claim=False):
    import copy

    from tests.test_research_cp2 import _extra_observation, _standalone_result

    plan, result = _standalone_result()
    result = copy.deepcopy(result)
    recited = _extra_observation("e_recited")
    recited["source"] = copy.deepcopy(result["evidence"][0]["source"])
    recited["independence_group"] = "other_group"  # the same source declared in a second group
    result["evidence"].append(recited)
    if extra_claim:
        extra = copy.deepcopy(result["claims"][0])
        extra["id"] = "c_extra"
        result["claims"].append(extra)
        result["links"].append({"claim_id": "c_extra", "claim_revision": 1, "evidence_id": "e1",
                                "relation": "supports", "rationale": "same table"})
    return plan, result


def test_recited_source_alone_refuses_only_the_second_row():
    from labhq.research.contract import salvage_research_result

    plan, result = _recited_result()
    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert problems == [] and salvaged is not None and unsupported == []
    assert [row.id for row in salvaged.evidence] == ["e1"]
    assert [(row["row_type"], row["row_id"]) for row in refused] == [("evidence", "e_recited")]


def test_a_claim_outside_the_step_no_longer_fails_a_result_with_a_recited_source():
    from labhq.research.contract import salvage_research_result

    plan, result = _recited_result(extra_claim=True)
    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert problems == [] and salvaged is not None
    assert [claim.id for claim in salvaged.claims] == ["c1"]
    assert [row.id for row in salvaged.evidence] == ["e1"]
    assert all(link.claim_id == "c1" for link in salvaged.links)
    assert {(row["row_type"], row["row_id"]) for row in refused} == {("evidence", "e_recited"), ("claim", "c_extra"),
                                                              ("link", "c_extra@1->e1:supports")}
    assert [row["claim_id"] for row in unsupported] == ["c_extra"]
    assert "outside the claim_ids" in unsupported[0]["reason"]


def test_an_out_of_step_claim_alone_is_refused_and_the_result_is_kept():
    import copy

    from labhq.research.contract import salvage_research_result
    from tests.test_research_cp2 import _standalone_result

    plan, result = _standalone_result()
    result = copy.deepcopy(result)
    extra = copy.deepcopy(result["claims"][0])
    extra["id"] = "c_extra"
    result["claims"].append(extra)
    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert problems == [] and salvaged is not None and [claim.id for claim in salvaged.claims] == ["c1"]
    assert [row["claim_id"] for row in unsupported] == ["c_extra"]


def test_a_result_with_no_claim_of_the_step_still_fails():
    import copy

    from labhq.research.contract import salvage_research_result
    from tests.test_research_cp2 import _standalone_result

    plan, result = _standalone_result()
    result = copy.deepcopy(result)
    result["claims"][0]["id"] = "c_other"
    result["links"] = [{**link, "claim_id": "c_other"} for link in result["links"]]
    salvaged, _refused, _unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert salvaged is None and any("outside the claim_ids" in problem for problem in problems)


def test_an_undeclared_evidence_slot_is_still_not_salvaged():
    import copy

    from labhq.research.contract import salvage_research_result
    from tests.test_research_cp2 import _standalone_result

    plan, result = _standalone_result()
    result = copy.deepcopy(result)
    result["evidence"][0]["slots"] = ["not_a_declared_slot"]
    salvaged, _refused, _unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert salvaged is None and problems


def test_a_refused_out_of_step_claim_also_records_its_removed_link():
    from labhq.research.contract import salvage_research_result

    plan, result = _recited_result(extra_claim=True)
    _salvaged, refused, _unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert problems == []
    assert any(row["row_type"] == "link" and row["row_id"].startswith("c_extra@1->e1")
               and "contract-refused claim c_extra" in row["reason"] for row in refused)


def test_pruning_an_out_of_step_claim_cannot_end_with_none_of_the_steps_own_claims():
    import copy

    from labhq.research.contract import salvage_research_result
    from tests.test_research_cp2 import _standalone_result

    plan, result = _standalone_result()
    result = copy.deepcopy(result)
    result["evidence"][0].pop("assessment_reason")  # c1's only support is refused, so c1 is dropped too
    extra = copy.deepcopy(result["claims"][0])
    extra["id"] = "c_extra"
    result["claims"].append(extra)
    result["links"].append({"claim_id": "c_extra", "claim_revision": 1, "evidence_id": "e1",
                            "relation": "supports", "rationale": "same table"})
    salvaged, _refused, _unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert salvaged is None and problems
