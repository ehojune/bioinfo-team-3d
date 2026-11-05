"""#606: the durable writer keeps order, raises where it is awaited, drains at shutdown and never leaves half a file."""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from labhq import durable
from labhq.adapters import owned
from labhq.adapters.owned import OwnedPathError, awrite_owned, read_owned, write_owned
from labhq.durable import DurableWriter
from labhq.models import Event
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.store import StateStore
from labhq.util import aatomic_write_text, atomic_write_text
from tests.support.serve_task import serve_task, wait_started

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_writer():
    """Every lane stopped before and after: a test starts from "no writer thread running"."""
    assert durable.shutdown(30)
    yield durable.lane(durable.FILES)
    assert durable.shutdown(30)


class SlowFsync:
    """os.fsync patched to sleep first: the disk behind a scanner."""

    def __init__(self, monkeypatch, seconds):
        self.real, self.seconds, self.count = os.fsync, seconds, 0
        self.threads: set[str] = set()
        monkeypatch.setattr(os, "fsync", self)

    def __call__(self, fd):
        self.count += 1
        self.threads.add(threading.current_thread().name)
        time.sleep(self.seconds)
        return self.real(fd)


class BlockedFsync:
    """Hold the first fsync until the test explicitly releases it."""

    def __init__(self, monkeypatch):
        self.real = os.fsync
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
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


# ----- the queue itself -----

def test_jobs_run_in_submission_order_on_one_thread(fresh_writer):
    seen = []
    futures = [durable.submit(durable.FILES, lambda i=i: (seen.append(i), threading.current_thread().name)[1]) for i in range(200)]
    names = {f.result(10) for f in futures}
    assert seen == list(range(200))
    assert names == {"labhq-durable-files"}


def test_call_is_direct_without_a_writer_and_queued_behind_one(fresh_writer):
    where = []
    durable.call(durable.FILES, lambda: where.append(threading.current_thread()))
    assert where == [threading.current_thread()]  # no writer running: the caller's own thread
    assert not durable.active()
    durable.submit(durable.FILES, time.sleep, 0.2)  # starts the writer and keeps it busy
    order = []
    durable.submit(durable.FILES, order.append, "queued")
    durable.call(durable.FILES, order.append, "synchronous")
    assert order == ["queued", "synchronous"]  # a synchronous write cannot overtake a queued one


def test_a_job_that_writes_runs_inline_on_the_writer(fresh_writer):
    def outer():
        return durable.call(durable.FILES, lambda: threading.current_thread().name)  # would deadlock if queued behind itself

    assert durable.submit(durable.FILES, outer).result(10) == "labhq-durable-files"


async def test_exception_reaches_the_awaiting_caller_and_the_writer_survives(tmp_path, fresh_writer):
    (tmp_path / "x.txt").mkdir()  # a folder occupies the file's place
    with pytest.raises(OwnedPathError, match="folder occupies"):
        await awrite_owned(tmp_path, "x.txt", "t")
    with pytest.raises(ZeroDivisionError):
        await durable.run(durable.FILES, lambda: 1 / 0)
    with pytest.raises(OwnedPathError):  # the synchronous twin raises the same
        write_owned(tmp_path, "x.txt", "t")
    store = StateStore(tmp_path / "s.sqlite3")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        await store.aput("task", "k", {"a": 1})  # a closed database
    await awrite_owned(tmp_path, "y.txt", "still writing")
    assert read_owned(tmp_path, "y.txt") == "still writing"


async def test_cancelled_caller_does_not_drop_a_queued_write(tmp_path, monkeypatch, fresh_writer):
    SlowFsync(monkeypatch, 0.3)
    task = asyncio.create_task(awrite_owned(tmp_path, "x.txt", "kept"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await durable.adrain(10)
    assert read_owned(tmp_path, "x.txt") == "kept"


# ----- order and visibility -----

async def test_1000_interleaved_writes_keep_their_order(tmp_path, monkeypatch, fresh_writer):
    monkeypatch.setattr(os, "fsync", lambda fd: None)  # order is the point here, not the flush
    real = owned._write_owned_now
    ran = []

    def spy(root, relative, text):
        ran.append((relative, text))
        for attempt in range(40):
            try:
                return real(root, relative, text)
            except OwnedPathError as exc:
                # Windows: a scanner holding the file the previous write just replaced refuses this replace for a
                # few milliseconds (util.replace_file waits it out elsewhere). Retry inside the same job.
                if not isinstance(exc.__cause__, PermissionError) or attempt == 39:
                    raise
                time.sleep(0.05)

    monkeypatch.setattr(owned, "_write_owned_now", spy)
    paths = ["a.txt", "b.txt", "outputs/c.txt", "outputs/d.txt", "e.txt"]
    submitted, tasks = [], []
    for i in range(1000):
        rel = paths[(i * 7 + i // 3) % 5]  # the same path twice in a row now and then, the others interleaved
        submitted.append((rel, str(i)))
        if i % 10 == 9:
            write_owned(tmp_path, rel, str(i))  # a synchronous write from the loop thread, between async ones
        else:
            tasks.append(asyncio.ensure_future(awrite_owned(tmp_path, rel, str(i))))
            await asyncio.sleep(0)  # let it queue before the next one is made
    await asyncio.gather(*tasks)
    assert ran == submitted  # one queue, in call order
    last = {rel: text for rel, text in submitted}
    assert {rel: read_owned(tmp_path, rel) for rel in paths} == last


async def test_a_read_after_an_awaited_write_sees_it(tmp_path, fresh_writer):
    store = StateStore(tmp_path / "s.sqlite3")
    for i in range(150):
        rel = f"f{i % 3}.txt"
        await awrite_owned(tmp_path, rel, f"v{i}")
        assert read_owned(tmp_path, rel) == f"v{i}"
        await aatomic_write_text(tmp_path / "plain.txt", f"p{i}")
        assert (tmp_path / "plain.txt").read_text(encoding="utf-8") == f"p{i}"
        await store.aput("task", f"k{i % 4}", {"i": i})
        assert store.get("task", f"k{i % 4}") == {"i": i}
    row = await store.aenqueue({"type": "x", "n": 1}, 100)
    assert [e["runner_seq"] for e in store.pending()] == [row["runner_seq"]]
    await store.aack(row["runner_seq"])
    assert store.pending() == []


async def test_store_writes_commit_in_call_order(tmp_path, fresh_writer):
    store = StateStore(tmp_path / "s.sqlite3")
    expected, tasks = {}, []
    for i in range(1000):
        key = f"k{(i * 5 + i // 4) % 7}"
        expected[key] = {"i": i}
        if i % 9 == 8:
            store.put("task", key, {"i": i})  # synchronous, queued behind the async ones before it
        else:
            tasks.append(asyncio.ensure_future(store.aput("task", key, {"i": i})))
            await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert store.all("task") == expected
    store.close()
    assert StateStore(tmp_path / "s.sqlite3").all("task") == expected


async def test_loop_stays_responsive_while_fsync_stalls(tmp_path, monkeypatch, fresh_writer):
    # A loop held by an fsync is late by the whole stall; a busy shared runner makes it late by 0.24 s at most (seen on
    # Windows CI), so the stall is long enough that the two do not meet (#1076).
    stall = SlowFsync(monkeypatch, 0.8)
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
    await asyncio.gather(*(awrite_owned(tmp_path, f"f{i}.txt", "x") for i in range(3)))
    await aatomic_write_text(tmp_path / "plain.txt", "x")
    elapsed = time.perf_counter() - started
    stop.set()
    await watcher
    assert stall.count >= 4 and elapsed >= 4 * 0.8 * 0.9  # the writes really were slow
    assert stall.threads == {"labhq-durable-files"}  # and none of the stalls ran on the loop's thread
    assert max(gaps) < 0.4


class SlowCommit(sqlite3.Connection):
    stall = 0.0

    def __exit__(self, *exc):
        time.sleep(type(self).stall)  # inside the open transaction, before the commit
        return super().__exit__(*exc)


async def test_store_reads_do_not_wait_for_a_stalled_commit(tmp_path, fresh_writer):
    path = tmp_path / "s.sqlite3"
    store = StateStore(path)
    for i in range(10):
        store.put("task", f"t{i}", {"i": i})
    SlowCommit.stall = 0.4
    store._wdb = sqlite3.connect(path, factory=SlowCommit, check_same_thread=False, timeout=5)
    writes = [asyncio.ensure_future(store.aput("task", f"n{i}", {"i": i})) for i in range(3)]
    reads = []
    while not all(w.done() for w in writes):
        started = time.perf_counter()
        assert store.get("task", "t3") == {"i": 3}
        store.all("task")
        reads.append(time.perf_counter() - started)
        await asyncio.sleep(0.02)
    assert len(reads) >= 20 and max(reads) < 0.3  # the commits took 1.2 s in all; a read that waited on one would take about that
    assert store.get("task", "n2") == {"i": 2}
    SlowCommit.stall = 0.0


# ----- shutdown -----

def test_shutdown_drains_every_queued_write(tmp_path, monkeypatch, fresh_writer):
    SlowFsync(monkeypatch, 0.02)
    futures = [durable.submit(durable.FILES, owned._write_owned_now, tmp_path, f"f{i}.txt", str(i)) for i in range(30)]
    assert durable.shutdown(30)  # returns after the thread has run all of them
    assert all(f.done() and f.exception() is None for f in futures)
    assert [read_owned(tmp_path, f"f{i}.txt") for i in range(30)] == [str(i) for i in range(30)]
    assert not durable.active()
    durable.call(durable.FILES, owned._write_owned_now, tmp_path, "after.txt", "direct again")  # a later write starts over
    assert read_owned(tmp_path, "after.txt") == "direct again"


async def test_gateway_shutdown_waits_for_queued_writes(tmp_path, monkeypatch, fresh_writer):
    import uvicorn
    from labhq.gateway.server import create_app
    from labhq.util import free_port

    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    port = free_port()
    s.gateway.port = port
    app = create_app(s)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve = serve_task(server.serve())
    await wait_started(lambda: server.started, serve, 15, "gateway")
    assert server.started
    SlowFsync(monkeypatch, 0.05)
    futures = [durable.submit(durable.FILES, owned._write_owned_now, tmp_path, f"f{i}.txt", str(i)) for i in range(20)]
    server.should_exit = True  # the same path SIGTERM takes: Uvicorn runs the app's shutdown hooks
    await asyncio.wait_for(serve, 60)
    assert all(f.done() for f in futures), "the gateway exited with writes still queued"
    assert [read_owned(tmp_path, f"f{i}.txt") for i in range(20)] == [str(i) for i in range(20)]


async def test_runner_shutdown_waits_for_queued_writes(tmp_path, monkeypatch, fresh_writer):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.broker_port = 0
    runner = Runner(s)
    run = asyncio.create_task(runner.run_forever())
    await asyncio.sleep(0.3)
    SlowFsync(monkeypatch, 0.05)
    futures = [durable.submit(durable.FILES, owned._write_owned_now, tmp_path, f"f{i}.txt", str(i)) for i in range(10)]
    runner.stop()
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    assert all(f.done() for f in futures)


async def test_a_runner_whose_broker_port_is_taken_fails_with_an_error_not_a_system_exit(tmp_path, fresh_writer):
    """uvicorn ends a failed start with sys.exit(3). As the SystemExit of the run_forever task it left the event loop and
    ended the rest of an xdist worker's tests (PR #1236's run, #1238): now one test sees a RuntimeError."""
    import socket

    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        s.runner.broker_port = taken.getsockname()[1]
        runner = Runner(s)
        try:
            with pytest.raises(RuntimeError, match="could not start") as raised:
                await asyncio.wait_for(runner.run_forever(), 30)
        finally:
            runner.stop()
    assert str(s.runner.broker_port) in str(raised.value)


# ----- a crash in the middle of the queue -----

def _run_crashing(script: str, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script), *map(str, args)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


FILE_CRASH = """
    import os, sys
    from pathlib import Path
    from labhq import durable
    from labhq.adapters import owned
    root, count, crash_at = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    real, seen = os.fsync, [0]
    def fsync(fd):
        seen[0] += 1
        if seen[0] == crash_at:
            os._exit(17)  # power cut: the temporary file is written, the replace has not happened
        return real(fd)
    os.fsync = fsync
    PAD = "x" * 20000
    for i in range(count):
        durable.submit(durable.FILES, owned._write_owned_now, root, f"f{i % 4}.txt", f"{i:05d}" + PAD)
    durable.drain()
    print("not reached")
"""


@pytest.mark.parametrize("crash_at", [7, 20, 39])
def test_a_crash_mid_queue_leaves_whole_files_and_a_queue_prefix(tmp_path, crash_at):
    count = 40
    proc = _run_crashing(FILE_CRASH, tmp_path, count, crash_at)
    assert proc.returncode == 17, proc.stderr
    contents = {}
    for p in range(4):
        file = tmp_path / f"f{p}.txt"
        if file.exists():
            text = file.read_text(encoding="utf-8")
            assert len(text) == 5 + 20000 and set(text[5:]) == {"x"}, "a file is partly written"
            contents[p] = int(text[:5])
    # The writes that landed are the first K of the queue, whatever K is: nothing later overtook an earlier one.
    fits = [k for k in range(count + 1)
            if contents == {p: max(i for i in range(k) if i % 4 == p) for p in range(4) if any(i % 4 == p for i in range(k))}]
    assert fits and 1 <= fits[0] < count, f"not a prefix of the queue: {contents}"
    write_owned(tmp_path, "f0.txt", "after the crash")  # the leftover temporary file gets in nobody's way
    assert read_owned(tmp_path, "f0.txt") == "after the crash"


STORE_CRASH = """
    import json, os, sys
    from pathlib import Path
    from labhq import durable
    from labhq.store import StateStore
    path, count, crash_at = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    store = StateStore(path)
    original, seen = StateStore._put_now, [0]
    def put_now(self, kind, key, raw):
        seen[0] += 1
        if seen[0] == crash_at:
            conn = self._write_connection()
            conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)", (kind, key, raw))
            os._exit(17)  # inside the open transaction: written, never committed
        return original(self, kind, key, raw)
    StateStore._put_now = put_now
    for i in range(count):
        durable.submit(store.lane, store._put_now, "task", f"k{i:04d}", json.dumps({"i": i}))
    durable.drain()
    print("not reached")
"""


@pytest.mark.parametrize("crash_at", [3, 25])
def test_a_crash_mid_queue_leaves_a_consistent_store(tmp_path, crash_at):
    path = tmp_path / "s.sqlite3"
    proc = _run_crashing(STORE_CRASH, path, 40, crash_at)
    assert proc.returncode == 17, proc.stderr
    store = StateStore(path)
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert sorted(store.all("task")) == [f"k{i:04d}" for i in range(crash_at - 1)]  # the committed prefix, no gap
    assert all(row == {"i": int(key[1:])} for key, row in store.all("task").items())
    store.put("task", "after", {"ok": True})
    assert store.get("task", "after") == {"ok": True}


RUNNER_CRASH = """
    import asyncio, os, sys
    from pathlib import Path
    from labhq.models import Event
    from labhq.runner.daemon import Runner
    from labhq.settings import Settings
    from labhq.store import StateStore
    root, count, crash_at = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    s = Settings()
    s.runner.state_dir = str(root / "state")
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = (
        str(root / "runs"), str(root / "talent"), str(root / "agents"))
    runner = Runner(s)
    runner.store.put("accepted_task", "t-lost", {"state": "running", "task": {"agent_id": "analyst", "request_id": "r1"}})
    original, seen = StateStore._enqueue_now, [0]
    def enqueue_now(self, body, limit):
        seen[0] += 1
        if seen[0] == crash_at:
            conn = self._write_connection()
            conn.execute("INSERT INTO outbox(body) VALUES (?)", ("{}",))
            os._exit(17)
        return original(self, body, limit)
    StateStore._enqueue_now = enqueue_now
    async def main():
        for i in range(count):
            await runner.emit(Event(type="agent.log", data={"text": f"line {i}"}))
    asyncio.run(main())
"""


def test_runner_restart_after_a_crash_finds_a_consistent_outbox(tmp_path):
    proc = _run_crashing(RUNNER_CRASH, tmp_path, 12, 5)
    assert proc.returncode == 17, proc.stderr
    s = Settings()
    s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = (
        str(tmp_path / "runs"), str(tmp_path / "talent"), str(tmp_path / "agents"))
    runner = Runner(s)  # the restart: it finishes what its predecessor accepted
    pending = runner.store.pending()
    logs = [e["data"]["text"] for e in pending if e["type"] == "agent.log"]
    assert logs == [f"line {i}" for i in range(4)]  # the four emitted before the crash, in order, no gap
    lost = [e for e in pending if e["type"] == "task.result"]
    assert [e["task_id"] for e in lost] == ["t-lost"] and lost[0]["data"]["ok"] is False
    assert runner.store.get("accepted_task", "t-lost")["state"] == "finished"
    seqs = [e["runner_seq"] for e in pending]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ----- a caller cancelled while its write is queued -----

async def test_cancelled_emit_still_queues_the_result_and_marks_the_task_finished(tmp_path, fresh_writer):
    s = Settings()
    s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = (
        str(tmp_path / "runs"), str(tmp_path / "talent"), str(tmp_path / "agents"))
    runner = Runner(s)
    runner.store.put("accepted_task", "t1", {"state": "running"})
    path = Path(s.path(s.runner.state_dir)) / f"runner-{s.runner.id}.sqlite3"
    SlowCommit.stall = 0.3
    runner.store._wdb = sqlite3.connect(path, factory=SlowCommit, check_same_thread=False, timeout=5)
    task = asyncio.create_task(runner.emit(Event(type="task.result", task_id="t1", agent_id="a", data={"ok": True})))
    await asyncio.sleep(0.05)
    task.cancel()  # while the result row is still committing
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await durable.adrain(10)
    SlowCommit.stall = 0.0
    assert [e["type"] for e in runner.store.pending()] == ["task.result"]
    assert runner.store.get("accepted_task", "t1") == {"state": "finished"}  # not left "running" with its result gone
    assert runner.outbox.qsize() == 1  # and the send queue was reloaded without the caller


async def test_a_stalled_file_write_does_not_hold_up_a_database_commit(tmp_path, monkeypatch, fresh_writer):
    store = StateStore(tmp_path / "s.sqlite3")
    await store.aput("task", "warm", {"up": True})  # the database's lane is running
    disk = BlockedFsync(monkeypatch)
    stalled = asyncio.ensure_future(awrite_owned(tmp_path, "x.txt", "slow"))
    assert await asyncio.to_thread(disk.entered.wait, 10), "the file write never reached fsync"
    committed = asyncio.Event()

    async def commit_rows():
        store.put("task", "k", {"a": 1})  # a synchronous commit on the loop thread: it waits for nothing else
        await store.aput("task", "k2", {"a": 2})
        committed.set()

    commit_task = asyncio.create_task(commit_rows())
    try:
        await asyncio.wait_for(committed.wait(), 10)
        assert not disk.release.is_set(), "the database commits only finished after the file write was released"
        assert not stalled.done()
        assert store.get("task", "k2") == {"a": 2}
    finally:
        disk.release.set()
        await asyncio.gather(stalled, commit_task)


# ----- a task's closing events survive cancels (review of #615) -----

def _slow_runner(tmp_path, stall):
    from labhq.runner.daemon import Runner  # noqa: F811 - the module-level import is the same class

    s = Settings()
    s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = (
        str(tmp_path / "runs"), str(tmp_path / "talent"), str(tmp_path / "agents"))
    runner = Runner(s)
    path = Path(s.path(s.runner.state_dir)) / f"runner-{s.runner.id}.sqlite3"
    SlowCommit.stall = stall
    runner.store._wdb = sqlite3.connect(path, factory=SlowCommit, check_same_thread=False, timeout=5)
    return runner


async def test_a_second_cancel_while_the_closing_events_commit_still_delivers_the_result_once(
        tmp_path, monkeypatch, fresh_writer):
    from labhq.models import Task

    runner = _slow_runner(tmp_path, 0.3)
    finishes, real_finish = [], runner.controls.finish
    monkeypatch.setattr(runner.controls, "finish", lambda tid: finishes.append(tid) or real_finish(tid))

    async def runs_forever(task):
        await asyncio.sleep(30)

    monkeypatch.setattr(runner, "run_task", runs_forever)
    task = Task(agent_id="analyst", prompt="x", request_id="r1")
    guarded = asyncio.create_task(runner._run_guarded(task))
    await asyncio.sleep(0.05)
    guarded.cancel()  # the first cancel: the handler starts sending the closing events
    await asyncio.sleep(0.1)
    guarded.cancel()  # the second lands while they commit (each commit takes 0.3 s)
    with pytest.raises(asyncio.CancelledError):
        await guarded
    assert await durable.adrain(10)
    SlowCommit.stall = 0.0
    sent = runner.store.pending()
    assert [e["type"] for e in sent] == ["agent.status", "task.result"]  # the gateway is not left waiting
    assert sent[1]["data"]["error"] == "cancelled" and finishes == [task.id]
    assert runner.store.get("accepted_task", task.id) == {"state": "finished"}


async def test_a_cancel_during_the_final_result_does_not_queue_a_second_result(tmp_path, monkeypatch, fresh_writer):
    from labhq.models import Task

    runner = _slow_runner(tmp_path, 0.3)

    async def finishing(task):  # the real result goes out through emit, as in _run_task
        await runner.emit(Event(type="task.result", task_id=task.id, agent_id=task.agent_id,
                                request_id=task.request_id, data={"task_id": task.id, "ok": True, "text": "real"}))

    monkeypatch.setattr(runner, "run_task", finishing)
    task = Task(agent_id="analyst", prompt="x", request_id="r1")
    guarded = asyncio.create_task(runner._run_guarded(task))
    await asyncio.sleep(0.1)
    guarded.cancel()  # while the real result is committing: the commit goes on
    with pytest.raises(asyncio.CancelledError):
        await guarded
    assert await durable.adrain(10)
    SlowCommit.stall = 0.0
    results = [e for e in runner.store.pending() if e["type"] == "task.result"]
    assert len(results) == 1 and results[0]["data"]["ok"] is True and results[0]["data"]["text"] == "real"


# ----- Windows: the write thread's replace meets a reader (review of #615) -----

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows refuses to replace a file another handle has open")


@windows_only
def test_replace_waits_out_a_reader_that_still_has_the_file_open(tmp_path, fresh_writer):
    target = tmp_path / "manifest.json"
    target.write_text("old", encoding="utf-8")
    reader = open(target, "rb")  # no FILE_SHARE_DELETE, like read_owned's own open
    timer = threading.Timer(0.3, reader.close)
    timer.start()
    try:
        write_owned(tmp_path, "manifest.json", "new")  # refused at first; the reader lets go after 0.3 s
    finally:
        timer.join()
        reader.close()
    assert target.read_text(encoding="utf-8") == "new"


@windows_only
def test_read_owned_retries_a_sharing_violation_instead_of_reporting_the_file_missing(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text('{"a": 1}', encoding="utf-8")
    real_open, refused = os.open, []

    def flaky(path, flags, *args, **kwargs):
        if str(path).endswith("manifest.json") and len(refused) < 2:  # a replace is in flight on the write thread
            refused.append(path)
            raise PermissionError(13, "sharing violation")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)
    assert read_owned(tmp_path, "manifest.json") == '{"a": 1}'
    assert len(refused) == 2


# ----- the last review round -----

async def test_a_cancel_between_the_two_contract_copies_cannot_leave_a_partial_hire(tmp_path, monkeypatch, fresh_writer):
    from labhq.models import AgentSpec, ContractInfo, Employment
    from labhq.registry import Registry

    now = time.time()
    spec = AgentSpec(id="c_x", name="X", role="[파견] test", employment=Employment.contract,
                     contract=ContractInfo(repo="https://github.com/a/b", hired_at=now, expires_at=now + 3600,
                                           status="active", talent_dir=str(tmp_path / "talent" / "x")))
    registry = Registry(tmp_path / "agents", tmp_path / "talent", tmp_path / "contracts")
    SlowFsync(monkeypatch, 0.25)
    task = asyncio.create_task(registry.asave_contract(spec))
    await asyncio.sleep(0.1)  # inside the first copy's fsync
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await durable.adrain(10)
    await asyncio.sleep(0)  # the roster update the job asked the loop for
    assert (tmp_path / "contracts" / "c_x.yaml").exists()
    assert (tmp_path / "talent" / "x" / "contract.yaml").exists(), "the talent-pool copy was never written"
    assert registry.agents.get("c_x") is spec and registry.paths.get("c_x") == tmp_path / "contracts" / "c_x.yaml"


def test_two_threads_writing_one_path_on_an_idle_lane_land_in_call_order(tmp_path, monkeypatch, fresh_writer):
    real, flushes, lock = os.fsync, [], threading.Lock()

    def first_flush_is_slow(fd):
        with lock:
            flushes.append(fd)
            first = len(flushes) == 1
        if first:
            time.sleep(0.4)  # the first caller's flush takes long, the second caller's does not
        return real(fd)

    monkeypatch.setattr(os, "fsync", first_flush_is_slow)
    first = threading.Thread(target=write_owned, args=(tmp_path, "manifest.json", "first"))
    second = threading.Thread(target=write_owned, args=(tmp_path, "manifest.json", "second"))
    first.start()
    time.sleep(0.1)  # the second call starts while the first is still flushing
    second.start()
    first.join()
    second.join()
    assert read_owned(tmp_path, "manifest.json") == "second"  # the later call wins, as with a queue


async def test_a_cancel_between_two_result_files_cannot_write_only_one(tmp_path, monkeypatch, fresh_writer):
    from labhq.adapters.owned import awrite_owned_many

    SlowFsync(monkeypatch, 0.25)
    task = asyncio.create_task(awrite_owned_many(tmp_path, [("outputs/RESULT_t.md", "done"),
                                                            ("outputs/RESULT.md", "done")]))
    await asyncio.sleep(0.1)  # inside the first file's fsync
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await durable.adrain(10)
    assert read_owned(tmp_path, "outputs/RESULT_t.md") == "done" and read_owned(tmp_path, "outputs/RESULT.md") == "done"


# ----- #1154: an alias of a file that is fsynced itself is not fsynced again -----

class FsyncedFiles:
    """Which files os.fsync was asked to flush: the temporary file being written is the one named `.<name>.labhq-*.tmp`,
    and it is the only such file in its folder while it is written."""

    def __init__(self, monkeypatch, root):
        self.real, self.root, self.names = os.fsync, Path(root), []
        monkeypatch.setattr(os, "fsync", self)

    def __call__(self, fd):
        self.names.extend(tmp.name.split(".labhq-")[0].lstrip(".") for tmp in self.root.rglob(".*.labhq-*.tmp"))
        return self.real(fd)


def test_a_file_write_fsyncs_by_default_and_an_alias_write_does_not(tmp_path, monkeypatch):
    spy = FsyncedFiles(monkeypatch, tmp_path)
    owned._write_owned_now(tmp_path, "a.txt", "kept")
    assert spy.names.count("a.txt") >= 1  # the default is unchanged: the file is flushed
    before = len(spy.names)
    owned._write_owned_now(tmp_path, "b.txt", "alias", False)
    assert len(spy.names) == before  # no fsync for the alias...
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "kept"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "alias"  # ...but it is written, and by rename:
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["a.txt", "b.txt"]  # no temporary file is left


def test_one_job_fsyncs_only_the_files_that_ask_and_writes_them_all(tmp_path, monkeypatch):
    (tmp_path / "outputs").mkdir()
    spy = FsyncedFiles(monkeypatch, tmp_path)
    paths = owned._write_owned_many_now(tmp_path, [("outputs/RESULT_t1.md", "text"), ("outputs/RESULT.md", "text", False)])
    assert [path.name for path in paths] == ["RESULT_t1.md", "RESULT.md"]
    assert "RESULT_t1.md" in spy.names and "RESULT.md" not in spy.names  # the per-task file is flushed, the alias not
    assert (tmp_path / "outputs" / "RESULT_t1.md").read_text(encoding="utf-8") == "text"
    assert (tmp_path / "outputs" / "RESULT.md").read_text(encoding="utf-8") == "text"


@pytest.mark.asyncio
async def test_a_finished_step_fsyncs_its_per_task_result_and_not_the_alias(tmp_path, monkeypatch):
    from labhq.models import AgentSpec, Engine, Task, TaskResult

    s = Settings()
    s.runner.state_dir = s.gateway.state_dir = str(tmp_path / "state")
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = (
        str(tmp_path / "runs"), str(tmp_path / "talent"), str(tmp_path / "agents"))
    s.hpc.scheduler = "none"
    runner = Runner(s)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, cost_usd=0, text="the answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    spy = FsyncedFiles(monkeypatch, tmp_path / "runs")
    task = Task(id="t1", agent_id="worker", prompt="x")
    await runner.run_task(task)
    assert "RESULT_t1.md" in spy.names and "RESULT.md" not in spy.names
    outputs = runner.workspaces[task.id].dir / "outputs"
    assert (outputs / "RESULT_t1.md").read_text(encoding="utf-8") == "the answer"
    assert (outputs / "RESULT.md").read_text(encoding="utf-8") == "the answer"
