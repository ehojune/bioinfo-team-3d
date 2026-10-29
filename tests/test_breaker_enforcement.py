"""Runaway breaker stages and enforcement (#59 ②): beat-paced escalation, engine support, hardStop, PI release."""
import functools
import itertools
import json
from types import SimpleNamespace

import pytest

from labhq.gateway.server import Hub
from labhq.hooks import control as control_hook
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.breaker import Breaker, BreakerPolicy, Escalation
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import tool_input_fingerprint


def test_escalation_moves_one_stage_per_beat_and_only_on_new_observations():
    now = [0.0]
    ladder = Escalation(BreakerPolicy(), beat_s=10, clock=lambda: now[0])
    assert ladder.observe(True) == "warn"
    for second in (1, 5, 9.9):
        now[0] = second
        assert ladder.observe(True) is None  # inside the beat
    now[0] = 10
    assert ladder.observe(True) == "constrain"
    now[0] = 25
    assert ladder.observe(True) == "stop"
    now[0] = 99
    assert ladder.observe(True) is None  # nothing above stop
    assert ladder.release() == "normal" and ladder.release() is None
    assert ladder.observe(True) == "warn"  # a released ladder starts again at once


def test_a_quiet_signal_lifts_a_warning_but_not_a_restriction():
    now = [0.0]
    ladder = Escalation(BreakerPolicy(), beat_s=1, clock=lambda: now[0])
    ladder.observe(True)
    assert ladder.observe(False) == "normal"
    now[0] = 2
    assert ladder.observe(True) == "warn"
    now[0] = 4
    assert ladder.observe(True) == "constrain"
    assert ladder.observe(False) is None


def test_breaker_active_tracks_the_signal_not_the_first_trip():
    breaker = Breaker()
    call = ("agent.tool", {"name": "shell", "input_fingerprint": tool_input_fingerprint("shell", "x")})
    assert [breaker.observe(*call) is not None for _ in range(10)].count(True) == 1
    assert breaker.active
    breaker.observe("agent.tool", {"name": "shell", "input_fingerprint": tool_input_fingerprint("shell", "y")})
    assert not breaker.active


def _runner(tmp_path, monkeypatch, engine, *, hard_stop=False, calls=11, between=None):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.breaker_beat_s = 0.5
    ticks = itertools.count()  # a fake clock: the Windows monotonic clock is too coarse for back-to-back events
    monkeypatch.setattr("labhq.runner.daemon.Escalation", functools.partial(Escalation, clock=lambda: float(next(ticks))))
    settings.runner.breaker_hard_stop = hard_stop
    runner = Runner(settings)
    agent = AgentSpec(id="engineer", name="Engineer", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    sent = []
    monkeypatch.setattr(runner, "send", lambda event: sent.append(event))
    seen = {}

    class Looping:
        async def run(self, ctx):
            for i in range(calls):
                await ctx.emit("agent.tool", {"name": "shell", "input": "x",
                                             "input_fingerprint": tool_input_fingerprint("shell", "x")})
                if between:
                    await between(runner, ctx, i, seen)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Looping())
    return runner, sent, seen


def _stages(sent):
    return [(e["data"]["stage"], e["data"].get("enforcement")) for e in sent
            if e["type"] == "agent.breaker" and "stage" in e["data"]]


async def test_an_engine_without_hooks_shows_unsupported_and_is_not_marked_restricted(tmp_path, monkeypatch):
    runner, sent, _ = _runner(tmp_path, monkeypatch, Engine.mock)
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        assert _stages(sent) == [("constrain", "unsupported"), ("stop", "off")]
        texts = [e["data"]["text"] for e in sent if e["type"] == "agent.log"]
        assert any("제한하지 못했어요" in t for t in texts) and any("hardStop이 꺼져" in t for t in texts)
    finally:
        runner.store.close()


async def test_a_hooked_engine_is_restricted_to_read_tools_and_the_pi_can_release_it(tmp_path, monkeypatch):
    async def between(runner, ctx, i, seen):
        path = runner.controls.path(ctx.task.id)
        if i == 8:  # after the constrain stage
            state = control_hook.read_state(path)
            seen["constrained"] = state["constrained"]
            seen["bash"] = control_hook.pre_tool_use(path, tool_name="Bash")
            await runner._on_control({"type": "task.control", "task_id": ctx.task.id, "action": "release_constraint"})
            seen["after"] = control_hook.read_state(path)["constrained"]

    runner, sent, seen = _runner(tmp_path, monkeypatch, Engine.claude_code, calls=9, between=between)
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        assert seen["constrained"] is True
        # The staff sheet only learns of the restriction (and shows its release button) through agent.control.
        controls = [e["data"] for e in sent if e["type"] == "agent.control"]
        assert controls and controls[0]["constrained"] is True and controls[0]["source"] == "breaker"
        assert seen["bash"]["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert seen["after"] is False
        assert _stages(sent) == [("constrain", "applied"), ("normal", "released")]
    finally:
        runner.store.close()


async def test_hard_stop_halts_through_the_existing_halt_path_and_only_when_on(tmp_path, monkeypatch):
    halted = {}

    async def between(runner, ctx, i, seen):
        halted["set"] = runner.controls.halt_event(ctx.task.id).is_set()

    runner, sent, _ = _runner(tmp_path, monkeypatch, Engine.mock, hard_stop=True, between=between)
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        assert _stages(sent)[-1] == ("stop", "halt")
        assert halted["set"] is True
    finally:
        runner.store.close()


async def test_other_tasks_are_unaffected_by_one_tasks_breaker(tmp_path, monkeypatch):
    runner, sent, _ = _runner(tmp_path, monkeypatch, Engine.claude_code)
    try:
        other = tmp_path / "other.json"
        control_hook.update_state(other, lambda state: None, create="other")
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        assert control_hook.pre_tool_use(other, tool_name="Bash") is None
        assert runner.breakers == {}  # dropped with the task
    finally:
        runner.store.close()


async def test_gateway_release_reaches_the_runner_and_refuses_finished_tasks(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "step_id": "s1", "kind": "step"})
    hub.store.put("task", "done", {"request_id": "r", "runner_id": "pc-a", "completed": True, "result": {"ok": True},
                                   "payload": {"agent_id": "worker"}, "step_id": "s0", "kind": "step"})
    sent = []

    async def send_runner(runner_id, message):
        sent.append((runner_id, message))

    hub.send_runner = send_runner
    assert (await hub.release_breaker("t1"))["sent"] is True
    assert sent[0][0] == "pc-a" and sent[0][1]["action"] == "release_constraint" and sent[0][1]["task_id"] == "t1"
    with pytest.raises(ValueError):
        await hub.release_breaker("done")
    with pytest.raises(KeyError):
        await hub.release_breaker("nope")


async def test_denied_calls_after_constrain_do_not_push_the_task_to_stop(tmp_path, monkeypatch):
    # #1008: once restricted, a failing call may be the restriction's own denial; only a repeat keeps climbing.
    runner, sent, _ = _runner(tmp_path, monkeypatch, Engine.claude_code, calls=0)

    class Denied:
        async def run(self, ctx):
            same = {"name": "shell", "input": "x", "input_fingerprint": tool_input_fingerprint("shell", "x")}
            for _ in range(8):
                await ctx.emit("agent.tool", same)  # repeat signal: warn
            for i in range(30):
                await ctx.emit("agent.tool", {"name": "Bash", "input": f"c{i}",
                                             "input_fingerprint": tool_input_fingerprint("Bash", f"c{i}")})
                await ctx.emit("agent.tool_error", {"text": "Runaway restriction: read-only tools only"})
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Denied())
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        stages = [stage for stage, _ in _stages(sent)]
        assert "constrain" in stages and "stop" not in stages, "error streaks alone never reach stop"
    finally:
        runner.store.close()


async def test_a_breaker_halt_reaches_the_gateway_as_a_pi_halt_and_a_release_waits_for_the_runner(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True, "step_id": "s1", "kind": "step", "dispatched_at": 1.0})
    sent, online = [], {"pc-a": False}

    async def send_runner(runner_id, message):
        if not online[runner_id]:
            from labhq.models import RunnerUnavailable
            raise RunnerUnavailable("offline")
        sent.append(message)

    hub.send_runner = send_runner
    await hub._note_runner_halt("t1")
    assert hub.store.get("task", "t1")["halt_requested"] is True  # a reconnecting runner is told to halt again
    assert (await hub.release_breaker("t1"))["sent"] is False
    online["pc-a"] = True
    await hub._push_task_state("t1")
    assert [m["action"] for m in sent if m["action"] != "halt"] == ["release_constraint"]
    sent.clear()
    await hub._push_task_state("t1")  # no acknowledgement yet: the same command goes again
    assert [m["action"] for m in sent if m["action"] != "halt"] == ["release_constraint"]


def _gateway(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True, "step_id": "s1", "kind": "step", "dispatched_at": 1.0})
    sent = []

    async def send_runner(runner_id, message):
        sent.append(message)

    hub.send_runner = send_runner
    return hub, sent


async def test_a_release_command_survives_a_restart_and_is_settled_by_the_runners_ack(tmp_path):
    hub, sent = _gateway(tmp_path)
    hub._note_breaker_event("t1", {"stage": "constrain", "episode": "run1:2"})
    out = await hub.release_breaker("t1")
    command = sent[0]
    assert out["sent"] and out["command_id"] == command["command_id"] and command["episode"] == "run1:2"
    hub2, sent2 = _gateway(tmp_path)  # a gateway restart: the unacknowledged command is still there
    await hub2._push_task_state("t1")
    assert [m.get("command_id") for m in sent2 if m["action"] == "release_constraint"] == [command["command_id"]]
    hub2._note_breaker_event("t1", {"enforcement": "released", "command_id": "someone-else"})
    assert hub2.store.get("breaker_release", "t1"), "another command's ack settles nothing"
    hub2._note_breaker_event("t1", {"enforcement": "released", "command_id": command["command_id"]})
    assert hub2.store.get("breaker_release", "t1") is None
    sent2.clear()
    await hub2._push_task_state("t1")
    assert all(m["action"] != "release_constraint" for m in sent2), "settled: never sent again"


async def test_the_runner_acks_a_release_once_applied_and_never_applies_a_resend_or_a_stale_one(tmp_path, monkeypatch):
    async def between(runner, ctx, i, seen):
        if i == 8:  # the task is constrained
            seen["episode"] = runner.breakers[ctx.task.id].episode_id
            for command, episode in (("c1", seen["episode"]), ("c1", seen["episode"]),
                                     ("c0", seen["episode"].split(":")[0] + ":99")):
                await runner._on_control({"type": "task.control", "task_id": ctx.task.id,
                                          "action": "release_constraint", "command_id": command, "episode": episode})
                acks = [e["data"] for e in runner.sent if e["type"] == "agent.breaker" and e["data"].get("command_id")]
                seen.setdefault("acks", []).append(acks[-1])

    runner, sent, seen = _runner(tmp_path, monkeypatch, Engine.claude_code, calls=9, between=between)
    runner.sent = sent
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        first, resend, stale = seen["acks"]
        assert (first["command_id"], first["enforcement"]) == ("c1", "released") and first["episode"] == seen["episode"]
        assert (resend["command_id"], resend["enforcement"]) == ("c1", "released")
        assert (stale["command_id"], stale["enforcement"]) == ("c0", "stale")
        logs = [e["data"]["text"] for e in sent if e["type"] == "agent.log" and "차단기를 풀었어요" in e["data"].get("text", "")]
        assert len(logs) == 1, "the resend and the stale command say nothing more"
    finally:
        runner.store.close()


def test_an_episode_id_never_repeats_across_runs():
    # A runner restart (or a task sent again) starts a new ladder; a release saved for the old run must not match it.
    first, second = Escalation(BreakerPolicy()), Escalation(BreakerPolicy())
    first.episode = second.episode = 1
    assert first.episode_id != second.episode_id
    assert first.episode_id.endswith(":1") and second.episode_id.endswith(":1")


async def test_a_release_saved_with_a_numeric_episode_is_sent_so_it_matches_nothing(tmp_path):
    # A row stored before episode IDs were strings must not be resent unscoped: it could free a newer restriction.
    hub, sent = _gateway(tmp_path)
    hub.store.put("breaker_release", "t1", {"command_id": "old", "episode": 3, "at": 1.0})
    await hub._push_task_state("t1")
    [message] = [m for m in sent if m["action"] == "release_constraint"]
    assert message["command_id"] == "old" and message["episode"] == "legacy"
    assert Escalation(BreakerPolicy()).episode_id != message["episode"]


def test_a_compaction_restarts_the_count_so_redoing_old_work_is_not_a_loop():
    breaker = Breaker()
    same = ("agent.tool", {"name": "shell", "input_fingerprint": tool_input_fingerprint("shell", "x")})
    assert [breaker.observe(*same) for _ in range(7)] == [None] * 7
    breaker.reset_window()
    assert [breaker.observe(*same) for _ in range(7)] == [None] * 7, "seven more after compaction is still under the limit"
    assert breaker.observe(*same) is not None, "an eighth in the new window is a real repeat"


async def test_claude_code_reports_its_compact_boundary_frame():
    # Claude Code 2.1.310's stream-json: {"type":"system","subtype":"compact_boundary","compact_metadata":{...}}.
    from labhq.adapters import get_adapter
    from labhq.adapters.base import RunState

    events = []

    async def emit(kind, data):
        events.append((kind, data))

    frame = {"type": "system", "subtype": "compact_boundary", "uuid": "u", "session_id": "s",
             "compact_metadata": {"trigger": "auto", "pre_tokens": 170000, "post_tokens": 12000}}
    await get_adapter("claude_code", Settings()).handle_line(json.dumps(frame), RunState(), SimpleNamespace(emit=emit))
    assert events == [("agent.compaction", {"trigger": "auto", "pre_tokens": 170000, "post_tokens": 12000})]


async def test_a_compaction_during_a_run_keeps_repeats_across_it_from_tripping_the_breaker(tmp_path, monkeypatch):
    runner, sent, _ = _runner(tmp_path, monkeypatch, Engine.mock, calls=0)

    class Redoing:
        async def run(self, ctx):
            same = {"name": "shell", "input": "x", "input_fingerprint": tool_input_fingerprint("shell", "x")}
            for _ in range(7):
                await ctx.emit("agent.tool", same)
            await ctx.emit("agent.compaction", {"trigger": "auto", "pre_tokens": 1, "post_tokens": 1})
            for _ in range(7):
                await ctx.emit("agent.tool", same)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Redoing())
    try:
        await runner.run_task(Task(agent_id="engineer", prompt="redo", meta={"kind": "step"}))
        assert [e for e in sent if e["type"] == "agent.breaker"] == []
        assert any(e["type"] == "agent.compaction" for e in sent)
    finally:
        runner.store.close()
