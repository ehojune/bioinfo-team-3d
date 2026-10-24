import asyncio
from pathlib import Path

import pytest

from labhq.gateway.server import Hub
from labhq.models import ApprovalRequest, TaskResult
from labhq.pi_text import with_approval_copy
from labhq.settings import Settings, PolicySettings
from labhq.policy import evaluate_tool


@pytest.fixture
def hub(tmp_path, monkeypatch):
    # Provenance is synthetic here; interpretation tests must not launch Git.
    monkeypatch.setattr('labhq.integrations.rounds._git_commit', lambda: 'test-commit')
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / 'state')
    value = Hub(cfg)
    value.requests['r'] = {'id': 'r', 'status': 'running', 'budget_usd': 5, 'cost_usd': 1.0}
    value.agents['chief_of_staff'] = {'id': 'chief_of_staff', 'engine': 'claude_code', 'model': 'sonnet'}
    value.agent_runner['chief_of_staff'] = 'runner'
    value.runners['runner'] = object()
    yield value
    value.store.close()


def event(typ='agent.log', **data):
    return {'type': typ, 'request_id': 'r', 'agent_id': 'engineer',
            'data': data or {'text': 'Please compare the input tables before continuing.'}}


async def drain(hub):
    while hub.pi_interpreter.tasks:
        await asyncio.gather(*list(hub.pi_interpreter.tasks))


def mock_model(monkeypatch, hub, text='입력 표를 비교해 차이가 있는지 확인하려 합니다. 비교가 끝나면 다음 작업을 진행합니다.'):
    calls = []
    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=text, cost_usd=0.02)
    monkeypatch.setattr(hub, 'dispatch', dispatch)
    return calls


@pytest.mark.asyncio
async def test_translation_reservation_precedes_dispatch_and_settles_once(hub, monkeypatch):
    async def dispatch(task):
        reservation = hub.store.get('pi_translation_reservations', 'r')[task.id]
        assert reservation['task_id'] == reservation['reservation_id'] == task.id
        assert reservation['attempt'] == 1 and reservation['source'] == 'pi_translation'
        assert reservation['ceiling_usd'] == task.budget_usd == 0.05
        assert hub.requests['r']['budget_holds'] == {}
        # A duplicate research hold never doubles the same reservation.
        hub.requests['r']['budget_holds'] = {task.id: 0.05}
        assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(1.05)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          text='입력 표를 확인합니다.', cost_usd=0.02)
    hub.orchestrator.cost['r'] = 1.0
    monkeypatch.setattr(hub, 'dispatch', dispatch)
    await hub.publish(event())
    await drain(hub)
    assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(1.02)
    assert len(hub.store.get('pi_translation_reservations', 'r')) == 1


@pytest.mark.asyncio
async def test_orphan_reservation_survives_and_blocks_cap(hub, monkeypatch):
    calls = mock_model(monkeypatch, hub)
    hub.store.put('pi_translation_reservations', 'r', {'orphan': {
        'reservation_id': 'orphan', 'task_id': 'orphan', 'request_id': 'r',
        'agent_id': 'chief_of_staff', 'attempt': 1, 'state': 'reserved',
        'source': 'pi_translation', 'ceiling_usd': 0.05}})
    hub.requests['r']['budget_usd'] = 1.05
    hub.orchestrator.cost['r'] = 1.0
    await hub.publish(event())
    await drain(hub)
    assert not calls
    assert hub.orchestrator._budget_bound('r', 1.05)[2] == pytest.approx(1.05)


@pytest.mark.parametrize('raw', [[], ['invalid'], 'invalid', 0])
def test_invalid_translation_ledger_blocks_admission(hub, raw):
    hub.store.put('pi_translation_reservations', 'r', raw)
    assert hub.orchestrator._budget_bound('r', 5)[2] == float('inf')


@pytest.mark.asyncio
@pytest.mark.parametrize('seeded_aggregate', [False, True])
async def test_restart_counts_durable_cost_plus_orphan_before_translation(hub, monkeypatch, seeded_aggregate):
    calls = mock_model(monkeypatch, hub)
    hub.requests['r'].update(budget_usd=5, cost_usd=4.94, cost_known=True,
        cost_items={'old_research': {'status': 'actual', 'usd': 4.94}},
        cost_by_task={'old_research': 4.94})
    hub.store.put('pi_translation_reservations', 'r', {'orphan': {
        'reservation_id': 'orphan', 'task_id': 'orphan', 'request_id': 'r',
        'agent_id': 'chief_of_staff', 'attempt': 1, 'state': 'reserved',
        'source': 'pi_translation', 'ceiling_usd': 0.05}})
    if seeded_aggregate:
        hub.orchestrator.cost['r'] = 4.94
    assert not hub.orchestrator.cost_tasks.get('r')
    await hub.publish(event())
    await drain(hub)
    assert not calls
    assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(4.99)


def test_unknown_translation_keeps_ceiling_above_generic_reserve(hub):
    hub.s.policy.budget.per_task_usd = 0.01
    hub.store.put('pi_translation_reservations', 'r', {'t': {
        'reservation_id': 't', 'task_id': 't', 'request_id': 'r',
        'agent_id': 'chief_of_staff', 'attempt': 1, 'state': 'reserved',
        'source': 'pi_translation', 'ceiling_usd': 0.05}})
    hub.orchestrator.cost_tasks['r'] = {'t'}
    hub.orchestrator.cost_unknown['r'] = {'t'}
    assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(0.05)
    hub.orchestrator.cost_unknown['r'].clear()
    hub.orchestrator.cost['r'] = 0.002
    assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_resume_card_seeds_cost_ids_before_translation(hub, monkeypatch):
    from tests.test_research_protocol import valid_plan
    calls = mock_model(monkeypatch, hub)
    hub.requests['r'].update(cost_usd=1, cost_known=True,
        cost_items={'old': {'status': 'actual', 'usd': 1}}, cost_by_task={'old': 1},
        plan=valid_plan(steps=1))
    assert hub.orchestrator.resume_plan_card('r', hub.requests['r']) is not None
    assert hub.orchestrator.cost_tasks['r'] == {'old'}
    await hub.publish(event())
    await drain(hub)
    assert len(calls) == 1
    assert hub.orchestrator._budget_bound('r', 5)[2] == pytest.approx(1.02)


@pytest.mark.asyncio
async def test_interpreter_receives_tool_error_evidence(hub, monkeypatch):
    calls = mock_model(monkeypatch, hub)
    error = 'InputValidationError: StructuredOutput could not be parsed as JSON'
    await hub.publish(event(text='The response was stopped by a safety check.', tool_errors=[error]))
    await drain(hub)
    assert len(calls) == 1
    assert error in calls[0].prompt
    assert '도구 오류 원문이 있으면 그것을 근거로' in calls[0].prompt


def test_card_explained_by_chief_and_existing_copy_preserved():
    raw = {'kind': 'tool_permission', 'summary': 'PowerShell: approval required',
           'detail': {'tool_name': 'PowerShell', 'tool_input': {'command': 'New-Item -ItemType Directory -Force .tmp'}}}
    card = with_approval_copy(raw)
    assert card['pi_explainer'] == 'chief_of_staff'
    assert '폴더를 만들' in card['pi_summary']
    assert card['summary'] == raw['summary'] and card['detail'] == raw['detail']
    provided = {**raw, 'pi_summary': '직원이 쓴 쉬운 설명', 'pi_reason': '표 비교', 'pi_outcome': '비교 결과'}
    assert with_approval_copy(provided) == provided
    assert ApprovalRequest(**provided).pi_summary == provided['pi_summary']


@pytest.mark.parametrize('text,expected', [('ABC', False), ('BRCA1과 TP53을 확인했어요', False),
    ('Please check the input tables.', True), ('결과: Analysis finished without errors.', True)])
def test_english_ratio(text, expected):
    from labhq.pi_language import is_english
    assert is_english(text) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('typ,data', [('agent.log', {'text': 'Please compare the input tables.'}),
    ('agent.ask', {'to': 'colleague:analyst', 'question': 'Can you compare the input tables?'}),
    ('agent.answer', {'from': 'analyst', 'to': 'engineer', 'answer': 'The input tables have different sizes.'}),
    ('agent.log', {'text': '표 확인 중입니다.\n```python\nprint(1)\n```'})])
async def test_translation_is_one_shot_durable_and_counted(hub, monkeypatch, typ, data):
    calls = mock_model(monkeypatch, hub)
    await hub.publish(event(typ, **data))
    original = hub.events[0]
    assert len(calls) == 0  # delivery is never held up by the model
    await drain(hub)
    assert len(calls) == 1
    assert calls[0].meta['agent_overrides']['model'] == 'haiku'
    assert calls[0].meta['agent_overrides']['max_turns'] == 1
    assert calls[0].budget_usd == 0.05
    assert '도구 오류 원문이 있으면 그것을 근거로' in calls[0].prompt
    assert '확인하지 않은 원인을 지어내지 않는다' in calls[0].prompt
    assert hub.requests['r']['cost_usd'] == pytest.approx(1.02)
    saved = hub.store.messenger_history()['events'][0]
    assert saved['data']['pi_display']['status'] == 'translated'
    for key, value in data.items():
        assert saved['data'][key] == value
    hub.pi_interpreter.schedule(original)
    await drain(hub)
    assert len(calls) == 1 and hub.requests['r']['cost_usd'] == pytest.approx(1.02)
    reopened = Hub(hub.s)
    try:
        reopened.pi_interpreter.schedule(saved)
        assert not reopened.pi_interpreter.tasks
        assert reopened.store.messenger_history()['events'][0]['data']['pi_display'] == saved['data']['pi_display']
        assert reopened.requests['r']['cost_usd'] == pytest.approx(1.02)
    finally:
        reopened.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['disabled', 'model', 'runner', 'cap', 'request_cap'])
async def test_unavailable_or_over_cap_keeps_original_and_delivery(hub, monkeypatch, failure):
    calls = mock_model(monkeypatch, hub)
    if failure == 'disabled': hub.s.pi_language.enabled = False
    if failure == 'model': hub.s.pi_language.model = None
    if failure == 'runner': hub.runners.clear()
    if failure == 'cap': hub.s.pi_language.per_request_usd = 0
    if failure == 'request_cap': hub.requests['r']['budget_usd'] = 1
    await hub.publish(event())
    await drain(hub)
    message = hub.store.messenger_history()['events'][0]
    assert message['data']['text'] == event()['data']['text']
    assert message['data']['pi_display']['label'] == '번역 못 함'
    assert calls == []
    await hub.publish(event(text='한국어 후속 메시지'))
    assert len(hub.store.messenger_history()['events']) == 2


@pytest.mark.asyncio
async def test_failed_model_is_cached_and_charged(hub, monkeypatch):
    calls = mock_model(monkeypatch, hub, text='Still in English, no translation.')
    await hub.publish(event())
    await drain(hub)
    assert hub.events[0]['data']['pi_display']['status'] == 'failed'
    hub.pi_interpreter.schedule(hub.events[0])
    assert len(calls) == 1 and hub.requests['r']['cost_usd'] == pytest.approx(1.02)


@pytest.mark.asyncio
async def test_reservations_survive_restart_and_concurrent_messages(hub, monkeypatch):
    hub.s.pi_language.per_request_usd = 0.05
    calls = mock_model(monkeypatch, hub)
    await hub.publish(event())
    await hub.publish(event())
    await drain(hub)
    assert len(calls) == 1
    reopened = Hub(hub.s)
    try:
        reopened.agents = hub.agents
        reopened.agent_runner = hub.agent_runner
        reopened.runners = hub.runners
        calls2 = mock_model(monkeypatch, reopened)
        await reopened.publish(event())
        await drain(reopened)
        assert calls2 == []
    finally:
        reopened.store.close()


def test_interrupted_translation_is_failed_without_retry(hub):
    saved = hub.store.append_event(event(), 100)
    hub.store.put('pi_translation', str(saved['seq']), {'status': 'pending'})
    reopened = Hub(hub.s)
    try:
        message = reopened.store.messenger_history()['events'][0]
        assert message['data']['pi_display']['label'] == '번역 못 함'
        reopened.pi_interpreter.schedule(message)
        assert not reopened.pi_interpreter.tasks
    finally:
        reopened.store.close()


def test_report_humanizer_present_and_absent(tmp_path, monkeypatch):
    from labhq import report_style
    monkeypatch.setattr(report_style, 'humanizer_skill', lambda: None)
    report = '결과가 정리되어진다.\n> 가지고 있다\n`에 있어서`'
    assert report_style.check_report(report) == (report, '')
    skill = tmp_path / 'SKILL.md'
    skill.write_text('humanizer', encoding='utf8')
    monkeypatch.setattr(report_style, 'humanizer_skill', lambda: skill)
    fixed, note = report_style.check_report(report)
    assert fixed == report
    assert 'WARN' in note and '되어진다 1건' in note
    assert 'PASS' in report_style.check_report('결과가 정리된다.\n> 가지고 있다\n`에 있어서`')[1]


def test_new_item_force_is_not_recursive_delete(tmp_path):
    result = evaluate_tool('PowerShell', {'command': 'New-Item -Force .tmp'}, PolicySettings(),
                           workdir=str(tmp_path), allowed_roots=[str(tmp_path)])
    assert result.action == 'allow' and '재귀 삭제' not in result.reason


@pytest.mark.asyncio
async def test_raw_runner_card_gets_interpretation_but_existing_copy_does_not(hub, monkeypatch):
    calls = mock_model(monkeypatch, hub)
    raw = {'id': 'a', 'kind': 'tool_permission', 'summary': 'New-Item -Force .tmp',
           'detail': {'tool_input': {'command': 'New-Item -Force .tmp'}}}
    await hub.on_runner_message('runner', {'type': 'approval.requested', 'request_id': 'r', 'data': raw})
    await drain(hub)
    assert len(calls) == 1
    stored = hub.store.get('approval', 'a')['approval']
    assert stored['pi_display']['text'] and stored['summary'] == raw['summary']
    provided = {**raw, 'id': 'b', 'pi_summary': '임시 폴더를 만듭니다.',
                'pi_reason': '비교 결과를 담습니다.', 'pi_outcome': '폴더가 생깁니다.'}
    await hub.on_runner_message('runner', {'type': 'approval.requested', 'request_id': 'r', 'data': provided})
    await drain(hub)
    assert len(calls) == 1
    assert hub.store.get('approval', 'b')['approval']['pi_summary'] == provided['pi_summary']


@pytest.mark.asyncio
async def test_timeout_cancels_only_interpreter_and_does_not_retry(hub, monkeypatch):
    cancelled = []
    async def dispatch(task):
        await asyncio.Event().wait()
    async def cancel(tid):
        cancelled.append(tid)
    monkeypatch.setattr(hub, 'dispatch', dispatch)
    monkeypatch.setattr(hub, 'cancel_task', cancel)
    hub.s.pi_language.timeout_s = 0.01
    await hub.publish(event())
    await drain(hub)
    assert cancelled == ['pi_text_1']
    assert hub.events[0]['data']['pi_display']['status'] == 'failed'
    assert hub.requests['r']['cost_known'] is False


def test_final_report_keeps_interpretation_cost_and_optional_style_appendix(hub, monkeypatch, tmp_path):
    from labhq.orchestrator.cso import Orchestrator
    from labhq import report_style
    monkeypatch.setattr(report_style, 'humanizer_skill', lambda: tmp_path / 'SKILL.md')
    # Capture the normal terminal boundary without starting background publication.
    monkeypatch.setattr(hub, 'commit_terminal', lambda *args, **kwargs: None)
    orchestrator = Orchestrator(hub)
    orchestrator.cost['r'] = 0.5
    orchestrator._finish('r', '결과를 가지고 있다.', {}, True)
    assert hub.requests['r']['cost_usd'] == 1.0
    assert 'WARN' in hub.requests['r']['execution_record']


@pytest.mark.asyncio
async def test_followup_detail_uses_same_cached_display(hub, monkeypatch):
    calls = mock_model(monkeypatch, hub)
    raw = 'Please compare the input tables.'
    hub.requests['r']['followups'] = [{'id': 'f', 'answer': raw}]
    await hub.publish(event('request.followup_done', id='f', answer=raw, ok=True))
    await drain(hub)
    saved = hub.store.get('request', 'r')['followups'][0]
    assert saved['answer'] == raw and saved['pi_display']['status'] == 'translated'
    assert len(calls) == 1


# #588 review regressions: only synthetic text and mocked model dispatch.
SOURCES = [
    ('task.dispatched', {'title': 'Please compare the input tables.'}),
    ('agent.log', {'text': 'Please compare the input tables.'}),
    ('agent.ask', {'question': 'Please compare the input tables.'}),
    ('agent.answer', {'answer': 'Please compare the input tables.'}),
    ('request.followup_done', {'id': 'f', 'ok': True, 'answer': 'Please compare the input tables.'}),
    ('request.questions', {'questions': ['Please compare the input tables.']}),
    ('request.failed', {'error': 'Please compare the input tables.'}),
    ('request.completed', {'error': 'Please compare the input tables.'}),
    ('github.failed', {'error': 'Please compare the input tables.'}),
    ('request.created', {'text': 'Please compare the input tables.'}),
    ('request.followup', {'text': 'Please compare the input tables.'}),
    ('request.note', {'text': 'Please compare the input tables.'}),
    ('approval.resolved', {'note': 'Please compare the input tables.'}),
    ('approval.requested', {'id': 'a', 'pi_needs_interpretation': True,
                            'summary': 'Please compare the input tables.'}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('typ,data', SOURCES)
@pytest.mark.parametrize('origin', ['text', 'project_dirs', 'references', 'error', 'unknown'])
async def test_review_request_restriction_blocks_every_source(hub, monkeypatch, tmp_path, typ, data, origin):
    from labhq.settings import DataZone
    calls = mock_model(monkeypatch, hub)
    zone = str(tmp_path / 'restricted')
    hub.s.policy.data_zones = [DataZone(path=zone, level='restricted')]
    req = hub.requests['r']
    if origin == 'text': req['text'] = f'Compare {zone}/example.txt'
    if origin == 'project_dirs': req['project_dirs'] = [zone]
    if origin == 'references': req['references'] = [{'kind': 'path', 'value': zone}]
    if origin == 'error':
        def broken(_rid): raise ValueError('classification unavailable')
        monkeypatch.setattr(hub.orchestrator, '_request_restricted', broken)
    if origin == 'unknown': monkeypatch.setattr(hub.orchestrator, '_request_restricted', lambda rid: None)
    # No task ID or event-level restricted marker: consult the request itself.
    await hub.publish(event(typ, **data))
    await drain(hub)
    assert calls == []
    assert hub.events[0]['data']['pi_display']['status'] == 'failed'
    assert hub.store.all('pi_translation_budget') == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout', [False, True])
async def test_review_translation_updates_active_budget_once(hub, monkeypatch, timeout):
    orch = hub.orchestrator
    orch.cost['r'] = 1.0
    calls = mock_model(monkeypatch, hub)
    if timeout:
        async def dispatch(task):
            calls.append(task)
            await asyncio.Event().wait()
        async def cancel(tid): pass
        monkeypatch.setattr(hub, 'dispatch', dispatch)
        monkeypatch.setattr(hub, 'cancel_task', cancel)
        hub.s.pi_language.timeout_s = 0.01
    await hub.publish(event())
    await drain(hub)
    spent, unknown, bound = orch._budget_bound('r', 5)
    assert spent == pytest.approx(1.0 if timeout else 1.02)
    assert unknown == int(timeout)
    assert bound > spent if timeout else bound == spent
    before = orch._budget_bound('r', 5)
    orch._count_recorded_costs('r')
    hub.pi_interpreter.schedule(hub.events[0])
    await drain(hub)
    assert orch._budget_bound('r', 5) == before
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('exhausted', ['active', 'unknown'])
async def test_review_spent_request_does_not_translate(hub, monkeypatch, exhausted):
    calls = mock_model(monkeypatch, hub)
    hub.orchestrator.cost['r'] = 5 if exhausted == 'active' else 1
    if exhausted == 'unknown':
        hub.s.policy.budget.per_task_usd = 5
        hub.orchestrator.cost_unknown['r'] = {'unreported'}
    await hub.publish(event())
    await drain(hub)
    assert calls == []


@pytest.mark.asyncio
async def test_review_english_terms_in_korean_translation(hub, monkeypatch):
    text = 'single-cell RNA-seq 분석을 실행합니다.'
    mock_model(monkeypatch, hub, text=text)
    await hub.publish(event())
    await drain(hub)
    assert hub.events[0]['data']['pi_display']['text'] == text


def test_review_interpreter_is_not_a_request_step(hub):
    from labhq.integrations.rounds import build_record, record_sources
    hub.requests['r']['plan'] = {'steps': [{'id': 'analysis', 'agent_id': 'engineer'}]}
    for tid, kind, step in [('internal', 'pi_interpretation', None), ('work', 'step', 'analysis')]:
        hub.store.put('task', tid, {'request_id': 'r', 'accepted': True, 'kind': kind, 'step_id': step,
                                  'payload': {'agent_id': 'chief_of_staff'}})
    assert [t['id'] for t in hub.running_tasks()] == ['work']
    assert set(hub.request_summary(hub.requests['r'])['step_progress']['steps']) == {'analysis'}
    snap = hub.snapshot()['data']
    assert [t['id'] for t in snap['running_tasks']] == ['work']
    assert [t['id'] for t in snap['staff_tasks']] == ['work']
    assert set(snap['requests'][0]['step_status']) == {'analysis'}
    assert set(snap['requests'][0]['step_details']) == {'analysis'}
    hub.store.append_event({'type': 'task.dispatched', 'request_id': 'r', 'task_id': 'internal', 'data': {}}, 100)
    hub.store.append_event({'type': 'messenger.translated', 'request_id': 'r',
                            'data': {'message_seq': 1, 'pi_display': {'status': 'failed'}}}, 100)
    hub.store.append_event({'type': 'request.note', 'request_id': 'r', 'data': {'text': 'kept'}}, 100)
    sources = record_sources(hub)
    assert [task.get('kind') for task in sources['tasks']['r']] == ['step']
    assert [event['type'] for event in sources['events']['r']] == ['request.note']
    assert 'chief_of_staff' not in build_record(hub, 'r')['environment']['model_ids']


@pytest.mark.asyncio
async def test_review_shutdown_waits_for_terminal_translation(hub, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    hub.requests['r']['status'] = 'done'
    async def dispatch(task):
        entered.set()
        await finish.wait()
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          text='입력 표를 확인했습니다.', cost_usd=0.02)
    monkeypatch.setattr(hub, 'dispatch', dispatch)
    await hub.publish(event('request.followup_done', id='f', ok=True, answer='Please compare the tables.'))
    await entered.wait()
    try:
        readiness = hub.shutdown_readiness()
        assert readiness['ready'] is False
        assert any(f['id'] == 'r' and f['stage'] == 'pi_interpretation' for f in readiness['finishing'])
    finally:
        finish.set()
        await drain(hub)
    assert hub.shutdown_readiness()['ready'] is True
    assert hub.events[0]['data']['pi_display']['status'] == 'translated'
