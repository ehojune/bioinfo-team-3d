"""#606: a slow disk must not freeze the gateway or the runner.

Every durable write ends in fsync. These tests make the disk slow and watch the process from outside. They use only
the public pieces (gateway app, runner, request), so the same file can be run against any revision.
"""
import asyncio
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from labhq.gateway.server import RequestIn, create_app
from labhq.models import Event
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port

REPO = Path(__file__).resolve().parents[1]
STALL_S = 0.5      # one slow fsync
PROBE_LIMIT_S = 0.2


class SlowDisk:
    """os.fsync patched: the first `stalls` calls sleep before they flush, like a disk behind a scanner."""

    def __init__(self, monkeypatch, seconds, stalls):
        self.real, self.seconds, self.stalls = os.fsync, seconds, stalls
        self.lock = threading.Lock()
        self.windows: list[tuple[float, float]] = []
        monkeypatch.setattr(os, "fsync", self)

    def __call__(self, fd):
        with self.lock:
            stall = len(self.windows) < self.stalls
            start = time.perf_counter()
            if stall:
                self.windows.append((start, start + self.seconds))
        if stall:
            time.sleep(self.seconds)
        return self.real(fd)


def _probe(url, stop, out):
    """What a browser or `labhq status` sees: a blocking client in its own thread."""
    with httpx.Client(timeout=30) as client:
        while not stop.is_set():
            start = time.perf_counter()
            try:
                client.get(url).raise_for_status()
                out.append((start, time.perf_counter() - start))
            except Exception:  # noqa: BLE001 - a failed probe is an unanswered one
                out.append((start, float("inf")))
            time.sleep(0.02)


def _settings(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    port = free_port()
    s.gateway.port, s.gateway.url = port, f"ws://127.0.0.1:{port}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 0.05
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "mock"
    return s, port


async def _until(pred, seconds, what):
    end = time.monotonic() + seconds
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


async def test_health_answers_while_the_runner_writes_through_a_slow_disk(tmp_path, monkeypatch):
    s, port = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 30, "gateway start")
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    stop, probes = threading.Event(), []
    prober = threading.Thread(target=_probe, args=(f"http://127.0.0.1:{port}/api/health", stop, probes), daemon=True)
    try:
        await _until(lambda: "biologist" in hub.agents, 30, "runner registration")
        disk = SlowDisk(monkeypatch, STALL_S, stalls=6)  # the task's first six durable writes are slow
        prober.start()
        rid = hub.create_request(RequestIn(text="CD276 요약", mode="direct", agent_id="biologist"))
        await _until(lambda: hub.requests[rid]["status"] in {"done", "failed"}, 120, "the request to end")
        assert hub.requests[rid]["status"] == "done"
        stop.set()
        prober.join(10)
        assert len(disk.windows) >= 4, "the slow disk was never reached"
        first, last = disk.windows[0][0], disk.windows[-1][1]
        # Probes sent while a slow write was in flight, from the stall's start to its end.
        during = [latency for start, latency in probes if first <= start <= last]
        assert len(during) >= 5, f"too few probes while writes stalled: {len(during)}"
        # A frozen loop answers nothing for the whole stall: a few probes, the first of them taking about STALL_S.
        # A live one answers every probe at once. One probe in twenty may meet a hiccup of the CI machine itself
        # (a real fsync on a shared disk, a starved core) - one 0.41 s outlier among 100 fast probes failed a run -
        # but no probe may take most of a stall.
        slow = [latency for latency in during if latency >= PROBE_LIMIT_S]
        assert max(during) < STALL_S * 0.9 and len(slow) <= max(1, len(during) // 20), (
            f"/api/health was slow while a write was stalled: {len(slow)} of {len(during)} probes took "
            f"{PROBE_LIMIT_S} s or more, the slowest {sorted(round(x, 2) for x in during)[-5:]}")
    finally:
        stop.set()
        runner.stop()
        server.should_exit = True
        _, pending = await asyncio.wait(tasks, timeout=15)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class _SlowCommit(sqlite3.Connection):
    """A commit that takes a while: the transaction stays open, holding the write lock, like a stuck WAL flush."""

    stall = 0.0

    def __exit__(self, *exc):
        time.sleep(type(self).stall)
        return super().__exit__(*exc)


async def test_runner_emit_and_ack_leave_the_loop_free_while_sqlite_commits_stall(tmp_path):
    s, _ = _settings(tmp_path)
    runner = Runner(s)
    store = runner.store
    path = Path(s.path(s.runner.state_dir)) / f"runner-{s.runner.id}.sqlite3"
    slow = sqlite3.connect(path, timeout=5, check_same_thread=False, factory=_SlowCommit)
    if hasattr(store, "_wdb"):  # the connection every write goes through
        store._wdb = slow
    else:  # an older store has one connection for everything
        store.db = slow
    _SlowCommit.stall = STALL_S
    gaps, stop = [], asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    watcher = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    for i in range(3):
        await runner.emit(Event(type="agent.log", task_id=None, data={"text": f"line {i}"}))
    first = store.pending()[0]["runner_seq"]
    await runner._on_message({"type": "runner.ack", "runner_seq": first})
    elapsed = time.perf_counter() - started
    stop.set()
    await watcher
    assert elapsed >= 3 * STALL_S * 0.9, "the commits were not slow"
    assert [e["data"]["text"] for e in store.pending()] == ["line 1", "line 2"]
    assert max(gaps) < PROBE_LIMIT_S, f"the loop stood still for {max(gaps):.2f} s"
