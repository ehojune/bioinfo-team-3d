import httpx
import pytest
from labhq.runner.approvals import Broker
from labhq.runner.lookup_admission import LookupAdmission


@pytest.mark.asyncio
async def test_body_cannot_override_issued_stage(tmp_path):
    async def noop(*args):
        pass
    ledger = LookupAdmission(tmp_path / 'broker.db', 4, precedent_share=0.5)
    broker = Broker(0, noop, noop, noop, lookup_admission=ledger)
    token = broker.issue_task_token('t', 'a', 'r', stage='precedent')
    transport = httpx.ASGITransport(app=broker.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        for body in ({'tool': 'search', 'host': 'example.org', 'stage': 'plan'},
                     {'tool': 'search', 'host': 'example.org'},
                     {'tool': 'search', 'host': 'example.org', 'stage': None}):
            response = await client.post('/lookup/admit', json=body, headers={'x-labhq-token': token})
            assert response.status_code == 200
        assert response.json()['reason'] == 'stage share exhausted'


@pytest.mark.asyncio
@pytest.mark.parametrize('share', [0.25, None])
async def test_runner_supplies_settings_and_issues_stage(tmp_path, monkeypatch, share):
    from labhq.settings import Settings
    from tests import test_observed_outputs as helpers
    settings = Settings()
    settings.annot.request_lookup_limit = 8
    settings.annot.precedent_lookup_share = share
    monkeypatch.setattr(helpers, 'Settings', lambda: settings)
    runner = helpers._runner(tmp_path, monkeypatch, lambda _: None)
    assert runner.broker.lookup_admission.precedent_share == share
    issue = runner.broker.issue_task_token
    identities = []
    def capture(*args, **kwargs):
        token = issue(*args, **kwargs)
        identities.append(runner.broker.identities[token].copy())
        return token
    monkeypatch.setattr(runner.broker, 'issue_task_token', capture)
    result = await runner.run_task(helpers._task(kind='precedent'))
    assert result.ok
    assert identities[0]['stage'] == 'precedent'
