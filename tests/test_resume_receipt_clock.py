"""Resume fixture timing with fake state/transport only; never construct a Hub."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from labhq.gateway.server import Hub
from tests.test_recruit_before_plan import ResumePollingClock


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["reconnect", "timeout", "cancel"])
async def test_resume_fixture_controls_deadline_without_changing_real_loop(outcome):
    class FakeHub:
        resume_when_ready = Hub.resume_when_ready

    hub = FakeHub()
    hub.s = SimpleNamespace(gateway=SimpleNamespace(resume_wait_s=0.2))
    hub.requests = {"r": {"status": "interrupted"}}
    hub.agent_runner = {"hire": "runner"}
    hub.runners = {}
    hub.resume_agents = lambda rid: {"hire"}
    hub.publish = AsyncMock()
    hub.orchestrator = SimpleNamespace(run_request=AsyncMock())
    hub.recovery_started, hub.recovery_steps = {}, set()

    async def status(rid, value):
        hub.requests[rid]["status"] = value

    hub._set_status = status
    clock = ResumePollingClock()
    controlled = clock.bind(hub)
    assert controlled.__func__.__code__ is Hub.resume_when_ready.__code__
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(controlled("r"))
    try:
        await asyncio.wait_for(clock.polled.wait(), 2)
        assert asyncio.get_running_loop() is loop
        assert hub.requests["r"]["status"] == "waiting_for_runner" and not task.done()
        assert clock.now == 0
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            hub.orchestrator.run_request.assert_not_awaited()
            assert clock.now == 0
        else:
            if outcome == "reconnect":
                hub.runners["runner"] = object()
            clock.release.set()
            await asyncio.wait_for(task, 2)
            if outcome == "reconnect":
                assert 0 < clock.now < hub.s.gateway.resume_wait_s
                assert hub.requests["r"]["status"] == "running"
                hub.orchestrator.run_request.assert_awaited_once_with("r", resume=True)
                assert not hub.recovery_started and not hub.recovery_steps
            else:
                assert clock.now == hub.s.gateway.resume_wait_s
                assert hub.requests["r"]["status"] == "interrupted"
                hub.orchestrator.run_request.assert_not_awaited()
                assert hub.publish.await_args.args[0]["type"] == "request.resume_timeout"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
