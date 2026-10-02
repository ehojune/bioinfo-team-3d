import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labhq import quota as quota_module
from labhq.gateway.server import Hub, create_app
from labhq.models import AgentSpec, AskRequest, Engine, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator, failure_kind
from labhq.quota import parse_quota_wait, quota_reset_instant, received_quota_wait
from labhq.runner.daemon import Runner
from labhq.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures" / "quota"
KST = timezone(timedelta(hours=9))


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


def test_runner_resolves_the_reset_in_its_zone_and_the_gateway_trusts_the_instant(monkeypatch):
    """#302 P1: a KST runner's 'resets 5:50pm' is 17:50 KST even on a UTC gateway, not 9 hours later."""
    text = (FIXTURES / "claude.txt").read_text(encoding="utf-8")
    now = datetime(2026, 10, 2, 12, 0, tzinfo=KST).timestamp()
    instant = quota_reset_instant("claude_code", text, now=now, tz=KST)
    assert instant == datetime(2026, 10, 2, 17, 50, tzinfo=KST).timestamp()
    assert parse_quota_wait("claude_code", text, now=now, tz=timezone.utc).resume_at - instant == 9 * 3600

    monkeypatch.setattr(quota_module, "local_zone", lambda: timezone.utc)  # the gateway's zone
    trusted = received_quota_wait("claude_code", text, instant, now=now, default_wait_s=3600)
    assert trusted.resume_at == instant and trusted.parsed
    assert failure_kind(TaskResult(task_id="t", agent_id="a", ok=False, error="limit",
                                   quota_reset_at=instant)) == "quota"
    # A runner that sent no instant: the clock time has no zone the gateway knows, so the wait is capped.
    fallback = received_quota_wait("claude_code", text, None, now=now, default_wait_s=3600)
    assert fallback.resume_at == now + 3600 and not fallback.parsed
    relative = (FIXTURES / "antigravity.txt").read_text(encoding="utf-8")
    assert received_quota_wait("antigravity", relative, None, now=now).resume_at == now + 2 * 3600 + 17 * 60
    assert received_quota_wait("codex", "ordinary failure", None, now=now) is None


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="switching the OS zone needs time.tzset")
def test_runner_reads_a_reset_past_a_dst_switch_by_that_dates_rules(monkeypatch):
    """#302: 2:53 AM on Nov 2 in New York is EST (07:53 UTC), though the runner reads it during EDT."""
    text = "You've hit your usage limit. Try again at Nov 2nd, 2026 2:53 AM."
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        now = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc).timestamp()  # noon EDT
        assert quota_reset_instant("codex", text, now=now) == datetime(2026, 11, 2, 7, 53,
                                                                        tzinfo=timezone.utc).timestamp()
    finally:
        monkeypatch.undo()
        time.tzset()


@pytest.mark.asyncio
async def test_runner_sends_the_reset_instant_in_its_own_zone(tmp_path, monkeypatch):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    text = (FIXTURES / "codex.txt").read_text(encoding="utf-8")

    class Adapter:
        async def run(self, ctx):
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=False, error=text)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    monkeypatch.setattr(quota_module, "local_zone", lambda: KST)  # the runner's zone
    result = await runner.run_task(Task(id="t-quota", request_id="r", agent_id="worker", prompt="work",
                                        meta={"kind": "step", "step_id": "A"}))
    expected = datetime(2026, 10, 8, 2, 53, tzinfo=KST).timestamp()
    assert result.quota_reset_at == expected
    sent = [e for e in runner.store.pending() if e["type"] == "task.result"]
    assert sent[-1]["data"]["quota_reset_at"] == expected
    assert "quota_reset_at" not in TaskResult(task_id="t", agent_id="a", ok=True).model_dump(mode="json")


@pytest.mark.asyncio
async def test_gateway_waits_until_the_runner_instant_not_its_own_reading(tmp_path, monkeypatch):
    hub = _hub(tmp_path, max_wait=1000)
    monkeypatch.setattr(quota_module, "local_zone", lambda: timezone.utc)
    instant = time.time() + 0.05
    waits, calls = [], []
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_quota_wait":
            waits.append(event["data"]["resume_at"])

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:  # the text alone would be read as 5:50pm in the gateway's zone
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, quota_reset_at=instant,
                              error="You've hit your usage limit · resets 5:50pm")
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    hub.publish = publish
    hub.dispatch = dispatch
    result = await asyncio.wait_for(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"})), 2)
    assert result.ok and len(calls) == 2 and waits == [instant]
    assert time.time() >= instant


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
async def test_a_wake_turn_that_hits_the_quota_parks_and_resumes(tmp_path):
    """#302: a step that hibernated on an HPC job and meets the quota on waking resumes after the reset."""
    hub = _hub(tmp_path)
    workdir = str(tmp_path / "work")
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="submitted", cost_usd=0.0,
                              pending_jobs=["j1"], session_id="s1", workdir=workdir)
        if len(calls) == 2:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, session_id="s1", workdir=workdir, cost_usd=0.0,
                              error="You've hit your usage limit; your quota will reset after 0.01s")
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done", session_id="s1",
                          cost_usd=0.0, workdir=workdir)

    async def wait_jobs(_task_id):
        return {"jobs": [{"job_id": "j1", "name": "align", "state": "completed", "exit_status": 0}]}

    hub.dispatch = dispatch
    hub.wait_jobs = wait_jobs
    result = await asyncio.wait_for(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"})), 1)
    assert result.ok and result.text == "done" and len(calls) == 3
    assert calls[2].meta["parent_task"] == calls[1].id and calls[2].resume_session_id == "s1"
    assert [e["type"] for e in hub.events if "quota" in e["type"]] == [
        "request.step_quota_wait", "request.step_quota_resumed"]


@pytest.mark.asyncio
async def test_budget_is_checked_again_when_a_quota_hold_releases(tmp_path):
    """#302: a budget denied while the step waited on the hold stops the dispatch it was waiting for."""
    hub = _hub(tmp_path, max_wait=1000)
    hub.requests["limited"] = {"id": "limited", "status": "waiting_quota", "quota_waits": {
        "A": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999, "reason": "limit"}}}
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
    orchestrator = Orchestrator(hub)
    running = asyncio.create_task(orchestrator.run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "B"})))
    await asyncio.wait_for(parked.wait(), 1)
    orchestrator.budget_denials["r"] = "budget exceeded; approval denied"
    await hub.force_quota_resume("limited", "A")
    with pytest.raises(BudgetExceeded):
        await asyncio.wait_for(running, 1)
    assert calls == []


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
        "A": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999, "reason": "limit"}})
    hub.save_request("r")
    hub.store.close()

    restored = Hub(hub.s)
    assert restored.requests["r"]["status"] == "waiting_quota"
    assert not any(entry["approval"]["kind"] == "resume" for entry in restored.approvals.values())
    resumed = asyncio.Event()

    async def resume_when_ready(rid):
        # Recovery starts before the reset; the durable wait stays for the step to meet in run_step.
        assert rid == "r" and restored.requests[rid]["quota_waits"]["A"]["engine"] == "codex"
        resumed.set()

    restored.resume_when_ready = resume_when_ready
    await asyncio.wait_for(restored.resume_quota_request("r"), 1)
    assert resumed.is_set()


@pytest.mark.asyncio
async def test_restart_recovery_holds_only_the_step_on_the_limited_engine(tmp_path):
    """#302 P2: an independent step runs at once; only the quota step waits for its engine's reset."""
    hub = _hub(tmp_path, max_wait=1000)
    hub.requests["r"].update(status="waiting_quota", quota_waits={
        "A": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999, "reason": "limit"}})
    hub.save_request("r")
    hub.store.close()

    restored = Hub(hub.s)
    restored.agents = {"worker": {"id": "worker", "engine": "codex"},
                       "helper": {"id": "helper", "engine": "claude_code"}}
    restored.resume_agents = lambda _rid: set()
    calls, outcome = [], {}
    independent_done = asyncio.Event()

    async def dispatch(task):
        calls.append(task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    async def run_request(rid, resume=False):
        assert resume
        held = asyncio.create_task(restored.orchestrator.run_step(
            Task(agent_id="worker", request_id=rid, prompt="a", meta={"kind": "step", "step_id": "A"})))
        outcome["B"] = await restored.orchestrator.run_step(
            Task(agent_id="helper", request_id=rid, prompt="b", meta={"kind": "step", "step_id": "B"}))
        independent_done.set()
        outcome["A"] = await held

    restored.dispatch = dispatch
    restored.orchestrator.run_request = run_request
    recovery = asyncio.create_task(restored.resume_quota_request("r"))
    await asyncio.wait_for(independent_done.wait(), 1)
    assert outcome["B"].ok and calls == ["B"]
    assert restored.requests["r"]["status"] == "waiting_quota"
    assert restored.request_summary(restored.requests["r"])["step_progress"]["steps"]["A"] == "waiting_quota"
    await restored.force_quota_resume("r", "A")
    await asyncio.wait_for(recovery, 1)
    assert outcome["A"].ok and calls == ["B", "A"]
    assert "quota_waits" not in restored.requests["r"]


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


def test_a_yearless_feb_29_reset_after_the_leap_day_uses_the_default_wait():
    # #302 review: the next year has no Feb 29; that must not crash the runner's quota handling.
    now = datetime(2028, 3, 1, 12, 0).astimezone().timestamp()
    parsed = parse_quota_wait("codex", "You've hit your usage limit. Your limit resets Feb 29 9am",
                              now=now, default_wait_s=900)
    assert parsed and not parsed.parsed and parsed.resume_at == now + 900


@pytest.mark.asyncio
async def test_a_followup_quota_wait_keeps_the_finished_request_status(tmp_path):
    # #302 review: a follow-up on a done request parks and resumes without reopening it.
    hub = _hub(tmp_path)
    hub.requests["r"]["status"] = "done"
    parked = asyncio.Event()
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_quota_wait":
            parked.set()

    hub.publish = publish
    waiter = asyncio.create_task(hub.wait_quota("r", "followup-1", "codex", resume_at=9999999999,
                                                deadline_at=9999999999, reason="limit"))
    await asyncio.wait_for(parked.wait(), 1)
    assert hub.requests["r"]["status"] == "done"
    await hub.force_quota_resume("r", "followup-1")
    assert await asyncio.wait_for(waiter, 1)
    assert hub.requests["r"]["status"] == "done" and "quota_waits" not in hub.requests["r"]


def test_restart_drops_a_followup_quota_wait_on_a_finished_request(tmp_path):
    # The follow-up is interrupted by the restart; its wait must not hold the engine or rerun the request.
    hub = _hub(tmp_path)
    hub.requests["r"].update(status="done", quota_waits={
        "followup-1": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999,
                       "reason": "limit"}})
    hub.save_request("r")
    hub.store.close()

    restored = Hub(hub.s)
    assert restored.requests["r"]["status"] == "done"
    assert "quota_waits" not in restored.requests["r"]
    assert restored.quota_hold("codex") is None
    restored.store.close()


@pytest.mark.asyncio
async def test_release_survives_a_request_added_while_it_publishes(tmp_path):
    # #302 review: publish() yields; a new POST /api/requests must not break the release loop.
    hub = _hub(tmp_path)
    for rid in ("a", "b"):
        hub.requests[rid] = {"id": rid, "status": "waiting_quota", "quota_waits": {
            "S": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999, "reason": "limit"}}}
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        if event["type"] == "request.step_quota_resumed":
            hub.requests[f"new-{len(hub.requests)}"] = {"id": "new", "status": "running"}
        await original_publish(event, *args, **kwargs)

    hub.publish = publish
    released = await hub.release_quota("codex", manual=True)
    assert set(released) == {("a", "S"), ("b", "S")}
