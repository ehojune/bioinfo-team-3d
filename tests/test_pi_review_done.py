import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, create_app
from labhq.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['done', 'failed', 'cancelled'])
async def test_review_is_durable_idempotent_and_audited(tmp_path, status):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = Hub(settings)
    hub.requests['r'] = {'id': 'r', 'text': 'Review result', 'status': status, 'report': 'Result'}
    hub._commit_terminal_record('r', 'request.completed' if status == 'done' else 'request.failed', {})
    assert hub.request_summary(hub.requests['r'])['pi_review_pending'] is True
    assert hub.snapshot()['data']['requests'][0]['pi_review_pending'] is True
    result = hub.review_done('r')
    assert result['pi_reviewed_at'] > 0
    assert result['pi_review_pending'] is False
    assert hub.review_done('r') == result
    events = [e for e in hub.store.events_since(0) if e['type'] == 'request.reviewed']
    assert len(events) == 1
    assert events[0]['data']['actor'] == 'pi'
    stored = hub.store.get('request', 'r')
    assert stored['pi_review_audit'][-1]['at'] == result['pi_reviewed_at']
    assert stored['status'] == status
    assert Hub(settings).requests['r']['pi_reviewed_at'] == result['pi_reviewed_at']


def test_review_endpoint_rejects_running_and_requires_auth(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.gateway.client_token = 'test-token'
    app = create_app(settings)
    hub = app.state.hub
    hub.requests['r'] = {'id': 'r', 'status': 'running'}
    with TestClient(app) as client:
        assert client.post('/api/requests/r/review-done').status_code == 401
        headers = {'Authorization': 'Bearer test-token'}
        assert client.post('/api/requests/r/review-done', headers=headers).status_code == 409
        hub.requests['r']['status'] = 'failed'
        assert client.get('/api/requests?status=running', headers=headers).json() == []
        waiting = client.get('/api/requests?status=all', headers=headers).json()
        assert waiting[0]['pi_review_pending'] is True
        assert client.post('/api/requests/r/review-done', headers=headers).status_code == 200
        assert client.get('/api/requests/r', headers=headers).json()['pi_reviewed_at'] > 0


def test_cli_review_and_status_labels(monkeypatch, capsys):
    from labhq import cli
    from labhq.costs import cost_text
    from labhq.request_status import request_display_label

    calls = []
    monkeypatch.setattr(cli, '_api', lambda s, method, url, **kw:
                        calls.append((method, url, kw)) or {'pi_reviewed_at': 100})
    monkeypatch.setattr(Settings, 'load', lambda *a, **kw: Settings())
    cli.main(['review-done', 'r'])
    assert calls == [('POST', '/api/requests/r/review-done', {'json': {}})]
    assert '검토·확인 완료' in capsys.readouterr().out
    assert request_display_label({'status': 'done', 'pi_review_pending': True}) == '보고서 검토 대기'
    assert cost_text(25.36, False) == '$25.36 + 비용 모름'
