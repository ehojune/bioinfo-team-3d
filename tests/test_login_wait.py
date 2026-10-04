from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from labhq.cli import render, render_snapshot
from labhq.gateway.server import Hub, create_app
from labhq.login import is_login_error, login_command
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, failure_kind
from labhq.settings import Settings


CLAUDE_EXPIRED = "Failed to authenticate: OAuth session expired and could not be refreshed"


def _hub(tmp_path, *, retry=0.02, maximum=1.0):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.orchestrator.step_retry_backoff_s = 0
    settings.orchestrator.login_retry_s = retry
    settings.orchestrator.login_wait_max_s = maximum
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "C:/staff/claude"}
    hub = Hub(settings)
    hub.agents = {"worker": {"id": "worker", "engine": "claude_code"}}
    hub.agent_runner = {"worker": "runner"}
    hub.runner_capabilities = {
        "runner": {"platform": "win32", "environment": {"USERPROFILE": "C:/Users/runner"}}
    }
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "budget_usd": 10,
                         "status": "running"}
    hub.save_request("r")
    return hub


@pytest.mark.parametrize(
    "engine,error",
    [
        ("claude_code", CLAUDE_EXPIRED),
        ("claude_code", "Please run /login"),
        ("claude_code", "Not logged in"),
        ("claude_code", "Invalid API key"),
        ("codex", "Not logged in"),
        ("codex", "Your access token could not be refreshed because your refresh token has expired. "
                  "Please log out and sign in again."),
        ("codex", "ChatGPT login is required"),
    ],
)
def test_known_engine_login_errors_are_classified(engine, error):
    assert is_login_error(engine, error)
    assert failure_kind(TaskResult(task_id="t", agent_id="a", ok=False, error=error), engine) == "login"


@pytest.mark.parametrize(
    "engine,error",
    [
        ("claude_code", "401 from a public data API"),
        ("claude_code", "permission denied"),
        ("codex", "Invalid API key"),
        ("codex", "request timed out"),
        ("mock", CLAUDE_EXPIRED),
    ],
)
def test_unknown_or_ambiguous_errors_are_not_login(engine, error):
    assert not is_login_error(engine, error)


def test_login_commands_use_runner_os_and_expanded_configured_account_paths():
    settings = Settings()
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "${USERPROFILE}/.labhq/claude-staff"}
    settings.engines.codex.env = {"CODEX_HOME": "~/.labhq/codex-staff"}
    windows = {"platform": "win32", "environment": {"USERPROFILE": "C:/Users/runner"}}
    posix = {"platform": "linux", "environment": {"HOME": "/home/runner"}}
    assert login_command("claude_code", settings, windows) == (
        '$env:CLAUDE_CONFIG_DIR="C:\\Users\\runner\\.labhq\\claude-staff"; claude\n/login')
    assert login_command("codex", settings, posix) == (
        "CODEX_HOME='/home/runner/.labhq/codex-staff' codex login")
    assert login_command("codex", settings, windows) == (
        '$env:CODEX_HOME="C:\\Users\\runner\\.labhq\\codex-staff"; codex login')
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "~/.labhq/claude-staff"}
    assert login_command("claude_code", settings, posix) == (
        "CLAUDE_CONFIG_DIR='/home/runner/.labhq/claude-staff' claude\n/login")


@pytest.mark.parametrize("engine,key", [
    ("claude_code", "CLAUDE_CONFIG_DIR"),
    ("codex", "CODEX_HOME"),
])
def test_login_command_does_not_invent_an_unexpanded_path(engine, key):
    settings = Settings()
    getattr(settings.engines, engine).env = {key: "${MISSING}/staff"}
    shown = login_command(engine, settings, {"platform": "linux", "environment": {"HOME": "/home/runner"}})
    assert shown == f"설정의 engines.{engine}.env.{key} 경로로 로그인하세요\n원래 값: ${{MISSING}}/staff"


def test_login_command_does_not_borrow_gateway_environment_for_a_runner():
    settings = Settings()
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "${USERPROFILE}/staff"}
    shown = login_command("claude_code", settings, {"platform": "linux", "environment": {}})
    assert "설정의 engines.claude_code.env.CLAUDE_CONFIG_DIR 경로" in shown
    assert "${USERPROFILE}/staff" in shown


def test_cli_login_guidance_covers_step_and_snapshot_without_repeating(capsys):
    command = "CLAUDE_CONFIG_DIR='/home/runner/.labhq/claude-staff' claude\n/login"
    seen = set()
    render_snapshot({"type": "snapshot", "ts": 1, "data": {"engine_holds": [
        {"engine": "claude_code", "command": command},
    ]}}, seen)
    render({"type": "request.step_login_wait", "ts": 2, "data": {
        "engine": "claude_code", "command": command,
    }}, seen)
    render({"type": "engine.login_wait", "ts": 3, "data": {
        "engine": "claude_code", "command": command,
    }}, seen)
    shown = capsys.readouterr().out
    assert shown.count("claude-staff") == 1
    assert shown.count("다시 로그인한 뒤 자동 재시도 또는 resume") == 1


@pytest.mark.asyncio
async def test_briefing_login_failure_parks_not_finishes_and_notifies_once(tmp_path):
    hub = _hub(tmp_path, retry=100, maximum=200)
    calls = []
    parked = asyncio.Event()
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_login_wait":
            parked.set()

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)

    hub.publish = publish
    hub.dispatch = dispatch
    running = asyncio.create_task(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="brief", meta={"kind": "briefing"})))
    await asyncio.wait_for(parked.wait(), 1)

    assert hub.requests["r"]["status"] == "waiting_login"
    assert "finished_at" not in hub.requests["r"]
    assert len(calls) == 1
    notices = [e for e in hub.events if e["type"] == "engine.login_wait"]
    assert len(notices) == 1
    assert '$env:CLAUDE_CONFIG_DIR="C:\\staff\\claude"; claude' in notices[0]["data"]["command"]
    assert "/login" in notices[0]["data"]["command"]
    waits = [e for e in hub.events if e["type"] == "request.step_login_wait"]
    assert waits[0]["data"]["command"] == notices[0]["data"]["command"]
    assert hub.snapshot()["data"]["engine_holds"] == [{
        "kind": "login", "engine": "claude_code", "command": notices[0]["data"]["command"],
        "request_id": "r", "step_id": "briefing",
    }]

    assert not running.done()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
async def test_login_wait_automatically_retries_the_same_turn(tmp_path):
    hub = _hub(tmp_path)
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) <= 2:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED,
                              workdir=str(tmp_path / "work"))
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done",
                          workdir=str(tmp_path / "work"))

    hub.dispatch = dispatch
    result = await asyncio.wait_for(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="plan", meta={"kind": "plan"})), 1)

    assert result.ok and result.text == "done" and len(calls) == 3
    assert [task.meta["kind"] for task in calls] == ["plan", "plan", "plan"]
    assert calls[1].meta["parent_task"] == calls[0].id
    assert calls[1].meta["workdir"] == str(tmp_path / "work")
    assert len([event for event in hub.events if event["type"] == "engine.login_wait"]) == 1
    assert len([event for event in hub.events if event["type"] == "engine.login_resumed"]) == 1
    assert "login_waits" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_login_retry_preserves_direct_task_budget_schema_and_fields(tmp_path):
    hub = _hub(tmp_path)
    calls = []
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    hub.dispatch = dispatch
    original = Task(agent_id="worker", request_id="r", prompt="direct", context="kept context",
                    output_schema=schema, budget_usd=1.25,
                    meta={"kind": "direct", "custom": "kept"})
    result = await asyncio.wait_for(Orchestrator(hub).run_step(original), 1)

    assert result.ok and len(calls) == 2
    assert calls[1].id != calls[0].id
    assert calls[1].budget_usd == calls[0].budget_usd == 1.25
    assert calls[1].output_schema == calls[0].output_schema == schema
    assert calls[1].context == calls[0].context == "kept context"
    assert calls[1].meta["custom"] == "kept"


@pytest.mark.asyncio
async def test_solo_login_retry_preserves_request_budget_and_schema(tmp_path):
    hub = _hub(tmp_path)
    hub.s.orchestrator.solo_agent = "worker"
    hub.requests["r"].update(
        mode="orchestrate", route="auto", budget_usd=0.75, project_dirs=[], references=[],
        plan={"assumptions": [], "steps": []},
        route_decision={"mode": "solo", "agent_id": "worker"},
    )
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done",
                          outputs=["answer.md"], workdir_id="task_solo", cost_usd=0.0)

    hub.dispatch = dispatch
    assert await asyncio.wait_for(Orchestrator(hub)._run_solo("r", "work", {}), 5)

    assert len(calls) == 2
    assert calls[1].budget_usd == calls[0].budget_usd == 0.75
    assert calls[1].output_schema == calls[0].output_schema is None


@pytest.mark.asyncio
async def test_two_requests_share_one_engine_notice(tmp_path):
    hub = _hub(tmp_path, retry=100, maximum=200)
    hub.requests["other"] = {"id": "other", "status": "running"}
    deadline = time.time() + 200
    first = asyncio.create_task(hub.wait_login("r", "plan", "claude_code",
                                               resume_at=time.time() + 100, deadline_at=deadline,
                                               reason=CLAUDE_EXPIRED))
    await asyncio.sleep(0)
    second = asyncio.create_task(hub.wait_login("other", "review", "claude_code",
                                                resume_at=time.time() + 100, deadline_at=deadline,
                                                reason=CLAUDE_EXPIRED))
    await asyncio.sleep(0)
    assert len([event for event in hub.events if event["type"] == "engine.login_wait"]) == 1
    assert len(hub.engine_login_holds()) == 1
    await hub.release_login("claude_code", manual=True)
    assert await first and await second


@pytest.mark.asyncio
async def test_non_login_failure_remains_terminal(tmp_path):
    hub = _hub(tmp_path)
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error="ordinary command failed")

    hub.dispatch = dispatch
    result = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="plan", meta={"kind": "plan"}))
    assert not result.ok and calls == 1 and "login_waits" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_manual_resume_retries_immediately_and_releases_same_engine(tmp_path):
    hub = _hub(tmp_path, retry=100, maximum=200)
    calls = []
    parked = asyncio.Event()
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "request.step_login_wait" and event["request_id"] == "r":
            parked.set()

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    hub.publish = publish
    hub.dispatch = dispatch
    running = asyncio.create_task(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="review", meta={"kind": "review"})))
    await asyncio.wait_for(parked.wait(), 1)
    hub.requests["other"] = {"id": "other", "status": "waiting_login", "login_waits": {
        "review": {"engine": "claude_code", "resume_at": 9999999999, "deadline_at": 9999999999,
                   "reason": "expired", "waiting_since": time.time()}}}
    released = await hub.force_quota_resume("r", "review")
    assert (await asyncio.wait_for(running, 1)).ok
    assert set(released) == {("r", "review"), ("other", "review")}
    assert "login_waits" not in hub.requests["r"] and "login_waits" not in hub.requests["other"]


@pytest.mark.asyncio
async def test_login_wait_deadline_becomes_terminal_failure_with_reason(tmp_path):
    hub = _hub(tmp_path, retry=1, maximum=0.01)
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)

    hub.dispatch = dispatch
    result = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="report", meta={"kind": "synthesis"}))
    assert not result.ok and result.error_kind == "login_wait_limit"
    assert "login wait exceeded" in result.error and calls == 1
    assert "login_waits" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_login_retry_expiry_ends_notice_and_next_failure_notifies_again(tmp_path):
    hub = _hub(tmp_path, retry=0.05, maximum=0.2)
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)

    hub.dispatch = dispatch
    result = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="report", meta={"kind": "synthesis"}))

    assert not result.ok and result.error_kind == "login_wait_limit" and calls >= 2
    assert "claude_code" not in hub.login_notices
    ended = [event for event in hub.events if event["type"] == "engine.login_resumed"]
    assert len(ended) == 1 and ended[0]["data"]["reason"] == "expired"
    first_notice_count = len([event for event in hub.events if event["type"] == "engine.login_wait"])

    hub.s.orchestrator.login_retry_s = 100
    hub.s.orchestrator.login_wait_max_s = 200
    parked = asyncio.Event()
    original_publish = hub.publish

    async def publish(event, *args, **kwargs):
        await original_publish(event, *args, **kwargs)
        if event["type"] == "engine.login_wait":
            parked.set()

    hub.publish = publish
    again = asyncio.create_task(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="again", meta={"kind": "direct"})))
    await asyncio.wait_for(parked.wait(), 1)
    assert len([event for event in hub.events if event["type"] == "engine.login_wait"]) == first_notice_count + 1
    again.cancel()
    with pytest.raises(asyncio.CancelledError):
        await again


@pytest.mark.asyncio
async def test_login_expiry_keeps_engine_notice_while_another_request_waits(tmp_path):
    hub = _hub(tmp_path, retry=1, maximum=10)
    hub.requests["r"]["login_windows"] = {
        "claude_code": {"started_at": time.time() - 2, "deadline_at": time.time() - 1}}
    hub.requests["other"] = {"id": "other", "status": "waiting_login", "login_waits": {
        "review": {"engine": "claude_code", "resume_at": time.time() + 100,
                   "deadline_at": time.time() + 200, "reason": CLAUDE_EXPIRED,
                   "waiting_since": time.time()}}}
    hub.login_notices.add("claude_code")

    async def dispatch(task):
        return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)

    hub.dispatch = dispatch
    result = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="report", meta={"kind": "synthesis"}))

    assert not result.ok and result.error_kind == "login_wait_limit"
    assert "claude_code" in hub.login_notices
    assert hub.login_hold("claude_code") is not None
    assert not [event for event in hub.events if event["type"] == "engine.login_resumed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["briefing", "precedent", "plan", "replan", "review", "synthesis", "direct", "followup"])
async def test_every_cso_and_direct_turn_kind_waits_for_login(tmp_path, kind):
    hub = _hub(tmp_path)
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error=CLAUDE_EXPIRED)
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    hub.dispatch = dispatch
    result = await asyncio.wait_for(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt=kind, meta={"kind": kind})), 1)
    assert result.ok and [task.meta["kind"] for task in calls] == [kind, kind]


@pytest.mark.asyncio
async def test_restart_keeps_waiting_login_and_recovers_without_approval(tmp_path):
    hub = _hub(tmp_path, retry=100, maximum=200)
    hub.requests["r"].update(status="waiting_login", login_waits={
        "plan": {"engine": "claude_code", "resume_at": 9999999999, "deadline_at": 9999999999,
                 "reason": CLAUDE_EXPIRED, "waiting_since": time.time()}})
    hub.save_request("r")
    hub.store.close()

    restored = Hub(hub.s)
    assert restored.requests["r"]["status"] == "waiting_login"
    assert not any(entry["approval"]["kind"] == "resume" for entry in restored.approvals.values())
    resumed = asyncio.Event()

    async def resume_when_ready(rid):
        assert restored.requests[rid]["login_waits"]["plan"]["engine"] == "claude_code"
        resumed.set()

    restored.resume_when_ready = resume_when_ready
    await asyncio.wait_for(restored.resume_held_request("r"), 1)
    assert resumed.is_set()


@pytest.mark.asyncio
async def test_restart_preserves_the_original_login_deadline(tmp_path):
    hub = _hub(tmp_path, retry=100, maximum=200)
    original_deadline = time.time() + 0.02
    hub.requests["r"].update(status="waiting_login", login_waits={
        "plan": {"engine": "claude_code", "resume_at": time.time() + 100,
                 "deadline_at": original_deadline, "reason": CLAUDE_EXPIRED,
                 "waiting_since": time.time() - 10}})
    hub.save_request("r")
    hub.store.close()
    restored = Hub(hub.s)
    restored.agents = {"worker": {"id": "worker", "engine": "claude_code"}}
    calls = 0

    async def dispatch(task):
        nonlocal calls
        calls += 1
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="unexpected")

    restored.dispatch = dispatch
    result = await asyncio.wait_for(Orchestrator(restored).run_step(
        Task(agent_id="worker", request_id="r", prompt="plan", meta={"kind": "plan"})), 1)
    assert not result.ok and result.error_kind == "login_wait_limit" and calls == 0
    assert time.time() >= original_deadline


def test_existing_resume_endpoint_releases_a_login_hold(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "status": "waiting_login", "login_waits": {
        "plan": {"engine": "codex", "resume_at": 9999999999, "deadline_at": 9999999999,
                 "reason": "Not logged in", "waiting_since": time.time()}}}
    hub.save_request("r")
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        response = client.post("/api/requests/r/steps/plan/resume-quota", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["released"] == [{"request_id": "r", "step_id": "plan"}]
    assert "login_waits" not in hub.requests["r"]
