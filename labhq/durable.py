"""Ordered writer threads for durable writes (#606).

A durable write (`write_owned`, `atomic_write_text`, a SQLite commit) ends in fsync. Run on the asyncio event loop it
freezes the API, the WebSocket and every other request for as long as the disk takes. Here the writes of one storage
target, the workspace files or one SQLite database, run on a single worker thread of their own (a *lane*) fed by a
FIFO queue:

* `await run(lane, fn, ...)` queues `fn` and resumes when it has finished. The caller continues only after the write
  is on disk, so "written before the next step" holds exactly as before; the loop serves other work meanwhile.
* A lane is FIFO and one thread drains it, so two writes to one path (or one database) land in the order they were
  queued, and a read after the `await` sees the write.
* An exception in `fn` is raised in the awaiting caller.
* A cancelled caller does not cancel the write. A queued job always runs: a crash can lose a job that has not run
  yet, never half of one, because each job is itself atomic (temporary file, fsync, replace; one SQLite transaction).
  Writes that must stay together go in one `fn`.
* `call(lane, fn, ...)` is the synchronous twin. With no thread running for the lane (a CLI, a script, a test without
  a loop, or a lane that has been idle) it calls `fn` directly. While the lane has queued work it queues behind it and
  waits, so a synchronous write cannot overtake a queued one.
* Lanes do not wait for each other. One slow file does not hold up a database commit, and the gateway's commits do
  not queue behind the runner's file writes when both run in one process.

Durability is unchanged: no group commit, no delayed fsync. Each write is flushed before its caller continues.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import logging
import queue
import threading
from typing import Any, Callable, TypeVar

log = logging.getLogger("labhq.durable")

T = TypeVar("T")

# The workspace files labhq writes (write_owned, atomic_write_text, manifests). Each SQLite database has its own lane.
FILES = "files"

# How long the exit hook and the shutdown hooks wait for queued writes. A write that long means a stuck disk; exiting
# then is better than hanging, and every finished job is already whole on disk.
EXIT_DRAIN_S = 30.0

# A lane's thread ends after this long without work and starts again on the next write.
IDLE_S = 2.0


class DurableWriter:
    """One lane: a worker thread and its FIFO queue."""

    def __init__(self, name: str = "labhq-durable-writer", idle_s: float = IDLE_S):
        self.name = name
        self.idle_s = idle_s
        self._lock = threading.Lock()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    # ----- state -----
    def active(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def in_writer(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    # ----- queue -----
    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> "concurrent.futures.Future[T]":
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._queue = queue.SimpleQueue()
                self._thread = threading.Thread(target=self._main, args=(self._queue,), name=self.name, daemon=True)
                self._thread.start()
            self._queue.put((fn, args, kwargs, future))
        return future

    def _main(self, jobs: queue.SimpleQueue) -> None:
        while True:
            try:
                item = jobs.get(timeout=self.idle_s)
            except queue.Empty:
                with self._lock:
                    if jobs.empty() and self._queue is jobs:
                        self._thread = None  # the next submit starts a new thread
                        return
                continue
            if item is None:
                return
            fn, args, kwargs, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - the awaiting caller gets it, the thread keeps draining
                future.set_exception(exc)
            else:
                future.set_result(result)

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run `fn` in order with the queued jobs and wait for it. Direct when no thread is running for the lane or
        when already on it (a job that itself writes)."""
        if self.in_writer() or not self.active():
            return fn(*args, **kwargs)
        return self.submit(fn, *args, **kwargs).result()

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Queue `fn` and wait for it without blocking the event loop. One `run` is one job, so writes that must stay
        together (a message and the mark that it was sent) go in one `fn`: a cancelled caller cannot separate them."""
        if self.in_writer():
            return fn(*args, **kwargs)
        inner = asyncio.wrap_future(self.submit(fn, *args, **kwargs), loop=asyncio.get_running_loop())
        inner.add_done_callback(_consume_exception)
        # shield: cancelling the caller leaves the queued job to finish.
        return await asyncio.shield(inner)

    # ----- end of life -----
    def drain(self, timeout: float | None = None) -> bool:
        """Wait until every job queued so far has finished. False when `timeout` ran out first."""
        if not self.active() or self.in_writer():
            return True
        try:
            self.submit(_barrier).result(timeout)
        except concurrent.futures.TimeoutError:
            return False
        return True

    async def adrain(self, timeout: float | None = None) -> bool:
        """`drain` for a coroutine: the loop keeps serving while the queue empties."""
        if not self.active():
            return True
        try:
            await asyncio.wait_for(self.run(_barrier), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def shutdown(self, timeout: float | None = None) -> bool:
        """Finish every queued job, then stop the thread. A later submit starts a new one. False on timeout."""
        with self._lock:
            thread, jobs = self._thread, self._queue
            if thread is None:
                return True
            self._thread = None
            self._queue = queue.SimpleQueue()
            jobs.put(None)  # behind every earlier job
        if thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()


def _barrier() -> None:
    return None


def _consume_exception(future: "asyncio.Future") -> None:
    """The caller gets the exception through `await`; mark it seen so a cancelled caller does not log a warning."""
    if not future.cancelled():
        future.exception()


# ----- the process's lanes -----
_lanes: dict[str, DurableWriter] = {}
_lanes_lock = threading.Lock()


def lane(name: str) -> DurableWriter:
    with _lanes_lock:
        found = _lanes.get(name)
        if found is None:
            found = _lanes[name] = DurableWriter(f"labhq-durable-{name}"[:60])
        return found


def _all() -> list[DurableWriter]:
    with _lanes_lock:
        return list(_lanes.values())


def active() -> bool:
    return any(writer.active() for writer in _all())


def submit(name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> "concurrent.futures.Future[T]":
    return lane(name).submit(fn, *args, **kwargs)


def call(name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return lane(name).call(fn, *args, **kwargs)


async def run(name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await lane(name).run(fn, *args, **kwargs)


def drain(timeout: float | None = None) -> bool:
    """Every lane's queue, as it stands now. `timeout` is for each lane."""
    return all([writer.drain(timeout) for writer in _all()])


async def adrain(timeout: float | None = None) -> bool:
    results = await asyncio.gather(*(writer.adrain(timeout) for writer in _all()))
    return all(results)


def shutdown(timeout: float | None = None) -> bool:
    return all([writer.shutdown(timeout) for writer in _all()])


@atexit.register
def _drain_at_exit() -> None:
    # The writer threads are daemons so a stuck disk cannot hold the exit open, but a normal exit finishes the queues.
    if not shutdown(EXIT_DRAIN_S):
        log.warning("durable writes still pending after %.0f s at exit", EXIT_DRAIN_S)
