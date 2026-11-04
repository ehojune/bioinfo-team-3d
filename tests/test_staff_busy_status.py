"""Staff activity survives quiet tools and snapshot event-window eviction."""
import json

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.gateway.server import create_app
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex, Engine.gemini, Engine.cli, Engine.antigravity])
async def test_tool_start_and_completion_are_paired(tmp_path, engine):
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    settings = Settings()
    agent = AgentSpec(id="analyst", name="Analyst", role="analysis", engine=engine,
                      builtin_mcp=[])
    if engine == Engine.cli:
        agent.cli = CliSpec(command=["unused"], output="jsonl")
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="Inspect"), agent=agent, workdir=tmp_path,
                     settings=settings, mcp_servers=[], env={}, emit=emit, prompt="Inspect")
    adapter = get_adapter(engine, settings)
    st = RunState()
    if engine == Engine.claude_code:
        start = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b1",
                 "name": "Bash", "input": {"command": "sleep 900"}}]}}
        end = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "content": "ok"}]}}
    elif engine == Engine.codex:
        start = {"type": "item.started", "item": {"type": "command_execution", "id": "b1", "command": "sleep 900"}}
        end = {"type": "item.completed", "item": {"type": "command_execution", "id": "b1", "exit_code": 0}}
    elif engine == Engine.gemini:
        start = {"type": "tool_use", "tool_id": "b1", "tool_name": "run_shell_command", "parameters": {"command": "sleep 900"}}
        end = {"type": "tool_result", "tool_id": "b1", "status": "success"}
    elif engine == Engine.cli:
        start = {"type": "tool", "id": "b1", "name": "Bash", "input": {"command": "sleep 900"}}
        end = {"type": "tool_result", "id": "b1"}
    else:
        start = {"event": "step_update", "step_update": {"step_type": "tool", "step_id": "b1", "state": "ACTIVE",
                 "tool_name": "run_command", "tool_info": {"parameters": {"command": "sleep 900"}}}}
        end = {"event": "step_update", "step_update": {"step_type": "tool", "step_id": "b1", "state": "DONE", "tool_name": "run_command"}}
    await adapter.handle_line(json.dumps(start), st, ctx)
    await adapter.handle_line(json.dumps(end), st, ctx)
    tool = next(data for kind, data in events if kind == "agent.tool")
    assert tool["id"] == "b1"
    assert ("agent.tool_result", {"id": "b1"}) in events


@pytest.mark.asyncio
async def test_snapshot_retains_activity_beyond_recent_events(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running"}
    hub.store.put("task", "t", {"accepted": True, "request_id": "req", "step_id": "s1",
                  "dispatched_at": 100, "payload": {"agent_id": "analyst", "meta": {"title": "Inspect"}}})
    async def send(kind, data, ts=100):
        await hub.on_runner_message("r", {"type": kind, "agent_id": "analyst", "task_id": "t", "request_id": "req", "ts": ts, "data": data})
    await send("agent.status", {"state": "working"})
    await send("agent.tool", {"id": "b1", "name": "Bash", "input": '{"command":"sleep 900"}'})
    for i in range(205):
        await hub.publish({"type": "agent.log", "data": {"text": "unrelated"}})
    task = hub.snapshot()["data"]["running_tasks"][0]
    assert task["activity"]["tools"]["b1"]["at"] == 100
    assert task["title"] == "Inspect"
    await send("agent.log", {"level": "warn", "text": "quiet"}, 200)
    assert hub.snapshot()["data"]["running_tasks"][0]["activity"]["warning"] == "quiet"
    await send("agent.tool_result", {"id": "b1"}, 201)
    activity = hub.snapshot()["data"]["running_tasks"][0]["activity"]
    assert activity["tools"] == {}
    assert not activity.get("warning")
    hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    {"state": "queued"}, {"state": "waiting", "approval": "p"},
    {"state": "waiting", "ask": "q"}, {"state": "hibernating", "jobs": ["j"]},
    {"state": "hibernating", "asks": ["q"]},
])
async def test_snapshot_restores_wait_reason(tmp_path, status):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running"}
    hub.store.put("task", "t", {"accepted": True, "request_id": "req", "step_id": "s1",
                              "payload": {"agent_id": "analyst"}})
    await hub.on_runner_message("r", {"type": "agent.status", "agent_id": "analyst", "task_id": "t", "data": status})
    for i in range(205):
        await hub.publish({"type": "agent.log", "data": {"text": "unrelated"}})
    activity = hub.snapshot()["data"]["running_tasks"][0]["activity"]
    for key, value in status.items():
        assert activity[key] == value
    hub.store.close()


def test_hibernating_snapshot_uses_result_not_last_tool(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running"}
    hub.store.put("task", "t", {"accepted": True, "completed": True, "request_id": "req", "step_id": "s1",
                              "payload": {"agent_id": "analyst"},
                              "activity": {"state": "working", "tools": {"old": {"name": "Bash"}}},
                              "result": {"ok": True, "pending_jobs": ["j"]}})
    activity = hub.snapshot()["data"]["running_tasks"][0]["activity"]
    assert activity == {"state": "hibernating", "jobs": ["j"], "asks": []}
    hub.store.close()


@pytest.mark.parametrize("accepted", [False, True])
def test_staff_snapshot_includes_queue_and_followup_on_finished_request(tmp_path, accepted):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "done"}
    hub.store.put("task", "followup", {"accepted": accepted, "request_id": "req", "kind": "followup",
                                     "payload": {"agent_id": "analyst"}})
    snapshot = hub.snapshot()["data"]
    assert snapshot["running_tasks"] == []
    task = snapshot["staff_tasks"][0]
    assert task["id"] == "followup"
    assert task["state"] == ("running" if accepted else "queued")
    hub.store.close()


@pytest.mark.asyncio
async def test_claude_long_input_has_readable_summary_before_truncation(tmp_path):
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    settings = Settings()
    agent = AgentSpec(id="analyst", name="Analyst", role="analysis", engine=Engine.claude_code)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="Inspect"), agent=agent, workdir=tmp_path,
                     settings=settings, mcp_servers=[], env={}, emit=emit, prompt="Inspect")
    await get_adapter(agent.engine, settings).handle_line(json.dumps({"type": "assistant", "message": {
        "content": [{"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "python analysis.py " + "x" * 500}}]}}), RunState(), ctx)
    data = next(data for kind, data in events if kind == "agent.tool")
    assert data["summary"].startswith("python analysis.py ")
    assert data["input_truncated"] is True
