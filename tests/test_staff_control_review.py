"""PR #532 review: unapplied controls stay unacknowledged, resends apply once, and an expired halt is told to pages."""
from __future__ import annotations

import pytest

from labhq.gateway.server import Hub
from labhq.hooks import control as hook
from labhq.models import TaskResult
from labhq.runner.control import carry_key
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _runner(tmp_path) -> Runner:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / "runner" / name))
    runner = Runner(settings)
    runner.CONTROL_RETRY_S = (0.0, 0.0)  # the bounded local retries, without waiting in a test
    return runner


def _hub(tmp_path) -> Hub:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "worker"},
                                 "accepted": True})
    return hub


def _acks(runner):
    return [event for event in runner.store.pending() if event["type"] == "control.ack"]


def _msg(tid, action, seq, text=None):
    return {"type": "task.control", "id": f"ctl-{seq}", "seq": seq, "task_id": tid, "action": action,
            "agent_id": "worker", **({"text": text} if text else {})}


@pytest.mark.asyncio
async def test_a_control_that_could_not_be_applied_is_not_acked_and_the_reconnect_resend_applies_it(
        tmp_path, monkeypatch):
    runner, hub = _runner(tmp_path), _hub(tmp_path)
    runner.controls.start("t1", "worker", hooks=True, session_id=None)
    path = runner.controls.path("t1")

    async def send_runner(_runner_id, message):
        await runner._on_message(message)

    hub.send_runner = send_runner
    real_update = hook.update_state
    attempts = []

    def busy(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError("control file lock is busy")

    monkeypatch.setattr(hook, "update_state", busy)
    assert (await hub.control_task("t1", "pause"))["sent"] is True
    assert len(attempts) == 3  # the first try and two bounded retries
    assert _acks(runner) == []  # nothing tells the gateway to drop it
    assert len(hub.store.all("task_control")) == 1
    assert hook.read_state(path)["paused"] is False
    warnings = [e["data"]["text"] for e in runner.store.pending() if e["type"] == "agent.log"]
    assert any("적용하지 못했어요" in text for text in warnings)

    monkeypatch.setattr(hook, "update_state", real_update)
    await hub.flush_task_controls("pc-a")  # what the gateway does when the runner reconnects
    assert hook.read_state(path)["paused"] is True
    [ack] = _acks(runner)
    await hub.on_runner_message("pc-a", ack, None, None)
    assert hub.store.all("task_control") == {}


@pytest.mark.asyncio
async def test_a_resent_steer_for_an_ended_task_is_carried_once(tmp_path):
    runner = _runner(tmp_path)
    runner.controls.start("t1", "worker", hooks=False, session_id=None)
    await runner._on_message(_msg("t1", "steer", 5, "chr1만 보세요"))
    runner.controls.finish("t1", "sess-1")
    carried = lambda: runner.store.get("control_carry", carry_key("worker", "sess-1"))  # noqa: E731
    assert [s["text"] for s in carried()["steers"]] == ["chr1만 보세요"]

    # The ack was lost and the gateway resends the same control after the task ended.
    await runner._on_message(_msg("t1", "steer", 5, "chr1만 보세요"))
    assert [s["text"] for s in carried()["steers"]] == ["chr1만 보세요"]
    assert len(_acks(runner)) == 2  # a known duplicate is acknowledged

    # The next turn's CLI starts with the steer; a later resend still does not bring it back.
    steers, _dropped, _paused = runner.controls.start("t2", "worker", hooks=False, session_id="sess-1")
    assert [s["text"] for s in steers] == ["chr1만 보세요"]
    runner.controls.started("t2")
    assert carried() is None
    await runner._on_message(_msg("t1", "steer", 5, "chr1만 보세요"))
    assert carried() is None


@pytest.mark.asyncio
async def test_a_control_resent_after_later_ones_still_applies_but_an_old_toggle_does_not_win(tmp_path):
    runner = _runner(tmp_path)
    runner.controls.start("t1", "worker", hooks=True, session_id=None)
    path = runner.controls.path("t1")
    # seq 2 (a steer) and seq 3 (a pause) failed and come back after seq 4 (a resume) was applied.
    await runner._on_message(_msg("t1", "resume", 4))
    await runner._on_message(_msg("t1", "steer", 2, "늦게 다시 온 지시"))
    await runner._on_message(_msg("t1", "pause", 3))
    state = hook.read_state(path)
    assert [s["text"] for s in state["steers"]] == ["늦게 다시 온 지시"]
    assert state["paused"] is False  # the PI's last toggle was resume
    assert sorted(state["applied_seqs"]) == [2, 3, 4]


@pytest.mark.asyncio
async def test_a_halt_nobody_resumes_tells_open_pages_it_expired(tmp_path):
    hub = _hub(tmp_path)
    hub.s.policy.approvals.pi_decision_timeout_s = 0.05
    hub.requests["r"] = {"id": "r", "text": "work", "mode": "team", "status": "running"}
    hub.save_request("r")
    halted = TaskResult(task_id="t1", agent_id="worker", ok=False, error_kind="halted", session_id="sess-1")
    assert await hub.wait_halted("r", "A", halted) is False
    [expired] = [e for e in hub.events if e["type"] == "request.step_halt_expired"]
    assert expired["task_id"] == "t1" and expired["agent_id"] == "worker" and expired["data"] == {"step_id": "A", "reason": "timed_out"}
    assert not [e for e in hub.events if e["type"] == "request.step_resumed"]
    assert hub.requests["r"]["status"] == "running" and not hub.halted_waits and "t1" not in hub.task_controls
