"""Steer, pause and halt a running staff member (#59 ①): hook contract, runner control file, gateway API."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.adapters.codex import CodexAdapter
from labhq.gateway.server import Hub, create_app
from labhq.hooks import control as hook
from labhq.models import AgentSpec, Engine, RunnerUnavailable, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, failure_kind
from labhq.runner.daemon import Runner
from labhq.settings import Settings

REPO = Path(__file__).resolve().parents[1]
# The PostToolUse input example of https://code.claude.com/docs/en/hooks#posttooluse-input
POST_PAYLOAD = {"session_id": "abc123", "transcript_path": "/tmp/t.jsonl", "cwd": "/work", "permission_mode": "default",
                "hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "/path/to/file.txt", "content": "file content"},
                "tool_response": {"filePath": "/path/to/file.txt", "type": "create"},
                "tool_use_id": "toolu_01ABC123", "duration_ms": 12}


def _control(tmp_path, **changes) -> Path:
    path = tmp_path / "control" / "task-1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = hook.new_state("task-1")
    state.update(changes)
    hook.write_state(path, state)
    return path


def _steer(path, *texts):
    for text in texts:
        hook.update_state(path, lambda state, text=text: hook.add_steer(state, text))


# ---------------- hook contract ----------------
def test_a_steer_reaches_claude_once_as_additional_context(tmp_path):
    path = _control(tmp_path)
    _steer(path, "표본 3번은 빼고 다시 보세요")
    out = hook.post_tool_use(path)
    specific = out["hookSpecificOutput"]
    assert specific["hookEventName"] == "PostToolUse"
    assert "표본 3번은 빼고 다시 보세요" in specific["additionalContext"]
    assert specific["additionalContext"].startswith("[PI 방향 수정]")
    assert hook.post_tool_use(path) is None  # delivered once, then gone from the file
    state = hook.read_state(path)
    assert state["steers"] == [] and state["delivered"] == 1
    _steer(path, "실패한 도구 뒤에도")
    failed = hook.post_tool_use(path, "PostToolUseFailure")["hookSpecificOutput"]
    assert failed["hookEventName"] == "PostToolUseFailure" and "실패한 도구 뒤에도" in failed["additionalContext"]


def test_the_steer_queue_keeps_the_newest_twenty_and_says_how_many_it_dropped(tmp_path):
    path = _control(tmp_path)
    _steer(path, *(f"msg-{n}" for n in range(25)))
    state = hook.read_state(path)
    assert [s["text"] for s in state["steers"]] == [f"msg-{n}" for n in range(5, 25)]
    assert state["dropped"] == 5
    text = hook.post_tool_use(path)["hookSpecificOutput"]["additionalContext"]
    assert "msg-5" in text and "msg-24" in text and "msg-4\n" not in text and "5개" in text
    assert hook.post_tool_use(path) is None

    # Long messages go over several tool calls, each under Claude's 10,000-character cap, each exactly once.
    _steer(path, *(f"long-{n}-" + "x" * 1990 for n in range(20)))
    seen = []
    while (out := hook.post_tool_use(path)) is not None:
        context = out["hookSpecificOutput"]["additionalContext"]
        assert len(context) <= 10_000
        seen += [line.split("-")[2] for line in context.splitlines() if line.startswith("- long-")]
    assert seen == [str(n) for n in range(20)]


def test_pause_holds_the_call_then_denies_it_with_the_pi_reason(tmp_path):
    path = _control(tmp_path, paused=True)
    clock = iter(range(1000))
    out = hook.pre_tool_use(path, hold_s=3, sleep=lambda _s: None, clock=lambda: next(clock))
    specific = out["hookSpecificOutput"]
    assert specific == {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                        "permissionDecisionReason": hook.PAUSE_REASON}
    assert "PI가 일시 정지함" in hook.PAUSE_REASON


def test_resume_during_the_hold_lets_the_call_through(tmp_path):
    path = _control(tmp_path, paused=True)

    def resume(_seconds):
        hook.update_state(path, lambda state: hook.apply_control(state, "resume"))

    assert hook.pre_tool_use(path, hold_s=60, sleep=resume) is None
    assert hook.pre_tool_use(_control(tmp_path)) is None  # nothing paused: no decision at all


def test_halt_stops_claude_before_and_after_a_tool_and_keeps_queued_steers(tmp_path):
    path = _control(tmp_path)
    _steer(path, "다음 turn에 전달")
    hook.update_state(path, lambda state: hook.apply_control(state, "halt"))
    assert hook.post_tool_use(path) == {"continue": False, "stopReason": hook.HALT_REASON}
    assert hook.pre_tool_use(path) == {"continue": False, "stopReason": hook.HALT_REASON}
    state = hook.read_state(path)
    assert state["halted_at"] and [s["text"] for s in state["steers"]] == ["다음 turn에 전달"]


def test_a_control_seen_before_is_not_applied_twice():
    state = hook.new_state("t")
    assert hook.apply_control(state, "steer", "한 번만", seq=3)
    assert not hook.apply_control(state, "steer", "한 번만", seq=3)  # the gateway resent it after a reconnect
    # A lower number that failed earlier still applies once (PR #532 review), but not a toggle older than the last one.
    assert hook.apply_control(state, "pause", seq=2)
    assert not hook.apply_control(state, "pause", seq=2)
    assert not hook.apply_control(state, "resume", seq=1)
    assert [s["text"] for s in state["steers"]] == ["한 번만"] and state["paused"]


def _hook_process(event, env, payload=POST_PAYLOAD):
    return subprocess.run([sys.executable, "-m", "labhq.hooks.control", event], input=json.dumps(payload).encode(),
                          capture_output=True, timeout=60, cwd=REPO,
                          env={**os.environ, "PYTHONPATH": str(REPO), **env})


def test_the_hook_command_speaks_the_documented_json(tmp_path):
    path = _control(tmp_path)
    _steer(path, "한국어 지시")
    done = _hook_process("post", {hook.CONTROL_ENV: str(path)})
    assert done.returncode == 0 and done.stdout.isascii()  # any console code page reads it
    assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"].endswith("- 한국어 지시")
    hook.update_state(path, lambda state: hook.apply_control(state, "halt"))
    done = _hook_process("pre", {hook.CONTROL_ENV: str(path)}, {**POST_PAYLOAD, "hook_event_name": "PreToolUse"})
    assert json.loads(done.stdout) == {"continue": False, "stopReason": hook.HALT_REASON}
    # No control file, a broken one, or no variable: exit 0 and say nothing, so the tool runs as before.
    env_free = {key: value for key, value in os.environ.items() if key != hook.CONTROL_ENV}
    for env in ({hook.CONTROL_ENV: str(tmp_path / "missing.json")}, {}):
        done = subprocess.run([sys.executable, "-m", "labhq.hooks.control", "pre"], input=b"{}", capture_output=True,
                              timeout=60, cwd=REPO, env={**env_free, "PYTHONPATH": str(REPO), **env})
        assert done.returncode == 0 and done.stdout == b""
    path.write_text("{broken", encoding="utf-8")
    done = _hook_process("post", {hook.CONTROL_ENV: str(path)})
    assert done.returncode == 0 and done.stdout == b""


def test_control_hooks_cover_every_tool_next_to_existing_hooks():
    from labhq.hooks.tool_use import add_claude_hook

    settings = hook.add_control_hooks(add_claude_hook({"hooks": {"Stop": [{"hooks": []}]}}))
    hooks = settings["hooks"]
    assert hooks["Stop"] == [{"hooks": []}]
    assert [h.get("matcher") for h in hooks["PostToolUse"]] == ["Write|Edit|Bash|PowerShell", None]
    for event, name in (("PreToolUse", "pre"), ("PostToolUse", "post"), ("PostToolUseFailure", "failure")):
        [entry] = [h for h in hooks[event] if "labhq.hooks.control" in h["hooks"][0]["command"]]
        assert "matcher" not in entry and entry["hooks"][0]["command"].endswith(f"labhq.hooks.control {name}")


# ---------------- runner ----------------
def _runner(tmp_path, monkeypatch, run, engine=Engine.claude_code) -> Runner:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            return await run(ctx)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    return runner


def _task(tid="task-1", session=None) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="표를 만드세요", resume_session_id=session,
                meta={"kind": "step", "step_id": "s1"})


def _control_msg(tid, action, seq, text=None):
    return {"type": "task.control", "id": f"ctl-{seq}", "seq": seq, "task_id": tid, "action": action,
            "agent_id": "worker", **({"text": text} if text else {})}


def _sent(runner, kind):
    return [event for event in runner.store.pending() if event["type"] == kind]


@pytest.mark.asyncio
async def test_a_hooked_claude_run_reads_steer_from_a_runner_file_outside_its_workspace(tmp_path, monkeypatch):
    seen = {}

    async def run(ctx):
        path = Path(ctx.env[hook.CONTROL_ENV])
        seen.update(path=path, workdir=Path(ctx.workdir), hooks=ctx.claude_settings["hooks"])
        await runner._on_message(_control_msg("task-1", "steer", 1, "범위를 chr1로 좁히세요"))
        await runner._on_message(_control_msg("task-1", "steer", 1, "범위를 chr1로 좁히세요"))  # a resend
        seen["delivered"] = hook.post_tool_use(path)
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="s1")

    runner = _runner(tmp_path, monkeypatch, run)
    result = await runner.run_task(_task())

    assert result.ok
    path, workdir = seen["path"], seen["workdir"]
    assert path.parent == tmp_path / "state_dir" / "control"
    assert not path.resolve().is_relative_to(workdir.resolve())  # no staff write root holds it
    assert {"PreToolUse", "PostToolUse", "PostToolUseFailure"} <= set(seen["hooks"])
    context = seen["delivered"]["hookSpecificOutput"]["additionalContext"]
    assert context.count("범위를 chr1로 좁히세요") == 1
    assert not path.exists()  # closed with the task
    controls = [event["data"] for event in _sent(runner, "agent.control")]
    assert controls[0]["mode"] == "hook" and controls[0]["steers_pending"] == 1 and controls[0]["applied"]
    assert controls[1]["applied"] is False
    assert [ack["id"] for ack in _sent(runner, "control.ack")] == ["ctl-1", "ctl-1"]
    assert runner.store.get("control_carry", "s1") is None  # nothing left for the next turn


@pytest.mark.asyncio
async def test_codex_gets_a_steer_at_the_start_of_the_next_resume_prompt(tmp_path, monkeypatch):
    prompts = []

    async def run(ctx):
        assert hook.CONTROL_ENV not in ctx.env and "hooks" not in ctx.claude_settings  # Codex runs no hooks
        prompts.append(CodexAdapter(ctx.settings).compose_prompt(ctx))
        if ctx.task.id == "turn-1":
            await runner._on_message(_control_msg("turn-1", "steer", 1, "QC 기준을 0.9로 올리세요"))
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="thr-1")

    runner = _runner(tmp_path, monkeypatch, run, engine=Engine.codex)
    await runner.run_task(_task("turn-1"))
    assert "QC 기준" not in prompts[0]
    assert runner.store.get("control_carry", "thr-1")["steers"][0]["text"] == "QC 기준을 0.9로 올리세요"
    note = [e["data"]["text"] for e in _sent(runner, "agent.log") if "방향 수정" in e["data"].get("text", "")]
    assert note and "다음 turn" in note[0]

    await runner.run_task(_task("turn-2", session="thr-1"))
    task_block = prompts[1].split("</task>")[0]
    assert "[PI 방향 수정]" in task_block and "QC 기준을 0.9로 올리세요" in task_block
    assert task_block.index("QC 기준") < task_block.index("표를 만드세요")
    assert runner.store.get("control_carry", "thr-1") is None  # delivered once

    # A steer that arrives after its turn ended still reaches the session's next turn.
    await runner._on_message(_control_msg("turn-2", "steer", 2, "늦게 온 지시"))
    await runner.run_task(_task("turn-3", session="thr-1"))
    assert "늦게 온 지시" in prompts[2]


@pytest.mark.asyncio
async def test_a_paused_session_does_not_start_its_next_turn_until_resumed(tmp_path, monkeypatch):
    started = []

    async def run(ctx):
        started.append(ctx.task.id)
        if ctx.task.id == "turn-1":
            await runner._on_message(_control_msg("turn-1", "pause", 1))
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="thr-1")

    runner = _runner(tmp_path, monkeypatch, run, engine=Engine.codex)
    await runner.run_task(_task("turn-1"))
    second = asyncio.create_task(runner.run_task(_task("turn-2", session="thr-1")))
    await asyncio.sleep(0.2)
    assert started == ["turn-1"] and not second.done()
    held = [e["data"] for e in _sent(runner, "agent.control") if e["task_id"] == "turn-2"]
    assert held == [{"paused": True, "halt": False, "halted": False, "held": True, "mode": "next_turn",
                     "action": "pause", "applied": True}]
    await runner._on_message(_control_msg("turn-2", "resume", 2))
    result = await asyncio.wait_for(second, 5)
    assert result.ok and started == ["turn-1", "turn-2"]


@pytest.mark.asyncio
async def test_halt_through_the_hook_ends_the_run_as_halted_with_its_session(tmp_path, monkeypatch):
    async def run(ctx):
        await runner._on_message(_control_msg(ctx.task.id, "halt", 1))
        assert ctx.halt.is_set() and ctx.halt_grace_s > 0
        assert hook.post_tool_use(ctx.env[hook.CONTROL_ENV]) == {"continue": False, "stopReason": hook.HALT_REASON}
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="stopped", session_id="s-halt")

    runner = _runner(tmp_path, monkeypatch, run)
    result = await runner.run_task(_task())
    assert not result.ok and result.error_kind == "halted" and result.session_id == "s-halt"
    assert failure_kind(result) == "terminal"  # the orchestrator never replays a halted turn
    statuses = [e["data"] for e in _sent(runner, "agent.status")]
    assert statuses[-1] == {"state": "idle", "halted": True}


@pytest.mark.asyncio
async def test_halt_ends_a_hookless_cli_at_once_and_keeps_its_session_id(tmp_path):
    script = tmp_path / "codex_fake.py"
    script.write_text("import sys, time\n"
                      "print('{\"type\": \"thread.started\", \"thread_id\": \"thr-9\"}', flush=True)\n"
                      "time.sleep(60)\n", encoding="utf-8")
    settings = Settings()
    settings.engines.codex.bin = sys.executable
    settings.engines.codex.prefix_args = [str(script)]
    (tmp_path / "codex-home").mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.codex, system_prompt="ROLE")
    halt = asyncio.Event()
    logs = []

    async def emit(kind, data):
        if kind == "agent.status":
            halt.set()  # thread.started arrived: the session id is known
        logs.append((kind, data))

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="do it"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="do it", halt=halt, halt_grace_s=0)
    started = time.monotonic()
    result = await asyncio.wait_for(get_adapter(Engine.codex, settings).run(ctx), 30)
    assert time.monotonic() - started < 25
    assert result.error_kind == "halted" and result.session_id == "thr-9" and not result.ok
    assert any("PI가 멈춤" in data.get("text", "") for kind, data in logs if kind == "agent.log")


# ---------------- gateway ----------------
def _hub(tmp_path) -> Hub:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.orchestrator.step_retry_backoff_s = 0
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True, "step_id": "s1", "dispatched_at": 1.0})
    return hub


def test_control_api_needs_the_pi_token_and_reaches_the_runner(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "pi-token"
    app = create_app(settings)
    hub = app.state.hub
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"}})
    hub.store.put("task", "done", {"request_id": "r", "runner_id": "pc-a", "completed": True, "result": {"ok": True},
                                   "payload": {"agent_id": "worker"}})
    sent = []

    async def send_runner(runner_id, message):
        sent.append((runner_id, message))

    hub.send_runner = send_runner
    pi = {"Authorization": "Bearer pi-token"}
    with TestClient(app) as client:
        for action in ("steer", "pause", "resume", "halt"):
            assert client.post(f"/api/tasks/t1/{action}", json={"text": "x"}).status_code == 401
            assert client.post(f"/api/tasks/t1/{action}", json={"text": "x"},
                               headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert sent == []
        out = client.post("/api/tasks/t1/steer", json={"text": "  chr1만 보세요 "}, headers=pi).json()
        assert out["ok"] and out["sent"]
        assert client.post("/api/tasks/t1/pause", headers=pi).status_code == 200
        assert client.post("/api/tasks/t1/steer", json={"text": "x" * 2001}, headers=pi).status_code == 422
        assert client.post("/api/tasks/t1/steer", json={"text": "   "}, headers=pi).status_code == 422
        assert client.post("/api/tasks/nope/halt", headers=pi).status_code == 404
        assert client.post("/api/tasks/done/halt", headers=pi).status_code == 409  # nothing left to stop
        # An ended task's session can still take a steer or pause for its next turn (the runner keeps it).
        assert client.post("/api/tasks/done/pause", headers=pi).status_code == 200
    messages = [message for _runner, message in sent]
    assert [(m["task_id"], m["action"], m.get("text"), m["seq"]) for m in messages] == [
        ("t1", "steer", "chr1만 보세요", 1), ("t1", "pause", None, 2), ("done", "pause", None, 3)]
    assert all(m["type"] == "task.control" and m["agent_id"] == "worker" for m in messages)
    assert {runner for runner, _message in sent} == {"pc-a"}


@pytest.mark.asyncio
async def test_controls_kept_while_the_runner_is_away_are_resent_in_order_until_acked(tmp_path):
    hub = _hub(tmp_path)
    sent, online = [], {"pc-a": False}

    async def send_runner(runner_id, message):
        if not online[runner_id]:
            raise RunnerUnavailable("offline")
        sent.append(message)

    hub.send_runner = send_runner
    for action, text in (("steer", "첫 지시"), ("pause", None), ("steer", "둘째 지시")):
        assert (await hub.control_task("t1", action, text))["sent"] is False
    online["pc-a"] = True
    await hub.flush_task_controls("pc-b")
    assert sent == []
    await hub.flush_task_controls("pc-a")
    assert [(m["action"], m.get("text")) for m in sent] == [("steer", "첫 지시"), ("pause", None), ("steer", "둘째 지시")]
    await hub.on_runner_message("pc-a", {"type": "control.ack", "id": sent[0]["id"]}, None, None)
    sent.clear()
    await hub.flush_task_controls("pc-a")
    assert [m["action"] for m in sent] == ["pause", "steer"]  # only what the runner has not acknowledged

    # The task ended meanwhile: a kept halt has nothing to stop; the rest still go to the session's next turn.
    online["pc-a"] = False
    await hub.control_task("t1", "halt")
    online["pc-a"] = True
    hub.store.put("task", "t1", {**hub.store.get("task", "t1"), "completed": True, "result": {"ok": True}})
    sent.clear()
    await hub.flush_task_controls("pc-a")
    assert [m["action"] for m in sent] == ["pause", "steer"]
    assert sorted(entry["message"]["action"] for entry in hub.store.all("task_control").values()) == ["pause", "steer"]


@pytest.mark.asyncio
async def test_the_snapshot_keeps_a_tasks_control_state(tmp_path):
    hub = _hub(tmp_path)
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "team"}
    await hub.on_runner_message("pc-a", {"type": "agent.control", "task_id": "t1", "agent_id": "worker",
                                         "data": {"paused": True, "mode": "hook"}}, None, None)
    [task] = hub.snapshot()["data"]["running_tasks"]
    assert task["id"] == "t1" and task["control"]["paused"] is True
    assert Hub(hub.s).task_controls["t1"]["paused"] is True  # kept across a gateway restart


@pytest.mark.asyncio
async def test_a_halted_step_parks_until_the_pi_resumes_it_on_the_same_session(tmp_path):
    hub = _hub(tmp_path)
    hub.agents = {"worker": {"id": "worker", "engine": "claude_code"}}
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "budget_usd": 10, "status": "running"}
    hub.save_request("r")
    calls = []

    async def dispatch(task):
        calls.append(task)
        if len(calls) == 1:
            return TaskResult(task_id=task.id, agent_id="worker", ok=False, error_kind="halted",
                              error="PI가 멈춤", session_id="sess-1", workdir=str(tmp_path / "w"))
        return TaskResult(task_id=task.id, agent_id="worker", ok=True, text="done", session_id="sess-1")

    hub.dispatch = dispatch
    step = asyncio.create_task(Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"})))
    for _ in range(100):
        if hub.halted_waits:
            break
        await asyncio.sleep(0.01)
    halted_id = calls[0].id
    assert len(calls) == 1 and halted_id in hub.halted_waits
    assert hub.requests["r"]["status"] == "waiting_pi"
    hub.store.put("task", halted_id, {"request_id": "r", "runner_id": "pc-a", "accepted": True, "completed": True,
                                      "step_id": "A", "payload": {"agent_id": "worker"},
                                      "result": {"ok": False, "error_kind": "halted"}})
    assert [t["state"] for t in hub.running_tasks() if t["id"] == halted_id] == ["halted"]

    assert (await hub.control_task(halted_id, "resume"))["resumed_halted"] is True
    result = await asyncio.wait_for(step, 5)
    assert result.ok and len(calls) == 2
    assert calls[1].resume_session_id == "sess-1" and calls[1].meta["parent_task"] == halted_id
    assert hub.requests["r"]["status"] == "running" and not hub.halted_waits
    assert [e["type"] for e in hub.events if e["type"] in ("request.step_halted", "request.step_resumed")] == [
        "request.step_halted", "request.step_resumed"]


def test_cli_task_control_posts_the_staff_sheet_call(monkeypatch):
    from labhq import cli

    calls = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: calls.append((method, path, kw)) or {"sent": True})
    settings = Settings()
    assert "방향 수정" in cli.task_control(settings, "steer", "t/1", "chr1만")
    assert "러너가 꺼져" in (monkeypatch.setattr(cli, "_api", lambda *a, **k: {"sent": False})
                             or cli.task_control(settings, "halt", "t1"))
    assert calls == [("POST", "/api/tasks/t%2F1/steer", {"json": {"text": "chr1만"}})]
    with pytest.raises(SystemExit):
        cli.task_control(settings, "steer", "t1", "  ")
