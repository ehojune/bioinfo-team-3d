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
from tests.support.serve_task import serve_task, wait_started

REPO = Path(__file__).resolve().parents[1]
STALL_S = 0.5      # one slow fsync
PROBE_LIMIT_S = 0.2


class BlockedDisk:
    """Hold the first fsync until the test explicitly releases it."""

    def __init__(self, monkeypatch):
        self.real = os.fsync
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        monkeypatch.setattr(os, "fsync", self)

    def __call__(self, fd):
        with self.lock:
            block = self.calls == 0
            self.calls += 1
        if block:
            self.entered.set()
            if not self.release.wait(30):
                raise TimeoutError("test did not release the blocked fsync")
        return self.real(fd)


def _probe_while_write_is_blocked(url, disk, finished, out):
    """Send one health request after fsync entered and record whether it answered before release."""
    if not disk.entered.wait(30):
        out.append("the file write never reached fsync")
        disk.release.set()
        finished.set()
        return
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(url)
            response.raise_for_status()
            out.append(response.status_code)
    except Exception as exc:  # noqa: BLE001 - the assertion reports an unanswered probe
        out.append(f"{type(exc).__name__}: {exc}")
        disk.release.set()  # let a blocking implementation unwind instead of hanging the test
    finally:
        finished.set()


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
    tasks = [serve_task(server.serve())]
    await wait_started(lambda: server.started, tasks[0], 30, "gateway start")
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    disk = prober = None
    try:
        await _until(lambda: "biologist" in hub.agents, 30, "runner registration")
        probe_finished, probe_result = threading.Event(), []
        disk = BlockedDisk(monkeypatch)
        prober = threading.Thread(
            target=_probe_while_write_is_blocked,
            args=(f"http://127.0.0.1:{port}/api/health", disk, probe_finished, probe_result),
            daemon=True,
        )
        prober.start()
        rid = hub.create_request(RequestIn(text="CD276 요약", mode="direct", agent_id="biologist"))
        assert await asyncio.to_thread(probe_finished.wait, 15), "the health probe did not finish"
        assert probe_result == [200], f"/api/health did not answer while the file write was blocked: {probe_result}"
        assert disk.entered.is_set(), "the file write never reached fsync"
        assert not disk.release.is_set(), "the health response arrived only after the file write was released"
        disk.release.set()
        await _until(lambda: hub.requests[rid]["status"] in {"done", "failed"}, 120, "the request to end")
        assert hub.requests[rid]["status"] == "done"
    finally:
        if disk is not None:
            disk.release.set()
        if prober is not None:
            prober.join(10)
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
