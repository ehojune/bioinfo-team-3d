"""PR #532 verification round: the gateway owns each step's controls until they are used; the runner applies them only
to tasks it holds and keeps nothing across turns. One test per verified finding class (wf532)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.adapters.codex import CodexAdapter
from labhq.gateway import server as gateway_server
from labhq.gateway.server import Hub
from labhq.hooks import control as hook
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from tests.support.time_proxy import RuntimeProxy


# ---------------- harness ----------------
class FakeWs:
    def __init__(self, up=True):
        self.frames, self.up = [], up

    async def send_text(self, payload):
        if not self.up:
            raise RuntimeError("socket closed")
        self.frames.append(json.loads(payload))

    async def close(self, code=1000):
        self.up = False


def _gateway(tmp_path, ws=None):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    settings.orchestrator.runner_reconnect_timeout_s = 10
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}
    hub.save_request("r")
    ws = ws or FakeWs()
    hub.register_runner("pc-a", ws, [{"id": "worker", "engine": "codex"}], "inc-1", {}, "proc-1")
    return hub, ws


def _step_task(tid, step="A") -> Task:
    return Task(id=tid, request_id="r", agent_id="worker", prompt="work", meta={"kind": "step", "step_id": step})


async def _frame(ws, kind, task_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in ws.frames:
            if frame.get("type") == kind and (frame.get("task_id") or (frame.get("task") or {}).get("id")) == task_id:
                return frame
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind} for {task_id}: {[f.get('type') for f in ws.frames]}")


async def _finish(hub, tid, **data):
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": tid, "request_id": "r"}, None, None)
    await hub.on_runner_message("pc-a", {"type": "task.result", "task_id": tid, "request_id": "r", "data": {
        "task_id": tid, "agent_id": "worker", "ok": True, "text": "done", **data}}, None, None)


def _runner(tmp_path, monkeypatch, run, engine=Engine.codex) -> Runner:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / "runner" / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            started = getattr(ctx, "on_started", None)
            if started:
                started()
            return await run(ctx)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    return runner


def _task(tid, session=None, steers=None) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="표를 만드세요", resume_session_id=session,
                meta={"kind": "step", "step_id": "s1", **({"steers": steers} if steers else {})})


def _msg(tid, action, steer_id=None, text=None):
    return {"type": "task.control", "task_id": tid, "action": action, "agent_id": "worker",
            **({"steer": {"id": steer_id, "text": text}} if steer_id else {})}


def _sent(runner, kind, tid=None):
    return [e for e in runner.store.pending() if e["type"] == kind and (tid is None or e.get("task_id") == tid)]


# ---------------- [0] durability: no time pruning; a steer waits on its step through a long halt ----------------
@pytest.mark.asyncio
async def test_a_steer_waits_on_its_step_through_a_long_halt_park(tmp_path, monkeypatch):
    hub, ws = _gateway(tmp_path)
    first = asyncio.create_task(hub.dispatch(_step_task("t1")))
    await _frame(ws, "task.dispatch", "t1")
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t1", "request_id": "r"}, None, None)
    out = await hub.control_task("t1", "steer", "주말 뒤에 GRCh38로")
    assert out["sent"] is True
    steer_id = (await _frame(ws, "task.control", "t1"))["steer"]["id"]
    await hub.on_runner_message("pc-a", {"type": "task.result", "task_id": "t1", "request_id": "r", "data": {
        "task_id": "t1", "agent_id": "worker", "ok": False, "error_kind": "halted", "session_id": "S",
        "control": {"delivered": [], "undelivered": [{"id": steer_id}], "paused": False}}}, None, None)
    await first
    later = time.time() + 3 * 86400  # past the runner's old 2-day carry limit, within the 7-day halt park
    monkeypatch.setattr(gateway_server, "time", RuntimeProxy(gateway_server.time, time=lambda: later))
    resumed = asyncio.create_task(hub.dispatch(_step_task("t2")))
    frame = await _frame(ws, "task.dispatch", "t2")
    assert [s["text"] for s in frame["task"]["meta"]["steers"]] == ["주말 뒤에 GRCh38로"]
    await _finish(hub, "t2", control={"delivered": [steer_id], "undelivered": [], "paused": False})
    await resumed
    assert hub._step(hub.step_key("r", "A", "step", "t2"))["steers"] == []  # used, so gone


# ---------------- [1]/[7] concurrency: a control racing finish() is NACKed or reported, never lost --------------
@pytest.mark.asyncio
async def test_a_control_that_races_the_end_of_its_task_is_reported_and_nacked_not_lost(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, None)
    runner.controls.start("t1", "worker", hooks=True)
    inside, release = threading.Event(), threading.Event()
    real_apply = hook.apply_control

    def slow_apply(*args, **kwargs):
        inside.set()
        release.wait(5)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(hook, "apply_control", slow_apply)
    applying = asyncio.create_task(runner._on_message(_msg("t1", "steer", "steer-9", "늦게 온 지시")))
    await asyncio.to_thread(inside.wait, 5)
    closing = asyncio.create_task(asyncio.to_thread(runner.controls.finish, "t1"))  # waits for the file lock
    await asyncio.sleep(0.1)
    release.set()
    report = await closing
    await applying
    assert {"id": "steer-9", "text": "늦게 온 지시"} in report["undelivered"]  # finish read it under the lock
    [nack] = _sent(runner, "control.nack")  # and the runner does not claim it holds the task any more
    assert nack["steer_id"] == "steer-9" and nack["reason"] == "unknown task"
    assert not list((tmp_path / "runner" / "state_dir" / "control").glob("*.json"))  # no orphan file


# ---------------- [2] durability: a re-sent task goes before its controls --------------------------------------
@pytest.mark.asyncio
async def test_after_a_reconnect_the_task_is_sent_before_its_controls(tmp_path):
    down = FakeWs(up=False)
    hub, _ws = _gateway(tmp_path, down)
    dispatch = asyncio.create_task(hub.dispatch(_step_task("t1")))
    for _ in range(100):
        if hub.store.get("task", "t1"):
            break
        await asyncio.sleep(0.01)
    assert (await hub.control_task("t1", "halt"))["sent"] is False
    up = FakeWs()
    hub.register_runner("pc-a", up, [{"id": "worker", "engine": "codex"}], "inc-1", {}, "proc-1")
    await hub.flush_task_controls("pc-a")  # what the hello does, before the waiting dispatch runs again
    await _frame(up, "task.control", "t1")
    kinds = [frame["type"] for frame in up.frames if (frame.get("task_id") or (frame.get("task") or {}).get("id")) == "t1"]
    assert kinds[:2] == ["task.dispatch", "task.control"] and up.frames[kinds.index("task.control")]["action"] == "halt"
    await _finish(hub, "t1", ok=False, error_kind="halted")
    await dispatch


# ---------------- [3]/[6] concurrency: a control through an ended turn reaches the step's live turn ------------
@pytest.mark.asyncio
async def test_a_control_through_an_ended_turn_reaches_the_steps_live_turn(tmp_path):
    hub, ws = _gateway(tmp_path)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "step_id": "A", "kind": "step",
                                 "payload": {"agent_id": "worker"}, "completed": True, "dispatched_at": 1.0})
    hub.store.put("task", "t2", {"request_id": "r", "runner_id": "pc-a", "step_id": "A", "kind": "step",
                                 "payload": {"agent_id": "worker"}, "accepted": True, "dispatched_at": 2.0})
    await hub.control_task("t1", "steer", "t1 줄에서 보낸 지시")  # the PI used the ended HPC-wait row
    await hub.control_task("t1", "pause")
    await hub.control_task("t1", "resume")
    to_t2 = [(f["action"], (f.get("steer") or {}).get("text")) for f in ws.frames
             if f.get("type") == "task.control" and f.get("task_id") == "t2"]
    assert to_t2 == [("steer", "t1 줄에서 보낸 지시"), ("pause", None), ("resume", None)]
    assert not [f for f in ws.frames if f.get("type") == "task.control" and f.get("task_id") == "t1"]


# ---------------- [4] durability: a started() failure after spawn ends the CLI ---------------------------------
def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                             text=True, timeout=15).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_a_cli_start_the_runner_cannot_record_ends_the_cli(tmp_path):
    pid_file = tmp_path / "pid.txt"
    script = tmp_path / "codex_fake.py"
    script.write_text(f"import os, time, pathlib\npathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                      "time.sleep(60)\n", encoding="utf-8")
    settings = Settings()
    settings.engines.codex.bin = sys.executable
    settings.engines.codex.prefix_args = [str(script)]
    (tmp_path / "codex-home").mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.codex, system_prompt="ROLE")

    async def emit(kind, data):
        pass

    def broken():
        time.sleep(1.0)  # the CLI is up by now
        raise OSError("control file is held by another process")

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="do it"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="do it", on_started=broken)
    result = await asyncio.wait_for(get_adapter(Engine.codex, settings).run(ctx), 30)
    assert not result.ok and "could not record the CLI start" in (result.error or "")
    if pid_file.exists():
        pid = int(pid_file.read_text())
        for _ in range(50):
            if not _alive(pid):
                break
            await asyncio.sleep(0.1)
        assert not _alive(pid), "the CLI was left running"


# ---------------- [5] durability: leftovers of a previous process are removed, never merged --------------------
@pytest.mark.asyncio
async def test_leftover_control_files_of_a_previous_runner_are_removed_not_replayed(tmp_path, monkeypatch):
    control = tmp_path / "runner" / "state_dir" / "control"
    control.mkdir(parents=True)
    old = hook.new_state("t1")
    old.update(paused=True, agent_id="worker", session_id="S",
               steers=[{"seq": 1, "id": "steer-1", "text": "이미 쓰인 지시", "at": 1.0}])
    (control / "t1.json").write_text(json.dumps(old), encoding="utf-8")
    prompts = []

    async def run(ctx):
        prompts.append(ctx.prompt)
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="S")

    runner = _runner(tmp_path, monkeypatch, run)
    assert not (control / "t1.json").exists()
    result = await asyncio.wait_for(runner.run_task(_task("t3", session="S")), 5)  # not held by the old pause
    assert result.ok and "이미 쓰인 지시" not in prompts[0]


# ---------------- [8]/[12] concurrency: a turn waiting to start takes a steer and a pause ----------------------
@pytest.mark.asyncio
async def test_a_turn_waiting_to_start_takes_the_pause_and_the_steer(tmp_path, monkeypatch):
    release, started = asyncio.Event(), []

    async def run(ctx):
        started.append((ctx.task.id, CodexAdapter(ctx.settings).compose_prompt(ctx)))
        if ctx.task.id == "a":
            await release.wait()
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done", session_id="Sb")

    runner = _runner(tmp_path, monkeypatch, run)
    runner.sem = asyncio.Semaphore(1)
    first = asyncio.create_task(runner.run_task(_task("a")))
    await asyncio.sleep(0.1)
    second = asyncio.create_task(runner.run_task(_task("b", session="Sb")))
    await asyncio.sleep(0.1)
    await runner._on_message(_msg("b", "steer", "steer-1", "QC 0.9 기준으로"))
    await runner._on_message(_msg("b", "pause"))
    release.set()
    await asyncio.wait_for(first, 2)
    await asyncio.sleep(0.2)
    assert [task_id for task_id, _prompt in started] == ["a"] and not second.done(), "paused: b does not start"
    await runner._on_message(_msg("b", "resume"))
    assert (await asyncio.wait_for(second, 2)).ok
    prompt_b = started[1][1].split("</task>")[0]
    assert "QC 0.9 기준으로" in prompt_b and prompt_b.index("QC 0.9") < prompt_b.index("표를 만드세요")
    [result] = [e["data"] for e in _sent(runner, "task.result", "b")]
    assert result["control"]["delivered"] == ["steer-1"]


# ---------------- [9]/[13] boundary: a steer or pause to a halted step keeps it halted -----------------------
@pytest.mark.asyncio
async def test_a_steer_to_a_halted_step_keeps_it_halted_and_waits_for_the_resume_turn(tmp_path):
    hub, ws = _gateway(tmp_path)
    hub.store.put("task", "t3", {"request_id": "r", "runner_id": "pc-a", "step_id": "A", "kind": "step",
                                 "payload": {"agent_id": "worker"}, "completed": True, "dispatched_at": 1.0})
    hub.halted_waits["t3"] = asyncio.Event()
    hub.task_controls["t3"] = {"halted": True, "halt": True}
    out = await hub.control_task("t3", "steer", "재개하면 이것부터")
    assert out == {"task_id": "t3", "action": "steer", "sent": False, "queued": True}
    assert hub.task_controls["t3"]["halted"] is True  # the resume row stays
    [event] = [e for e in hub.events if e["type"] == "agent.control" and e.get("task_id") == "t3"]
    assert event["data"]["steers_pending"] == 1
    resumed = asyncio.create_task(hub.dispatch(_step_task("t4")))  # the halt-resume turn of the step
    frame = await _frame(ws, "task.dispatch", "t4")
    assert [s["text"] for s in frame["task"]["meta"]["steers"]] == ["재개하면 이것부터"]
    await _finish(hub, "t4")
    await resumed


# ---------------- [10] boundary: a steer no turn took is reported when the step ends -------------------------
@pytest.mark.asyncio
async def test_a_steer_no_turn_took_is_reported_to_the_pi_when_the_step_ends(tmp_path):
    hub, ws = _gateway(tmp_path)
    run = asyncio.create_task(hub.dispatch(_step_task("t1")))
    await _frame(ws, "task.dispatch", "t1")
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t1", "request_id": "r"}, None, None)
    await hub.control_task("t1", "steer", "마지막 turn에 늦게 온 지시")
    steer_id = (await _frame(ws, "task.control", "t1"))["steer"]["id"]
    await _finish(hub, "t1", control={"delivered": [], "undelivered": [{"id": steer_id}], "paused": False})
    await run
    await hub.publish({"type": "request.step_done", "ts": time.time(), "request_id": "r",
                       "data": {"step_id": "A", "ok": True}})
    [told] = [e for e in hub.events if e["type"] == "agent.control" and (e.get("data") or {}).get("undelivered")]
    assert told["data"]["undelivered"] == ["마지막 turn에 늦게 온 지시"] and told["task_id"] == "t1"
    assert hub.store.all("step_control") == {}


# ---------------- [11] boundary: a steer to a queued first turn survives its halt ----------------------------
@pytest.mark.asyncio
async def test_a_steer_to_a_queued_first_turn_survives_its_halt(tmp_path, monkeypatch):
    async def run(ctx):
        raise AssertionError("the halted turn must not start")

    runner = _runner(tmp_path, monkeypatch, run)
    runner.sem = asyncio.Semaphore(0)  # no slot will come
    queued = asyncio.create_task(runner.run_task(_task("t2")))
    await asyncio.sleep(0.1)
    await runner._on_message(_msg("t2", "steer", "steer-1", "GRCh38로 하세요"))
    await runner._on_message(_msg("t2", "halt"))
    result = await asyncio.wait_for(queued, 2)
    assert result.error_kind == "halted"
    [sent] = [e["data"] for e in _sent(runner, "task.result", "t2")]
    assert sent["control"]["undelivered"] == [{"id": "steer-1", "text": "GRCh38로 하세요"}]  # back to the step


# ---------------- [15] boundary: the step queue keeps 20 and counts the rest ----------------------------------
@pytest.mark.asyncio
async def test_the_step_queue_keeps_twenty_steers_and_counts_the_dropped(tmp_path):
    hub, ws = _gateway(tmp_path)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "step_id": "A", "kind": "step",
                                 "payload": {"agent_id": "worker"}, "completed": True, "dispatched_at": 1.0})
    for n in range(25):
        await hub.control_task("t1", "steer", f"지시 {n}")
    state = hub._step(hub.step_key("r", "A", "step", "t1"))
    assert len(state["steers"]) == 20 and state["dropped"] == 5
    nxt = asyncio.create_task(hub.dispatch(_step_task("t2")))
    meta = (await _frame(ws, "task.dispatch", "t2"))["task"]["meta"]
    assert [s["text"] for s in meta["steers"]] == [f"지시 {n}" for n in range(5, 25)] and meta["steer_dropped"] == 5
    await _finish(hub, "t2")
    await nxt


# ---------------- new invariants ----------------
@pytest.mark.asyncio
async def test_a_paused_step_does_not_send_its_next_turn_until_resumed(tmp_path):
    hub, ws = _gateway(tmp_path)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "step_id": "A", "kind": "step",
                                 "payload": {"agent_id": "worker"}, "completed": True, "dispatched_at": 1.0})
    await hub.control_task("t1", "pause")
    nxt = asyncio.create_task(hub.dispatch(_step_task("t2")))
    await asyncio.sleep(0.2)
    assert not [f for f in ws.frames if f.get("type") == "task.dispatch"]
    assert hub.requests["r"]["status"] == "waiting_pi"
    [row] = [t for t in hub.running_tasks() if t["step_id"] == "A"]
    assert row["id"] == "t1" and row["state"] == "paused"  # the row the PI resumes from, after a reload too
    await hub.control_task("t1", "resume")
    await _frame(ws, "task.dispatch", "t2")
    assert hub.requests["r"]["status"] == "running"
    await _finish(hub, "t2")
    await nxt
    assert [e["type"] for e in hub.events if e["type"] in ("request.step_paused", "request.step_unpaused")] == [
        "request.step_paused", "request.step_unpaused"]


@pytest.mark.asyncio
async def test_a_task_halted_before_a_runner_restart_parks_instead_of_failing(tmp_path):
    hub, ws = _gateway(tmp_path)
    run = asyncio.create_task(hub.dispatch(_step_task("t1")))
    await _frame(ws, "task.dispatch", "t1")
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t1", "request_id": "r"}, None, None)
    await hub.control_task("t1", "halt")
    # The runner process restarted before the halt took: its new process reports the task interrupted.
    await hub.on_runner_message("pc-a", {"type": "task.result", "task_id": "t1", "request_id": "r", "data": {
        "task_id": "t1", "agent_id": "worker", "ok": False,
        "error": "runner restarted: accepted task interrupted"}}, None, None)
    result = await run
    assert result.error_kind == "halted" and "interrupted" in result.error
