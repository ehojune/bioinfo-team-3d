import time

from fastapi.testclient import TestClient

from labhq import cli
from labhq.gateway.server import Hub, create_app
from labhq.models import TaskResult
from labhq.request_status import is_office_request, is_past_request
from labhq.settings import Settings
from tests.support.time_proxy import RuntimeProxy


def test_gateway_marks_one_shared_office_and_session_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr('labhq.gateway.server.time', RuntimeProxy(time, time=lambda: 100.0))
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = Hub(settings)
    hub.requests.update({
        'past': {'id': 'past', 'status': 'done', 'created_at': 10, 'pi_reviewed_at': 90},
        'review': {'id': 'review', 'status': 'failed', 'created_at': 20},
        'new-past': {'id': 'new-past', 'status': 'done', 'created_at': 110, 'pi_reviewed_at': 115},
    })

    assert is_past_request(hub.requests['past'])
    assert not is_office_request(hub.requests['past'])
    assert hub.request_summary(hub.requests['past'])['office_current'] is False
    assert hub.request_summary(hub.requests['review'])['office_current'] is True
    assert hub.request_summary(hub.requests['new-past'])['session_current'] is True
    snapshot = hub.snapshot()['data']
    assert snapshot['session_started_at'] == 100.0
    assert {row['id']: row['office_current'] for row in snapshot['requests']} == {
        'past': False, 'review': True, 'new-past': False}


def test_gateway_request_totals_include_history_beyond_list_limit(tmp_path, monkeypatch):
    monkeypatch.setattr('labhq.gateway.server.time', RuntimeProxy(time, time=lambda: 100.0))
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.gateway.client_token = 'test-token'
    app = create_app(settings)
    app.state.hub.requests.update({
        **{f'past-{index}': {'id': f'past-{index}', 'status': 'done', 'created_at': index / 10,
                             'pi_reviewed_at': 90, 'cost_usd': 1}
           for index in range(201)},
        'current': {'id': 'current', 'status': 'running', 'created_at': 110, 'cost_usd': 2},
    })

    with TestClient(app) as client:
        headers = {'Authorization': f'Bearer {settings.gateway.client_token}'}
        assert len(client.get('/api/requests?status=all&limit=200', headers=headers).json()) == 200
        totals = client.get('/api/requests/summary', headers=headers).json()

    assert totals == {
        'request_count': 202,
        'past_count': 201,
        'session': {'cost_usd': 2.0, 'estimated_usd': 0.0, 'unknown_count': 0,
                    'legacy_unknown': False},
        'total': {'cost_usd': 203.0, 'estimated_usd': 0.0, 'unknown_count': 0,
                  'legacy_unknown': False},
    }


def test_old_reviewed_request_returns_to_office_for_followup_and_counts_only_new_cost(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('labhq.gateway.server.time', RuntimeProxy(time, time=lambda: now[0]))
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.gateway.client_token = 'test-token'
    app = create_app(settings)
    request = {
        'id': 'old', 'status': 'done', 'created_at': 10, 'pi_reviewed_at': 90,
        'followups': [{'id': 'fu', 'status': 'waiting_quota'}],
        'cost_usd': 30,
        'cost_by_task': {'old-task': 30},
        'cost_items': {
            'old-task': {'task_id': 'old-task', 'engine': 'mock', 'status': 'actual',
                         'usd': 30, 'recorded_at': 50},
        },
    }
    app.state.hub.requests['old'] = request
    now[0] = 110.0
    app.state.hub._record_task_cost(
        'old', 'followup-task',
        TaskResult(task_id='followup-task', agent_id='cso', ok=True, cost_usd=2, cost_known=True),
    )

    assert is_office_request(request)
    row = app.state.hub.request_summary(request)
    assert row['office_current'] is True
    assert app.state.hub.snapshot()['data']['requests'][0]['office_current'] is True

    with TestClient(app) as client:
        headers = {'Authorization': f'Bearer {settings.gateway.client_token}'}
        totals = client.get('/api/requests/summary', headers=headers).json()

    assert totals['session'] == {
        'cost_usd': 2.0, 'estimated_usd': 0.0, 'unknown_count': 0,
        'legacy_unknown': False,
    }
    request['followups'][0]['status'] = 'done'
    assert is_past_request(request)
    assert app.state.hub.snapshot()['data']['requests'][0]['office_current'] is False


def test_health_and_cli_status_use_the_same_clean_office_boundary(tmp_path, monkeypatch, capsys):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    settings.gateway.client_token = 'test-token'
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['session_started_at'] == app.state.hub.session_started_at

    rows = [
        {'id':'run', 'status':'running', 'text':'running', 'created_at':10, 'cost_usd':4,
         'step_progress':{'done':0, 'total':1, 'steps':{'s1':'working'}}, 'office_current':True,
         'session_current':True},
        {'id':'review', 'status':'failed', 'text':'review', 'created_at':20, 'cost_usd':3,
         'pi_review_pending':True, 'step_progress':{'done':1, 'total':1, 'steps':{}},
         'office_current':True, 'session_current':True},
        {'id':'past', 'status':'done', 'text':'past', 'created_at':10, 'cost_usd':30,
         'pi_reviewed_at':90, 'step_progress':{'done':1, 'total':1, 'steps':{}},
         'office_current':False, 'session_current':False},
        {'id':'recent', 'status':'done', 'text':'recent', 'created_at':110, 'cost_usd':2,
         'pi_reviewed_at':115, 'step_progress':{'done':1, 'total':1, 'steps':{}},
         'office_current':False, 'session_current':True},
    ]

    def fake_api(_settings, _method, url, **_kwargs):
        if url == '/api/health':
            return {'runners': [], 'session_started_at': 100}
        if url.startswith('/api/requests?status=running'):
            return rows[:1]
        if url.startswith('/api/requests?status=all'):
            return rows
        if url == '/api/requests/summary':
            return {
                'request_count': 4, 'past_count': 2,
                'session': {'cost_usd': 9, 'estimated_usd': 0, 'unknown_count': 0,
                            'legacy_unknown': False},
                'total': {'cost_usd': 39, 'estimated_usd': 0, 'unknown_count': 0,
                          'legacy_unknown': False},
            }
        if url == '/api/approvals':
            return []
        raise AssertionError(url)

    monkeypatch.setattr(Settings, 'load', lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, '_api', fake_api)
    monkeypatch.setattr(cli, '_local_process_diagnostics', lambda _settings: [])
    cli.main(['status'])
    output = capsys.readouterr().out
    assert '이번 세션·진행 중 비용: $9.00' in output
    assert '지난 요청: 2건 · 누적 비용 $39.00' in output
    assert 'PI 검토·확인 대기: 1' in output
