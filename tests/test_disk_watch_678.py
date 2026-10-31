"""While a task runs, the runner halts it once when a drive it fills drops below the floor (#678)."""
import asyncio
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import disk_space
from labhq.disk_space import GIB
from labhq.models import Task
from labhq.runner import daemon
from labhq.runner.breaker import stage_text
from labhq.runner.daemon import Runner
from labhq.settings import Settings

Space = namedtuple("Space", "total used free")


def runner_with(tmp_path, *, watch_s=0.01, halt=True, floor=2.0, held=True):
    runner = Runner.__new__(Runner)  # no store, daemon, writer thread or host configuration is opened
    s = Settings()
    s.runner.disk_watch_s, s.runner.disk_watch_halt, s.runner.disk_fail_free_gb = watch_s, halt, floor
    s.runner.state_dir = str(tmp_path / "state")
    runner.s = s
    events, applied = [], []
    live = {"held": held}

    async def emit(event):
        events.append(event)

    async def apply(message, **kw):
        applied.append(message)
        live["held"] = False  # a halted task ends and the controls let go of it
        return {"paused": False, "halt": True, "action": "halt", "applied": True}

    runner.controls = SimpleNamespace(holds=lambda tid: live["held"], apply=apply)
    runner.emit = emit
    return runner, events, applied, live


def task():
    return Task(id="t1", agent_id="a1", request_id="r", prompt="fake; never execute")


AGENT = SimpleNamespace(id="a1")


def free_gib(value):
    return lambda folder: Space(100 * GIB, 0, int(value * GIB))


async def settle(watcher, timeout=2):
    await asyncio.wait_for(asyncio.gather(watcher, return_exceptions=True), timeout)


@pytest.mark.asyncio
async def test_a_drive_below_the_floor_halts_the_task_once(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert applied == [{"task_id": "t1", "action": "halt"}]
    kinds = [e.type for e in events]
    assert kinds == ["agent.log", "agent.control", "agent.breaker"]
    record = events[-1].data
    assert record["signal"] == "disk" and record["enforcement"] == "halt" and record["free_gib"] == 1.0
    assert "episode" not in record  # not a breaker restriction the PI could release
    assert events[1].data["source"] == "disk"
    assert "멈췄어요" in stage_text(record)


@pytest.mark.asyncio
async def test_a_roomy_drive_never_halts(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    monkeypatch.setattr(disk_space, "usage", free_gib(50))
    watcher = asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path]))
    await asyncio.sleep(0.1)
    live["held"] = False  # the task ended
    await settle(watcher)
    assert applied == [] and events == []


@pytest.mark.asyncio
async def test_the_first_low_drive_of_several_is_the_one_named(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    low = tmp_path / "low"

    def usage(folder):
        return Space(100 * GIB, 0, int((0.5 if Path(folder) == low else 50) * GIB))

    monkeypatch.setattr(disk_space, "usage", usage)
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path, low])))
    assert events[-1].data["path"] == low.as_posix()


@pytest.mark.asyncio
async def test_an_unreadable_drive_is_skipped_and_never_halts(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)

    def broken(folder):
        raise OSError("no such drive")

    monkeypatch.setattr(disk_space, "usage", broken)
    watcher = asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path]))
    await asyncio.sleep(0.1)
    live["held"] = False
    await settle(watcher)
    assert applied == [] and events == []


@pytest.mark.asyncio
async def test_with_halting_off_it_only_warns(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path, halt=False)
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert applied == []
    assert [e.type for e in events] == ["agent.log", "agent.breaker"]
    assert events[-1].data["enforcement"] == "off" and "멈추지 않았어요" in stage_text(events[-1].data)


@pytest.mark.asyncio
async def test_a_failed_halt_is_reported_and_does_not_crash_the_watch(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)

    async def broken(message, **kw):
        raise OSError("control file locked")

    runner.controls.apply = broken
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert events[-1].data["enforcement"] == "failed"


@pytest.mark.asyncio
async def test_watch_starts_per_task_only_when_enabled(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")
    assert "t1" in runner._disk_watchers
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")  # a second call does not start a second watcher
    assert len(runner._disk_watchers) == 1
    live["held"] = False
    await settle(runner._disk_watchers["t1"])
    await asyncio.sleep(0)
    assert runner._disk_watchers == {}  # it ends with the task and leaves nothing behind

    for kwargs in ({"watch_s": 0}, {"floor": 0}):
        quiet, *_ = runner_with(tmp_path, **kwargs)
        quiet._start_disk_watch(task(), AGENT, tmp_path / "ws")
        assert quiet.__dict__.get("_disk_watchers", {}) == {}
    fix = Task(id="t2", agent_id="a1", request_id="r", prompt="x", meta={"kind": "facilities_fix"})
    runner._start_disk_watch(fix, AGENT, tmp_path / "ws")
    assert "t2" not in runner._disk_watchers  # clearing a full workspace is what a fix is for


@pytest.mark.asyncio
async def test_the_bulk_data_root_is_watched_when_set(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    runner.s.runner.bulk_data_root = str(tmp_path / "bulk")
    seen = []
    real = runner._disk_watch

    async def spy(task_, agent, folders):
        seen.extend(Path(f).as_posix() for f in folders)
        live["held"] = False

    runner._disk_watch = spy
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")
    await asyncio.sleep(0.05)
    assert (tmp_path / "bulk").as_posix() in seen and (tmp_path / "ws").as_posix() in seen
    assert Path(runner.s.path(runner.s.runner.state_dir)).as_posix() in seen
    assert real is not None and daemon.Runner is Runner
