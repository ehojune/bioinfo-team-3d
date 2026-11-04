"""PR #532 outstanding comments: deterministic control ordering at the real boundaries."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from labhq.adapters.base import RunContext
from labhq.adapters.cli import CliAdapter
from labhq.models import AgentSpec, CliSpec, Engine, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.runner.control import halted_result
from labhq.runner.workspace import portable_input_path
from labhq.settings import Settings
from test_staff_control_scope import _entry, _gateway, _result, _step


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["project", "upstream"])
@pytest.mark.parametrize("rewrite", [False, True])
async def test_4257865735_resume_hashes_the_input_the_cli_reads(tmp_path, monkeypatch, input_kind, rewrite):
    root = tmp_path / input_kind
    folder = root / "outputs" if input_kind == "upstream" else root
    folder.mkdir(parents=True)
    source = folder / "input.txt"
    source.write_text("before pause", encoding="utf8")
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    script = "from pathlib import Path; p = Path(" + repr(str(source)) + "); print(p.read_text())"
    if rewrite:
        script += "; p.write_text('changed by CLI')"
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.cli, builtin_mcp=[],
                      cli=CliSpec(command=[sys.executable, "-c", script]))
    monkeypatch.setattr(runner, "_resolve_agent", lambda _: agent)
    task = _step("t1")
    task.meta["project_dirs"] = [str(root)]
    if input_kind == "upstream":
        task.meta["upstream_steps"] = {"source": str(root)}
    paused = asyncio.Event()
    real_emit = runner.emit

    async def emit(event):
        await real_emit(event)
        if event.type == "agent.log" and event.data.get("text", "").startswith("$ cli"):
            await runner._on_message({"type": "task.control", "task_id": task.id, "action": "pause"})
            paused.set()

    monkeypatch.setattr(runner, "emit", emit)
    running = asyncio.create_task(runner.run_task(task))
    try:
        await asyncio.wait_for(paused.wait(), 10)
        source.write_text("after resume", encoding="utf8")
        await runner._on_message({"type": "task.control", "task_id": task.id, "action": "resume"})
        result = await asyncio.wait_for(running, 10)
        assert result.ok and result.text.strip() == "after resume"
        manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf8"))
        [record] = manifest["runs"][task.id]["input_files"]
        assert record["path"] == ("inputs/source/input.txt" if input_kind == "upstream"
                                  else portable_input_path(source.resolve()))
        assert record["sha256"] == hashlib.sha256(b"after resume").hexdigest()
        assert record.get("changed_during_step", False) is rewrite
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["resume", "halt"])
async def test_4244965036_pause_during_preparation_holds_spawn(tmp_path, monkeypatch, release):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.cli, builtin_mcp=[],
                      cli=CliSpec(command=[sys.executable, "-c", "print('done')"]))
    monkeypatch.setattr(runner, "_resolve_agent", lambda _: agent)
    paused, spawned = asyncio.Event(), asyncio.Event()
    real_emit, real_spawn = runner.emit, asyncio.create_subprocess_exec

    async def emit(event):
        await real_emit(event)
        if event.type == "agent.log" and event.data.get("text", "").startswith("$ cli"):
            await runner._on_message({"type": "task.control", "task_id": "t1", "action": "pause"})
            paused.set()

    async def spawn(*args, **kwargs):
        spawned.set()
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(runner, "emit", emit)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    running = asyncio.create_task(runner.run_task(_step("t1")))
    try:
        await asyncio.wait_for(paused.wait(), 10)
        # If the gate works, spawn cannot complete until the explicit release below.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(spawned.wait(), 0.15)
        await runner._on_message({"type": "task.control", "task_id": "t1", "action": release})
        result = await asyncio.wait_for(running, 10)
        assert spawned.is_set() == (release == "resume")
        assert result.error_kind == ("halted" if release == "halt" else None)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("session", ["session-early", None])
async def test_4244965042_hookless_halt_reports_session_availability(tmp_path, session):
    script = tmp_path / "fake_cli.py"
    event = {"type": "status", "state": "working", "session_id": session}
    script.write_text("import time\nprint(" + repr(json.dumps(event)) + ", flush=True)\n"
                      "print('{\"type\":\"log\",\"text\":\"partial analysis\"}', flush=True)\n"
                      "time.sleep(60)\n", encoding="utf8")
    settings, halt, logs = Settings(), asyncio.Event(), []
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.cli,
                      cli=CliSpec(command=[sys.executable, str(script)], output="jsonl"))

    async def emit(kind, data):
        logs.append(data.get("text", ""))
        if kind == "agent.log" and data.get("text") == "partial analysis":
            halt.set()

    ctx = RunContext(task=_step("t1"), agent=agent, settings=settings, workdir=tmp_path,
                     mcp_servers=[], env={}, prompt="work", emit=emit, halt=halt)
    result = await asyncio.wait_for(CliAdapter(settings).run(ctx), 30)
    assert result.error_kind == "halted" and not result.ok
    assert "partial analysis" in result.text
    if session:
        assert result.session_id == session
    else:
        assert not result.session_id
        assert "같은 세션 재개 불가" in result.error
        assert "같은 세션 재개 불가" in halted_result(result).error
        assert any("같은 세션 재개 불가" in line for line in logs)


@pytest.mark.asyncio
async def test_4244965042_no_session_resume_keeps_previous_result(tmp_path):
    hub, _ = _gateway(tmp_path)
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error_kind="halted",
                              text="partial analysis", structured={"completed": "input QC"})
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done")

    async def resume(*_):
        return True

    hub.dispatch, hub.wait_halted = dispatch, resume
    result = await Orchestrator(hub).run_step(_step("t1"))
    assert result.ok and len(calls) == 2
    assert calls[1].resume_session_id is None
    assert "partial analysis" in calls[1].prompt and "input QC" in calls[1].prompt
    assert "Original instruction:\nwork" in calls[1].prompt


@pytest.mark.asyncio
async def test_4244965047_steer_between_publish_and_frame_is_resent(tmp_path):
    hub, ws = _gateway(tmp_path)
    original = hub.publish

    async def publish(event, **kwargs):
        await original(event, **kwargs)
        if event["type"] == "task.dispatched":
            await hub.control_task("t1", "steer", "new direction")
            await hub.on_runner_message("pc-a", {"type": "control.nack", "task_id": "t1"}, None, None)

    hub.publish = publish
    running = asyncio.create_task(hub.dispatch(_step("t1")))
    try:
        for _ in range(100):
            if any(f["type"] == "task.dispatch" for f in ws.frames):
                break
            await asyncio.sleep(0.01)
        await _result(hub, "t1")
        await running
        index = next(i for i, f in enumerate(ws.frames) if f["type"] == "task.dispatch")
        steers = [f for f in ws.frames[index + 1:] if f.get("action") == "steer"]
        assert len(steers) == 1 and steers[0]["steer"]["text"] == "new direction"
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_4255659855_resume_before_waiter_is_kept(tmp_path):
    hub, _ = _gateway(tmp_path)
    _entry(hub, "t1")
    await _result(hub, "t1", ok=False, error_kind="halted", session_id="S")
    assert "t1" not in hub.halted_waits
    reply = await hub.control_task("t1", "resume")
    assert reply.get("resumed_halted") is True
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="S")
    assert await asyncio.wait_for(hub.wait_halted("r", "A", halted), 1)
    assert "t1" not in hub.halted_waits
