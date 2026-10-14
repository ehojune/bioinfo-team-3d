"""CP4 shares report review; all workers and source lookups are fake."""
import asyncio
import copy
import json

import pytest

from labhq.gateway.server import Hub, RequestIn
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings
from tests.test_research_report import _hub, _kinds, REPORT, REVISE


async def draft_hub(**kwargs):
    hub = _hub(**kwargs)
    hub.requests['r']['id'] = 'r'
    hub.requests['r']['research_options'] = {'contract_version': 2, 'final_checkpoint': True}
    await Orchestrator(hub).run_request('r')
    return hub


@pytest.mark.asyncio
async def test_final_draft_waits_in_existing_report_review_not_done():
    hub = await draft_hub()
    req = hub.requests['r']
    assert req['status'] == 'review_pending'
    assert _kinds(hub) == ['plan', 'step', 'review', 'synthesis']
    assert [a['kind'] for a in hub.approvals] == ['research_plan', 'research_evidence']
    assert req['report'].startswith(REPORT)
    assert req['research_contract']['final_draft']['content']['report'] == req['report']


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'report': 'unsupported [[claim:s1/missing]].'}, {'review': REVISE}])
async def test_incomplete_or_revision_never_reaches_cp4(kwargs):
    hub = await draft_hub(**kwargs)
    assert hub.requests['r']['status'] != 'done'
    assert 'final_draft' not in hub.requests['r']['research_contract']


def persisted(tmp_path, req):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / 'state')
    hub = Hub(settings)
    hub.requests['r'] = copy.deepcopy(req)
    hub.save_request('r')
    return hub


def target(req):
    draft = req['research_contract']['final_draft']
    return {k: draft[k] for k in ('revision', 'target_sha256')}


@pytest.mark.asyncio
async def test_accept_receipt_restart_and_finish_preserve_approved_body(tmp_path):
    from labhq.evidence.verify import final_checkpoint_problems
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    before = copy.deepcopy(hub.requests['r'])
    result = hub.review_done('r', **target(before))
    assert result['status'] == 'done'
    assert hub.review_done('r', **target(before)) == result
    receipt = hub.requests['r']['research_contract']['final_receipt']
    assert receipt['gate'] == 'research_final' and receipt['delegation_scope'] == []
    assert receipt['request_id'] == 'r' and receipt['decider'] == 'PI' and receipt['decided_at'] > 0
    assert not final_checkpoint_problems(hub.requests['r'])
    restored = Hub(hub.s)
    await restored.orchestrator.run_request('r', resume=True)
    restored.orchestrator._finish('r', 'must not replace the approved report', {}, True)
    assert restored.requests['r']['report'] == before['report']
    assert restored.requests['r']['report_appendix'] == before['report_appendix']
    assert restored.requests['r']['status'] == 'done'


@pytest.mark.asyncio
@pytest.mark.parametrize('part', ['report', 'report_appendix', 'claims', 'review', 'receipt', 'missing', 'component'])
async def test_changed_content_or_stale_receipt_cannot_be_done(tmp_path, part):
    from labhq.evidence.verify import final_checkpoint_problems
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    req = hub.requests['r']
    hub.review_done('r', **target(req))
    contract = req['research_contract']
    if part in {'report', 'report_appendix'}:
        req[part] += 'changed'
    elif part == 'claims':
        req['results']['s1']['structured']['claims'][0]['statement'] += 'changed'
    elif part == 'review':
        contract['review']['verdict'] = 'revise'
    elif part == 'missing':
        contract.pop('final_receipt')
    elif part == 'component':
        contract['final_draft']['audit_sha256'] = 'a' * 64
    else:
        contract['final_receipt']['revision'] = 100
    assert final_checkpoint_problems(req)
    hub.save_request('r')
    assert req['status'] != 'done' and not req['pi_reviewed_at']
    assert Hub(hub.s).requests['r']['status'] != 'done'
    if part in {'report', 'report_appendix', 'claims', 'review'}:
        with pytest.raises(ValueError):
            hub.review_done('r', **target(req))


@pytest.mark.asyncio
@pytest.mark.parametrize('decision,status', [('rejected', 'rejected'), ('timed_out', 'failed'), ('cancelled', 'cancelled')])
async def test_negative_decisions_survive_restart(tmp_path, decision, status):
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    req = hub.requests['r']
    if decision == 'rejected':
        hub.review_done('r', decision=decision, **target(req))
    elif decision == 'cancelled':
        await hub.cancel_request('r')
    else:
        req['research_contract']['final_draft']['deadline_at'] = 0
        hub.recover_final_reviews()
        await hub.final_review_timers['r']
    assert req['status'] == status
    assert req['research_contract']['final_receipt']['decision'] == decision
    with pytest.raises(ValueError):
        hub.review_done('r', **target(req))
    if decision == 'timed_out':
        assert req['pi_review_pending'] and req['pi_reviewed_at'] is None
        hub.review_done('r')  # acknowledge timeout; never accepts the draft
        assert req['status'] == 'failed' and req['pi_reviewed_at']
        assert req['research_contract']['final_receipt']['decision'] == 'timed_out'
    restored = Hub(hub.s)
    await restored.orchestrator.run_request('r', resume=True)
    assert restored.requests['r']['status'] == status


@pytest.mark.asyncio
async def test_restart_reuses_pending_draft_without_synthesis_or_config(tmp_path):
    fake = await draft_hub()
    before = copy.deepcopy(fake.requests['r'])
    fake.s.research.enabled = False
    await Orchestrator(fake).run_request('r', resume=True)
    assert _kinds(fake).count('synthesis') == 1
    assert fake.requests['r'] == before
    hub = persisted(tmp_path, before)
    restored = Hub(hub.s)
    assert not restored.approvals  # no resume or CP4 second card
    assert restored.requests['r']['research_contract']['final_draft'] == before['research_contract']['final_draft']
    assert restored.snapshot()['data']['requests'][0]['final_checkpoint']['target_sha256'] == target(before)['target_sha256']
    assert restored.review_done('r', **target(before))['status'] == 'done'


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,wanted', [({}, True), ({'mode':'plan_only'}, False),
    ({'mode':'direct'}, False), ({'work_kind':'simple'}, False)])
async def test_option_is_frozen_at_creation(tmp_path, monkeypatch, changes, wanted):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.research.enabled = settings.research.evidence_checkpoint = settings.research.final_checkpoint = True
    hub = Hub(settings)
    monkeypatch.setattr(hub, 'track_request', lambda rid, coro: coro.close())
    rid = hub.create_request(RequestIn(**{'text':'compare conditions', 'work_kind':'research', **changes}))
    settings.research.final_checkpoint = False
    saved = Hub(settings).requests[rid]
    assert saved['research_options'] == {'contract_version':2, 'final_checkpoint':wanted}


@pytest.mark.asyncio
async def test_cp1_only_and_legacy_are_not_upgraded(tmp_path, monkeypatch):
    from labhq.evidence.verify import final_checkpoint_problems
    fake = _hub()
    fake.s.research.final_checkpoint = True
    await Orchestrator(fake).run_request('r')
    assert fake.requests['r']['status'] == 'done'  # pre-existing request, no option snapshot
    assert not final_checkpoint_problems(fake.requests['r'])
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.research.enabled = settings.research.final_checkpoint = True
    hub = Hub(settings)
    monkeypatch.setattr(hub, 'track_request', lambda rid, coro: coro.close())
    rid = hub.create_request(RequestIn(text='compare conditions', work_kind='research'))
    assert not hub.requests[rid]['research_options']['final_checkpoint']


@pytest.mark.asyncio
async def test_review_requires_the_displayed_hash_and_revision(tmp_path):
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    for supplied in ({}, {'revision':99, 'target_sha256':'a'*64}, {'decision':'nonsense'}):
        with pytest.raises(ValueError):
            hub.review_done('r', **supplied)
    assert hub.requests['r']['status'] == 'review_pending'


@pytest.mark.asyncio
async def test_bundle_carries_immutable_draft_and_receipt(tmp_path):
    from labhq.request_bundle import build_request_bundle
    from tests.test_request_bundle import configured, write
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    hub.review_done('r', **target(hub.requests['r']))
    settings = configured(tmp_path)
    workdir = tmp_path / 'runs' / 'task'
    import platform
    write(workdir / 'manifest.json', json.dumps({'host': platform.node()}))
    write(workdir / 'outputs' / 'result.tsv', 'data')
    result = hub.requests['r']['results']['s1']
    result.update(workdir=str(workdir), workdir_id='task')
    bundle = build_request_bundle(hub.requests['r'], settings)
    from pathlib import Path
    folder = Path(bundle['path'])
    record = json.loads((folder / 'final_checkpoint.json').read_text(encoding='utf-8'))
    assert record['receipt']['target_sha256'] == record['draft']['target_sha256']
    assert record['draft']['content']['report'] == hub.requests['r']['report']
    assert 'final_checkpoint.json' in (folder / 'MANIFEST.tsv').read_text(encoding='utf-8')
    from labhq.evidence.verify import final_bundle_problems
    from labhq.ro_crate import verify_bundle_copy
    assert not final_bundle_problems(record)
    assert not any('CP4' in p for p in verify_bundle_copy(folder, 'r')['problems'])
    record['receipt']['revision'] = 9
    (folder / 'final_checkpoint.json').write_text(json.dumps(record), encoding='utf-8')
    assert any('CP4' in p for p in verify_bundle_copy(folder, 'r')['problems'])
    (folder / 'final_checkpoint.json').unlink()
    assert any('CP4' in p for p in verify_bundle_copy(folder, 'r')['problems'])


@pytest.mark.asyncio
async def test_crash_between_draft_and_terminal_commit_recovers_without_dispatch(tmp_path):
    fake = await draft_hub()
    req = fake.requests['r']
    req['status'] = 'running'
    hub = persisted(tmp_path, req)
    restored = Hub(hub.s)
    assert restored.requests['r']['status'] == 'review_pending'
    assert not restored.approvals


@pytest.mark.asyncio
async def test_decision_during_bundle_copy_rebuilds_receipt_before_clearing_pending(tmp_path, monkeypatch):
    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    snapshots = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def to_thread(fn, *args):
        snapshots.append(copy.deepcopy(args[1]))
        if len(snapshots) == 1:
            entered.set()
            await release.wait()
        return {}
    monkeypatch.setattr(asyncio, 'to_thread', to_thread)
    monkeypatch.setattr(hub, '_has_bundle_workdir', lambda rid: True)
    monkeypatch.setattr(hub, '_apply_bundle_outcome', lambda *args: None)
    hub.requests['r']['bundle_pending'] = True
    first = hub._schedule_bundle('r', {})
    await entered.wait()
    hub.review_done('r', **target(hub.requests['r']))
    release.set()
    await first
    await asyncio.sleep(0)
    assert len(snapshots) == 2
    assert snapshots[-1]['research_contract']['final_receipt']['decision'] == 'accepted'
    assert not hub.store.get('request', 'r').get('bundle_pending')


def test_cp4_cli_sends_explicit_target_and_prints_pending(monkeypatch, capsys):
    from labhq import cli
    calls = []
    monkeypatch.setattr(Settings, 'load', lambda *a, **kw: Settings())
    monkeypatch.setattr(cli, '_api', lambda *a, **kw: calls.append(kw) or {'pi_reviewed_at': 1})
    cli.main(['review-done', 'r', '--revision', '1', '--sha256', 'a'*64, '--reject'])
    assert calls == [{'json': {'revision':1, 'target_sha256':'a'*64, 'decision':'rejected'}}]
    cli.render({'type':'request.completed', 'request_id':'r', 'data':{'status':'review_pending', 'ok':True,
               'final_checkpoint':{'revision':1, 'target_sha256':'a'*64}}})
    assert '보고서 검토 대기 (CP4)' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_existing_review_endpoint_requires_matching_cp4_target(tmp_path):
    import httpx
    from labhq.gateway.server import create_app
    fake = await draft_hub()
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.gateway.client_token = 'test-token'
    app = create_app(settings)
    hub = app.state.hub
    hub.requests['r'] = fake.requests['r']
    headers = {'Authorization':'Bearer test-token'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/requests/r/review-done', json={})).status_code == 401
        assert (await client.post('/api/requests/r/review-done', json={}, headers=headers)).status_code == 409
        result = await client.post('/api/requests/r/review-done', json=target(hub.requests['r']), headers=headers)
        assert result.status_code == 200 and result.json()['status'] == 'done'
    stored = hub.store.get('request', 'r')
    assert stored['pi_reviewed_at'] == stored['research_contract']['final_receipt']['decided_at']


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', [None, 'accepted', 'rejected', 'timed_out'])
async def test_github_terminal_delivery_waits_for_cp4_and_recovers(tmp_path, decision):
    from labhq.settings import ProjectSettings
    from tests.test_github_reporter import fake_github

    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    hub.s.projects = [ProjectSettings(id='demo', repo='o/p')]
    hub.requests['r']['project_id'] = 'demo'
    hub.store.put('github_issue', 'r', {'number': 7})
    hub.reporter.issues['r'] = 7
    calls = []
    hub.reporter.transport = fake_github(calls)
    # Use the real terminal checkpoint reached by Orchestrator._finish.
    hub._commit_terminal_record('r', 'request.completed', {'ok': True})
    await hub.reporter.drain()
    try:
        if decision is None:
            assert not calls
            assert not hub.store.all('terminal_delivery')
            # Previously queued draft events must also stay silent after upgrade/restart.
            await hub.reporter.handle(next(e for e in hub.events if e['type'] == 'request.completed'))
            assert not calls
            return
        calls.clear()
        # Crash after the durable decision but before its delivery is queued.
        hub.reporter.submit = lambda event: None
        hub.review_done('r', decision=decision, **target(hub.requests['r']))
        restored = Hub(hub.s, github_transport=fake_github(calls))
        restored.recover_terminal_deliveries()
        try:
            await restored.reporter.drain()
            comments = [body['body'] for method, path, body in calls if path.endswith('/comments')]
            assert len(comments) == 1
            assert comments[0].startswith('🏁 완료' if decision == 'accepted' else '💥 실패')
            assert sum(method == 'PUT' for method, _, _ in calls) == (2 if decision == 'accepted' else 0)
            assert sum(method == 'PATCH' for method, _, _ in calls) == (1 if decision == 'accepted' else 0)
            assert not restored.store.all('terminal_delivery')
            before = list(calls)
            restored.recover_terminal_deliveries()
            await restored.reporter.drain()
            assert calls == before
        finally:
            if restored.reporter.worker:
                restored.reporter.worker.cancel()
    finally:
        for timer in hub.final_review_timers.values():
            timer.cancel()
        if hub.reporter.worker:
            hub.reporter.worker.cancel()


@pytest.mark.asyncio
async def test_cp4_accept_rebuilds_existing_report_only_bundle(tmp_path, monkeypatch):
    from labhq.evidence.verify import final_bundle_problems
    from tests.test_request_bundle import configured

    fake = await draft_hub()
    req = copy.deepcopy(fake.requests['r'])
    for result in req['results'].values():
        result.pop('workdir_id', None)
        result.pop('workdir', None)
    hub = Hub(configured(tmp_path))
    hub.requests['r'] = req
    # Imported report-only bundle: exercise the receipt refresh, not source discovery.
    folder = tmp_path / 'bundle'
    folder.mkdir()
    def bundle_outcome(rid, request=None, *args):
        contract = (request or req)['research_contract']
        record = {'request_id': rid, 'contract_version': 2,
                  'draft': contract['final_draft'], 'receipt': contract.get('final_receipt')}
        (folder / 'final_checkpoint.json').write_text(json.dumps(record), encoding='utf-8')
        return {'bundle': {'path': str(folder), 'status': 'complete'}}
    monkeypatch.setattr(hub, '_bundle_outcome', bundle_outcome)
    hub.schedule_terminal('r', 'request.completed', {'ok': True})
    assert not json.loads((folder / 'final_checkpoint.json').read_text(encoding='utf-8'))['receipt']
    hub.review_done('r', **target(req))
    try:
        assert req['bundle_pending'] is True
        await asyncio.sleep(0)  # let the scheduled refresh register its bundle task
        await hub.terminal_tasks['r']
        record = json.loads((folder / 'final_checkpoint.json').read_text(encoding='utf-8'))
        assert record['receipt']['decision'] == 'accepted'
        assert not final_bundle_problems(record)
        assert not hub.store.get('request', 'r').get('bundle_pending')
    finally:
        for timer in hub.final_review_timers.values():
            timer.cancel()


@pytest.mark.asyncio
async def test_cp4_accept_wakes_recall_and_publishes_editor_after_review(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    fake = await draft_hub()
    hub = persisted(tmp_path, fake.requests['r'])
    hub.s.runner.workspace_root = str(tmp_path / 'runs')
    hub.s.github.auto_publish_results = True
    req = hub.requests['r']
    req['text'] = 'checkpoint recall integration'
    req['result_storage'] = {'mode': 'existing', 'repo': 'owner/study', 'status': 'selected'}
    upload = AsyncMock()
    monkeypatch.setattr(hub.result_storage, 'upload', upload)
    # CP4 draft is neither a completed search hit nor a publishable edition.
    hub._commit_terminal_record('r', 'request.completed', {'ok': True})
    assert 'r' not in hub.result_storage.tasks
    await hub.knowledge.drain()
    assert not hub.knowledge.search('checkpoint')
    upload.assert_not_awaited()
    hub.knowledge.wake.clear()
    try:
        hub.review_done('r', **target(req))
        assert hub.knowledge.wake.is_set()
        assert hub.store.get('knowledge_pending', 'r')
        await hub.result_storage.tasks['r']
        upload.assert_awaited_once()
        assert req['result_storage']['status'] == 'published'
        assert req['pi_reviewed_at'] == req['research_contract']['final_receipt']['decided_at']
        await hub.knowledge.drain()
        assert hub.knowledge.search('checkpoint')[0]['request_id'] == 'r'
    finally:
        await hub.result_storage.close()
        for timer in hub.final_review_timers.values():
            timer.cancel()
