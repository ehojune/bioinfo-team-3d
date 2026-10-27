"""Reproduction only: pinned d5202665, real isolated Hub/Store, mock transport."""
import json
from unittest.mock import AsyncMock

import pytest

from labhq import durable
from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.mark.parametrize("scenario", ["no_race", "stale_before_attach", "advance_after_attach"])
async def test_rejected_dispatch_returns_its_reservations(tmp_path, monkeypatch, scenario):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.requests["req"] = {"id": "req", "status": "running", "results": {}}
    hub.runners["runner"] = object()
    hub.set_roster("runner", [{"id": "mock", "engine": "mock"}])
    key = hub.step_key("req", "s1", "step", "")
    frames = []

    def task(tid, revision):
        return Task(id=tid, request_id="req", agent_id="mock", prompt="mock only",
                    meta={"kind": "step", "step_id": "s1", "revision": revision})

    def snapshot():
        return hub._step(key)

    async def send(runner, frame):
        frames.append(frame)
        if frame["type"] == "task.dispatch":
            tid = frame["task"]["id"]
            # Transport completion only; intentionally no result settlement.
            hub.futures[tid].set_result(TaskResult(task_id=tid, agent_id="mock", ok=True))

    monkeypatch.setattr(hub, "send_runner", send)
    # Event presentation/PI translation is outside this reservation experiment.
    monkeypatch.setattr(hub, "publish", AsyncMock())
    original_attach = hub._attach_steers
    trace = {}

    async def attach_then_advance(value):
        attached = await original_attach(value)
        if value.id == "old":
            trace["after_old_attach"] = snapshot()
            trace["old_attached_meta"] = attached.meta
            if scenario == "advance_after_attach":
                # Force the precise interleaving: committed old attachment ->
                # committed higher revision -> dispatch's stale guard.
                def advance(state):
                    state["revision"] = 1
                    state["last_task"] = "revision-owner"
                    # Separate concurrent owner's notification must survive cleanup.
                    state["dropped"] += 2
                    state["dropped_told"] += 2
                    state["drop_reservations"]["other-new"] = 2
                    state["steers"].append({"id": "other-new-steer", "text": "other new",
                                            "task": "other-new"})
                await hub._update_step(key, advance)
                trace["after_revision_commit"] = snapshot()
        return attached

    monkeypatch.setattr(hub, "_attach_steers", attach_then_advance)
    try:
        await hub.store.aput("step_control", key, {
            "revision": 1 if scenario == "stale_before_attach" else 0,
            "last_task": "previous", "dropped": 5, "dropped_told": 2,
            "drop_reservations": {"other": 2},
            "steers": [{"id": "pending", "text": "PI instruction", "task": None},
                       {"id": "other-steer", "text": "other instruction", "task": "other"}]})
        old = await hub.dispatch(task("old", 0))
        after_old = snapshot()
        trace.update(scenario=scenario, old_result=old.model_dump(mode="json"),
                     old_task_exists=hub.store.get("task", "old") is not None,
                     old_future_exists="old" in hub.futures,
                     old_frames=[f for f in frames if (f.get("task") or {}).get("id") == "old"],
                     after_old_dispatch=after_old)
        restored = StateStore(hub.store.path)
        try:
            trace["reopened_row_matches"] = restored.get("step_control", key) == hub.store.get("step_control", key)
        finally:
            restored.close()
        await hub.dispatch(task("next", 1))
        next_frame = next(f for f in frames if (f.get("task") or {}).get("id") == "next")
        trace["next_payload_meta"] = next_frame["task"]["meta"]
        trace["after_next_dispatch"] = snapshot()
        print("REPRO " + json.dumps(trace, sort_keys=True))
        assert trace["reopened_row_matches"]
        assert after_old["drop_reservations"]["other"] == 2
        assert next(s for s in after_old["steers"] if s["id"] == "other-steer")["task"] == "other"
        if scenario == "no_race":
            assert old.ok and trace["old_task_exists"]
            assert [s["id"] for s in trace["old_attached_meta"]["steers"]] == ["pending"]
            assert trace["old_attached_meta"]["steer_dropped"] == 3
        else:
            assert old.error == "step ended before dispatch"
            assert not trace["old_task_exists"] and not trace["old_future_exists"] and not trace["old_frames"]
            if scenario == "advance_after_attach":
                assert after_old["drop_reservations"]["other-new"] == 2
                assert next(s for s in after_old["steers"] if s["id"] == "other-new-steer")["task"] == "other-new"
            # Desired behavior. Fails on pinned head for the post-attach race.
            assert ([s["id"] for s in trace["next_payload_meta"].get("steers", [])],
                    trace["next_payload_meta"].get("steer_dropped", 0)) == (["pending"], 3)
    finally:
        hub.store.close()
        assert durable.lane(hub.store.lane).shutdown(5)
