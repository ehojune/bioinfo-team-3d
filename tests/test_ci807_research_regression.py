"""CI #807 regressions with fake Hub/dispatch only; no gateway or native workers."""
import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from tests.test_research_protocol import MiniHub
from tests.test_research_cp2 import _settings


@pytest.fixture(autouse=True)
def mock_native_boundaries(monkeypatch):
    from labhq.integrations import rounds
    monkeypatch.setattr(rounds, '_git_commit', lambda: 'a' * 40)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    def denied(*args, **kwargs):
        pytest.fail('mock regression must not start a native worker')
    monkeypatch.setattr(threading.Thread, 'start', denied)


@pytest.mark.asyncio
@pytest.mark.parametrize('rid', ['r', '', 'other', None])
@pytest.mark.parametrize('kind', ['consult', 'wake', 'plan', 'step'])
async def test_unregistered_consult_wake_and_nonresearch_tasks_keep_dispatch_contract(rid, kind):
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text='mock answer')
    hub = MiniHub(_settings(), reply)
    hub.requests.clear()
    orch = Orchestrator(hub)
    orch._verify_research_reuse = AsyncMock(side_effect=AssertionError('not a research continuation'))
    result = await orch.run_step(Task(agent_id='worker', request_id=rid, prompt='fixture', meta={'kind': kind}))
    assert result.ok and result.text == 'mock answer'
    assert len(hub.calls) == 1 and hub.calls[0].request_id == rid
    orch._verify_research_reuse.assert_not_awaited()


@pytest.mark.asyncio
async def test_consult_on_a_research_request_does_not_require_its_pending_cp1_snapshot():
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text='consult answer')
    hub = MiniHub(_settings(), reply)
    hub.requests['r']['research_contract'] = {'plan_sha256': 'a' * 64,
        'continuation': {'plan_sha256': 'a' * 64, 'verified': False}}
    orch = Orchestrator(hub)
    orch._verify_research_reuse = AsyncMock(side_effect=AssertionError('consult is not research execution'))
    result = await orch.run_step(Task(agent_id='worker', request_id='r', prompt='fixture', meta={'kind': 'consult'}))
    assert result.ok and len(hub.calls) == 1
    orch._verify_research_reuse.assert_not_awaited()
