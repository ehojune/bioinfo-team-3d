import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, create_app
from labhq.models import AskRequest, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, failure_kind
from labhq.quota import parse_quota_wait
from labhq.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures" / "quota"


@pytest.mark.parametrize(
    ("engine", "fixture", "expected"),
    [
        ("claude_code", "claude.txt", datetime(2026, 10, 2, 17, 50)),
        ("codex", "codex.txt", datetime(2026, 10, 8, 2, 53)),
        ("antigravity", "antigravity.txt", datetime(2026, 10, 2, 14, 17)),
    ],
)
def test_subscription_quota_fixtures_parse_reset_time(engine, fixture, expected):
    now = datetime(2026, 10, 2, 12, 0).astimezone()
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    parsed = parse_quota_wait(engine, text, now=now.timestamp())
    assert parsed and parsed.parsed
    assert datetime.fromtimestamp(parsed.resume_at).astimezone().replace(tzinfo=None) == expected
    assert failure_kind(TaskResult(task_id="t", agent_id="a", ok=False, error=text)) == "quota"


def test_unreadable_reset_uses_bounded_default_but_burst_limit_does_not():
    now = datetime(2026, 10, 2, 12, 0).astimezone().timestamp()
    parsed = parse_quota_wait("codex", "You've hit your usage limit; try again later",
                              now=now, default_wait_s=900)
    assert parsed and not parsed.parsed and parsed.resume_at == now + 900
    assert parse_quota_wait("claude_code",
                            "Server is temporarily limiting requests (not your usage limit) · Rate limited",
                            now=now) is None


def _hub(tmp_path, *, max_wait=1.0):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.orchestrator.step_retry_backoff_s = 0
    settings.orchestrator.quota_max_wait_s = max_wait
    hub = Hub(settings)
    hub.agents = {"worker": {"id": "worker", "engine": "codex"}}
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "budget_usd": 10,
                         "status": "running"}
    hub.save_request("r")
    return hub


@pytest.mark.asyncio
async def test_waiting_quota_request_routes_parallel_ask(tmp_path):
    hub = _hub(tmp_path)
    hub.requests["r"]["status"] = "waiting_quota"
    routed = asyncio.Event()

    async def answer_ask(_ask, _runner_id):
        routed.set()

    hub.orchestrator.answer_ask = answer_ask
    ask = AskRequest(task_id="parallel", agent_id="worker", request_id="r", to="cso",
                     question="다른 단계의 결과가 필요한가요?", why_blocked="병렬 단계가 진행 중")
    hub.store.put("ask", ask.id, {"state": "pending", "ask": ask.model_dump(mode="json")})
    hub._start_ask(ask, "runner")

    await asyncio.wait_for(routed.wait(), 0.2)
    assert hub.store.get("ask", ask.id)["state"] == "working"


@pytest.mark.asyncio
async def test_quota_parks_then_automatically_resumes_same_session(tmp_path):
    hub = _hub(tmp_path)
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False,
                              error="You've hit your usage limit; your quota will reset after 0.01s",
                              session_id="session-1", workdir=str(tmp_path / "work"))
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done",
                          session_id="session-1", workdir=str(tmp_path / "work"))

    hub.dispatch = dispatch
    result = await asyncio.wait_for(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"})), 1)
    assert result.ok and len(calls) == 2
    assert calls[1].resume_session_id == "session-1"
    assert "quota_waits" not in hub.requests["r"]
    assert [e["type"] for e in hub.events if "quota" in e["type"]] == [
        "request.step_quota_wait", "request.step_quota_resumed"]


@pytest.mark.asyncio
async def test_manual_resume_releases_every_step_on_the_engine(tmp_path):
    hub = _hub(tmp_path)
    hub.requests["other"] = {"id": "other", "status": "waiting_quota", "quota_waits": {
        "B": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999,
              "reason": "limit"}}}
    parked = asyncio.Event()
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_quota_wait":
            parked.set()

    hub.publish = publish
    waiter = asyncio.create_task(hub.wait_quota("r", "A", "codex", resume_at=9999999999,
                                                deadline_at=9999999999, reason="limit"))
    await asyncio.wait_for(parked.wait(), 1)
    assert hub.request_summary(hub.requests["r"])["step_progress"]["steps"]["A"] == "waiting_quota"
    released = await hub.force_quota_resume("r", "A")
    assert await asyncio.wait_for(waiter, 1)
    assert set(released) == {("r", "A"), ("other", "B")}
    assert all("quota_waits" not in hub.requests[rid] for rid in ("r", "other"))


@pytest.mark.asyncio
async def test_another_step_on_the_same_engine_waits_before_dispatch(tmp_path):
    hub = _hub(tmp_path, max_wait=1000)
    hub.requests["limited"] = {"id": "limited", "status": "waiting_quota", "quota_waits": {
        "A": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999,
              "reason": "limit"}}}
    parked = asyncio.Event()
    calls = []
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_quota_wait" and event["request_id"] == "r":
            parked.set()

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    hub.publish = publish
    hub.dispatch = dispatch
    running = asyncio.create_task(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "B"})))
    await asyncio.wait_for(parked.wait(), 1)
    assert calls == [] and hub.requests["r"]["status"] == "waiting_quota"
    await hub.force_quota_resume("limited", "A")
    assert (await asyncio.wait_for(running, 1)).ok and len(calls) == 1


@pytest.mark.asyncio
async def test_quota_beyond_maximum_fails_without_waiting(tmp_path):
    hub = _hub(tmp_path, max_wait=1)
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return TaskResult(task_id=task.id, agent_id="worker", ok=False,
                          error="You've hit your usage limit; your quota will reset after 10s")

    hub.dispatch = dispatch
    result = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"}))
    assert not result.ok and result.error_kind == "quota_wait_limit" and calls == 1
    assert "quota_waits" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_gateway_restart_keeps_wait_and_auto_recovery_path(tmp_path):
    hub = _hub(tmp_path)
    hub.requests["r"].update(status="waiting_quota", quota_waits={
        "A": {"engine": "codex", "resume_at": 0, "deadline_at": 9999999999, "reason": "limit"}})
    hub.save_request("r")
    hub.store.close()

    restored = Hub(hub.s)
    assert restored.requests["r"]["status"] == "waiting_quota"
    assert not any(entry["approval"]["kind"] == "resume" for entry in restored.approvals.values())
    resumed = asyncio.Event()

    async def resume_when_ready(rid):
        assert rid == "r" and "quota_waits" not in restored.requests[rid]
        resumed.set()

    restored.resume_when_ready = resume_when_ready
    await asyncio.wait_for(restored.resume_quota_request("r"), 1)
    assert resumed.is_set()


def test_resume_quota_endpoint_releases_the_real_hold(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "status": "waiting_quota", "quota_waits": {
        "A": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999,
              "reason": "limit"}}}
    hub.save_request("r")
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        response = client.post("/api/requests/r/steps/A/resume-quota", headers=headers, json={})
    assert response.status_code == 200 and response.json()["released"] == [{"request_id": "r", "step_id": "A"}]
    assert "quota_waits" not in hub.requests["r"]
