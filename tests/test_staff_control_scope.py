"""PR #532 second verification: only step and direct turns take controls, a halt never drops an HPC or answer wait,
a resume after a halt lifts the pause sent before it, and smaller reporting fixes (wf532r2)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from labhq.gateway import server as gateway_server
from labhq.gateway.server import Hub, create_app
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


class FakeWs:
    def __init__(self):
        self.frames = []

    async def send_text(self, payload):
        self.frames.append(json.loads(payload))

    async def close(self, code=1000):
        pass


def _gateway(tmp_path, status="running"):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": status}
    hub.save_request("r")
    ws = FakeWs()
    hub.register_runner("pc-a", ws, [{"id": "worker", "engine": "codex"}], "inc-1", {}, "proc-1")
    return hub, ws


def _entry(hub, tid, kind="step", step_id="A", **extra):
    hub.store.put("task", tid, {"request_id": "r", "runner_id": "pc-a", "kind": kind, "step_id": step_id,
                                "payload": {"agent_id": "worker"}, "accepted": True, "dispatched_at": time.time(),
                                **extra})


def _frames(ws, kind, tid):
    return [f for f in ws.frames if f.get("type") == kind and (f.get("task_id") or (f.get("task") or {}).get("id")) == tid]


async def _wait_frame(ws, kind, tid, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _frames(ws, kind, tid):
            return _frames(ws, kind, tid)[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind} for {tid}")


async def _result(hub, tid, **data):
    await hub.on_runner_message("pc-a", {"type": "task.result", "task_id": tid, "request_id": "r", "data": {
        "task_id": tid, "agent_id": "worker", "ok": True, "text": "done", **data}}, None, None)


def _step(tid, step="A", kind="step") -> Task:
    return Task(id=tid, request_id="r", agent_id="worker", prompt="work",
                meta={"kind": kind, **({"step_id": step} if step else {})})


# ---------------- 1. scope: only step and direct turns ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "step_id"), [("consult", None), ("followup", None), ("review", None),
                                               ("briefing", None), ("precedent", None), ("recruit", None),
                                               ("plan", None), ("synthesis", None), ("wrap_up", "A"), ("step", None)])
async def test_only_step_and_direct_turns_take_controls(tmp_path, kind, step_id):
    hub, ws = _gateway(tmp_path)
    _entry(hub, "t1", kind=kind, step_id=step_id)
    for action in ("steer", "pause", "resume", "halt"):
        with pytest.raises(ValueError, match="실행 중 제어를 받지 않습니다"):
            await hub.control_task("t1", action, "x" if action == "steer" else None)
    assert not _frames(ws, "task.control", "t1") and hub.store.all("step_control") == {}


@pytest.mark.asyncio
async def test_step_and_direct_turns_take_controls_and_other_kinds_are_refused_over_http(tmp_path, monkeypatch,
                                                                                          capsys):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    settings.gateway.client_token = "pi-token"
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}

    async def send_runner(_runner_id, _message):
        pass

    hub.send_runner = send_runner
    _entry(hub, "s", kind="step")
    _entry(hub, "d", kind="direct", step_id=None)
    _entry(hub, "c", kind="consult", step_id=None)
    pi = {"Authorization": "Bearer pi-token"}
    with TestClient(app) as client:
        assert client.post("/api/tasks/s/pause", headers=pi).status_code == 200
        assert client.post("/api/tasks/d/steer", json={"text": "x"}, headers=pi).status_code == 200
        refused = client.post("/api/tasks/c/steer", json={"text": "x"}, headers=pi)
    assert refused.status_code == 409 and "consult" in refused.json()["detail"]
    assert "실행 중 제어를 받지 않습니다" in refused.json()["detail"]

    from labhq import cli
    import httpx

    def fake_request(method, url, **kwargs):
        return httpx.Response(409, json={"detail": refused.json()["detail"]}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", fake_request)
    with pytest.raises(SystemExit):
        cli.task_control(settings, "steer", "c", "x")
    err = capsys.readouterr().err
    assert "HTTP 409" in err and "실행 중 제어를 받지 않습니다" in err  # the CLI says the same


@pytest.mark.asyncio
async def test_a_consult_dispatch_never_waits_on_a_step_pause_or_takes_its_steers(tmp_path):
    hub, ws = _gateway(tmp_path)
    key = hub.step_key("r", None, "consult", "c1")
    hub.store.put("step_control", key, {"steers": [{"id": "steer-1", "text": "alice에게만", "task": None}],
                                        "paused": True, "last_task": "c1"})
    run = asyncio.create_task(hub.dispatch(_step("c2", step=None, kind="consult")))
    frame = await _wait_frame(ws, "task.dispatch", "c2", timeout=1.0)
    assert "steers" not in frame["task"]["meta"]
    await _result(hub, "c2")
    await run


# ---------------- 2. halt and HPC / answer waits ----------------
@pytest.mark.asyncio
async def test_halt_is_refused_while_the_turn_waits_on_hpc_jobs_or_a_question(tmp_path):
    hub, _ws = _gateway(tmp_path)
    _entry(hub, "t1")
    await hub.on_runner_message("pc-a", {"type": "job.submitted", "task_id": "t1", "request_id": "r",
                                         "agent_id": "worker", "data": {"job_id": "42"}}, None, None)
    with pytest.raises(ValueError, match="일시 정지를 쓰세요"):
        await hub.control_task("t1", "halt")
    _entry(hub, "t2")
    hub.store.put("ask", "ask-1", {"state": "pending", "ask": {"task_id": "t2", "id": "ask-1"}})
    with pytest.raises(ValueError) as refused:
        await hub.control_task("t2", "halt")
    assert str(refused.value) == getattr(gateway_server, "HALT_WHILE_WAITING", None)
    assert (await hub.control_task("t2", "pause"))["sent"] is True  # pausing it is the way


@pytest.mark.asyncio
async def test_a_halt_that_meets_an_hpc_wait_keeps_the_jobs_and_pauses_the_step(tmp_path):
    hub, ws = _gateway(tmp_path)
    # The hook halted right after the submit: the halted result still holds the job.
    run = asyncio.create_task(hub.dispatch(_step("t1")))
    await _wait_frame(ws, "task.dispatch", "t1")
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t1", "request_id": "r"}, None, None)
    await _result(hub, "t1", ok=False, error_kind="halted", error="halted", pending_jobs=["job-1"], session_id="S")
    result = await run
    assert result.ok and result.error_kind is None and result.pending_jobs == ["job-1"]  # the wake turn stays
    assert hub._step(hub.step_key("r", "A", "step", "t1"))["paused"] is True
    # The halt came after the last tool call: the turn ended ok into its wait, and the wake turn waits for resume.
    run2 = asyncio.create_task(hub.dispatch(_step("t2", step="B")))
    await _wait_frame(ws, "task.dispatch", "t2")
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t2", "request_id": "r"}, None, None)
    await hub.control_task("t2", "halt")
    await _result(hub, "t2", pending_jobs=["job-2"])
    assert (await run2).pending_jobs == ["job-2"]
    wake = asyncio.create_task(hub.dispatch(_step("t3", step="B")))
    await asyncio.sleep(0.3)
    assert not _frames(ws, "task.dispatch", "t3"), "the halt became a pause: the wake turn waits"
    assert any("일시 정지했어요" in (e.get("data") or {}).get("text", "") for e in hub.events if e["type"] == "agent.log")
    await hub.control_task("t2", "resume")
    await _wait_frame(ws, "task.dispatch", "t3")
    await _result(hub, "t3")
    await wake


# ---------------- 3. pause and halt ----------------
@pytest.mark.asyncio
async def test_resume_after_a_halt_lifts_the_pause_sent_before_it_but_not_one_sent_after(tmp_path):
    hub, ws = _gateway(tmp_path)
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="S")
    # Paused, then halted: one resume continues the step.
    _entry(hub, "t1")
    await hub.control_task("t1", "pause")
    await hub.control_task("t1", "halt")
    _entry(hub, "t1", completed=True)
    parked = asyncio.create_task(hub.wait_halted("r", "A", halted))
    await asyncio.sleep(0.05)
    assert (await hub.control_task("t1", "resume"))["resumed_halted"] is True
    assert await parked is True
    nxt = asyncio.create_task(hub.dispatch(_step("t2")))
    await _wait_frame(ws, "task.dispatch", "t2", timeout=1.0)
    await _result(hub, "t2")
    await nxt
    # Halted, then paused while parked: the later pause still holds the resume turn.
    _entry(hub, "u1", step_id="B")
    await hub.control_task("u1", "halt")
    _entry(hub, "u1", step_id="B", completed=True)
    parked = asyncio.create_task(hub.wait_halted("r", "B", halted.model_copy(update={"task_id": "u1"})))
    await asyncio.sleep(0.05)
    await hub.control_task("u1", "pause")
    await hub.control_task("u1", "resume")
    assert await parked is True
    held = asyncio.create_task(hub.dispatch(_step("u2", step="B")))
    await asyncio.sleep(0.3)
    assert not _frames(ws, "task.dispatch", "u2")
    await hub.control_task("u1", "resume")
    await _wait_frame(ws, "task.dispatch", "u2")
    await _result(hub, "u2")
    await held


# ---------------- 4. smaller fixes ----------------
@pytest.mark.asyncio
async def test_a_steer_that_overtakes_the_first_dispatch_frame_is_sent_again_once_the_task_is_accepted(tmp_path):
    hub, ws = _gateway(tmp_path)
    original = hub.publish
    sent = {}

    async def publish(event, *args, **kwargs):
        await original(event, *args, **kwargs)
        if event.get("type") == "task.dispatched" and not sent:
            sent["out"] = await hub.control_task(event["task_id"], "steer", "지금 바로")  # before task.dispatch

    hub.publish = publish
    run = asyncio.create_task(hub.dispatch(_step("t1")))
    await _wait_frame(ws, "task.dispatch", "t1")
    # The post-dispatch replay now awaits its own durable step commit.
    async def wait_replay():
        while len(_frames(ws, "task.control", "t1")) < 2:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait_replay(), 3.0)
    [early, after_frame] = _frames(ws, "task.control", "t1")
    steer_id = early["steer"]["id"]
    assert after_frame["steer"]["id"] == steer_id  # resend immediately after dispatch, even before acceptance
    # The runner does not hold t1 yet: it NACKs, then accepts the task that follows.
    await hub.on_runner_message("pc-a", {"type": "control.nack", "task_id": "t1", "action": "steer",
                                         "steer_id": steer_id, "reason": "unknown task"}, None, None)
    await hub.on_runner_message("pc-a", {"type": "task.accepted", "task_id": "t1", "request_id": "r"}, None, None)
    order = [f["type"] for f in ws.frames if (f.get("task_id") or (f.get("task") or {}).get("id")) == "t1"]
    assert order == ["task.control", "task.dispatch", "task.control", "task.control"]
    assert _frames(ws, "task.control", "t1")[-1]["steer"]["id"] == steer_id
    await _result(hub, "t1", control={"delivered": [steer_id], "undelivered": [], "paused": False})
    await run
    assert hub._step(hub.step_key("r", "A", "step", "t1"))["steers"] == []


@pytest.mark.asyncio
async def test_twenty_one_steers_to_a_live_turn_tell_no_drop(tmp_path):
    hub, ws = _gateway(tmp_path)
    _entry(hub, "t1")
    for n in range(21):
        await hub.control_task("t1", "steer", f"지시 {n}")
    frames = _frames(ws, "task.control", "t1")
    assert len(frames) == 21 and all(f["dropped"] == 0 for f in frames)
    assert hub._step(hub.step_key("r", "A", "step", "t1"))["dropped"] == 0


@pytest.mark.asyncio
async def test_a_pause_after_the_run_slot_is_taken_is_reported_as_it_will_act(tmp_path, monkeypatch):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / "runner" / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    real_take = runner.controls.take_prompt_steers
    ran = []

    def take(task_id):
        out = real_take(task_id)
        asyncio.get_running_loop().create_task(runner._on_message({"type": "task.control", "task_id": task_id,
                                                                   "action": "pause", "agent_id": "worker"}))
        return out

    class FakeCli:
        async def run(self, ctx):
            await asyncio.sleep(0.2)  # preparing the CLI: the pause lands here
            ctx.on_started()
            ran.append(ctx.task.id)
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done")

    monkeypatch.setattr(runner.controls, "take_prompt_steers", take)
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    await runner.run_task(Task(id="t1", request_id="r1", agent_id="worker", prompt="p",
                               meta={"kind": "step", "step_id": "s1"}))
    notes = [e["data"]["text"] for e in runner.store.pending() if e["type"] == "agent.log"
             and "일시 정지" in e["data"].get("text", "")]
    assert ran == ["t1"] and notes, notes
    assert not any("시작하지 않습니다" in note for note in notes)  # it did start
    assert any("이번 turn은 계속하고" in note for note in notes)
