"""The order of pause/resume frames on the wire is the order the PI's intent was decided in (#1190, #1147 ②).

The runner applies the last pause/resume it receives, so a stale frame arriving after a newer one would override it. That
does not happen today because of two facts, and these tests fail if either changes:

1. `send_runner` takes `runner_locks[runner_id]`, a FIFO lock: frames leave in the order their senders queued up.
2. `_push_task_state` reads the step state and reaches `send_runner` with no event-loop turn in between, so nothing the
   PI does after the read can be queued ahead of the frame that carries the read.
No sequence number is added on purpose: it would cost a new message field and runner state for a failure that cannot
happen while these two hold.
"""
import asyncio
import random

import pytest

from test_staff_control_review import _hub
from test_staff_control_steps import FakeWs

ROSTER = [{"id": "worker", "engine": "codex"}]


def registered(tmp_path, ws=None):
    hub = _hub(tmp_path)
    ws = ws or FakeWs()
    hub.register_runner("pc-a", ws, ROSTER, "inc-1", {}, "proc-1")
    return hub, ws


def controls_on_the_wire(ws):
    return [frame["action"] for frame in ws.frames
            if frame.get("type") == "task.control" and frame.get("action") in {"pause", "resume"}]


def gateway_paused(hub):
    key = hub._entry_step("t1", hub.store.get("task", "t1"))
    return bool(hub._step(key)["paused"])


@pytest.mark.asyncio
async def test_send_runner_sends_frames_in_the_order_they_queued(tmp_path):
    """Fact 1: with the first send stuck, a second one waits behind it instead of overtaking it."""
    ws = FakeWs()
    release = asyncio.Event()
    real = ws.send_text
    stuck = {"first": True}

    async def send_text(payload):
        if stuck.pop("first", False):
            await release.wait()
        await real(payload)

    ws.send_text = send_text
    hub, _ = registered(tmp_path, ws)
    first = asyncio.ensure_future(hub.send_runner("pc-a", {"type": "task.control", "action": "pause", "n": 1}))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(hub.send_runner("pc-a", {"type": "task.control", "action": "resume", "n": 2}))
    for _ in range(5):
        await asyncio.sleep(0)
    assert ws.frames == [], "the second frame must not leave while the first is still being sent"
    release.set()
    await asyncio.gather(first, second)
    assert [frame["n"] for frame in ws.frames] == [1, 2]
    hub.store.close()


@pytest.mark.asyncio
async def test_a_state_snapshot_reaches_the_send_queue_in_the_same_loop_turn_it_read_the_state(tmp_path):
    """Fact 2: no event-loop turn passes between reading the step state and entering `send_runner`."""
    hub, ws = registered(tmp_path)
    await hub.control_task("t1", "pause")
    ws.frames.clear()
    turns = {"now": 0}
    stop = asyncio.Event()

    async def ticker():  # counts event-loop turns: any await in between makes the two numbers differ
        while not stop.is_set():
            turns["now"] += 1
            await asyncio.sleep(0)

    reads, entries = [], []
    real_step, real_send = hub._step, hub.send_runner

    def step(key):
        reads.append(turns["now"])
        return real_step(key)

    async def send_runner(runner_id, message):
        if message.get("action") in {"pause", "resume"}:
            entries.append(turns["now"])
        return await real_send(runner_id, message)

    hub._step, hub.send_runner = step, send_runner
    tick = asyncio.ensure_future(ticker())
    await asyncio.sleep(0)
    await hub._push_task_state("t1")
    stop.set()
    await tick
    assert controls_on_the_wire(ws) == ["pause"] and entries, "the snapshot must carry the paused state"
    assert entries[0] == reads[0], "an await was added between reading the state and sending it"
    hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(25))
async def test_the_last_frame_on_the_wire_matches_the_final_state_whatever_the_interleaving(tmp_path, seed):
    """Pause, resume and snapshot re-sends racing each other, with the wire slowed by random yields."""
    rng = random.Random(seed)
    hub, ws = registered(tmp_path)
    real = ws.send_text

    async def slow(payload):
        for _ in range(rng.randint(0, 4)):
            await asyncio.sleep(0)
        await real(payload)

    ws.send_text = slow
    await hub.control_task("t1", "pause")
    ws.frames.clear()
    work = [hub.control_task("t1", "resume"), hub._push_task_state("t1"), hub.control_task("t1", "pause"),
            hub._push_task_state("t1"), hub.control_task("t1", "resume")]
    rng.shuffle(work)
    await asyncio.gather(*work, return_exceptions=True)
    frames = controls_on_the_wire(ws)
    assert frames, "something must have been sent"
    assert (frames[-1] == "pause") == gateway_paused(hub), f"stale frame last: {frames}"
    hub.store.close()
