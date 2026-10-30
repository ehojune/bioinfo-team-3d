"""Run `server.serve()` (or `broker.serve()`) as a task that cannot kill the test worker (#1113).

uvicorn ends a failed startup, for example a port another process took, with `sys.exit(3)`. As the SystemExit of a task
it leaves the event loop and takes the next tests of the same worker down with it (146 of them in PR #1110's run). Use
`serve_task` where a test wrote `asyncio.create_task(server.serve())`, and `wait_started` where it waited for
`server.started`:

    tasks = [serve_task(server.serve())]
    await wait_started(lambda: server.started, tasks[0], 10)

A failed startup then ends only that test, with the cause from uvicorn's own log in the error.
"""

import asyncio
import collections
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any


class _Tail(logging.Handler):
    """The last few uvicorn error lines, so a failed startup says why (`address already in use`)."""

    def __init__(self, lines: int):
        super().__init__(level=logging.ERROR)
        self.lines: collections.deque[str] = collections.deque(maxlen=lines)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage().strip().splitlines()[0] if record.getMessage().strip() else "")


async def _serve(target: Coroutine[Any, Any, Any], tail: _Tail) -> None:
    try:
        await target
    except SystemExit as exit_:
        cause = " | ".join(line for line in tail.lines if line) or "no uvicorn error line was logged"
        raise RuntimeError(f"uvicorn stopped with exit code {exit_.code}: {cause}") from None


def serve_task(target: Coroutine[Any, Any, Any], *, tail_lines: int = 4) -> asyncio.Task:
    """`asyncio.create_task(target)` for a uvicorn serve coroutine, with SystemExit turned into a RuntimeError."""
    tail = _Tail(tail_lines)
    logger = logging.getLogger("uvicorn.error")
    logger.addHandler(tail)
    task = asyncio.create_task(_serve(target, tail))
    task.add_done_callback(lambda _: logger.removeHandler(tail))
    return task


async def wait_started(is_started: Callable[[], bool], task: asyncio.Task, timeout: float = 10,
                       what: str = "server") -> None:
    """Wait until `is_started()`; raise the task's error as soon as it dies, instead of waiting out the timeout."""
    deadline = time.monotonic() + timeout
    while not is_started():
        if task.done():
            task.result()  # the RuntimeError from a failed startup
            raise RuntimeError(f"{what} stopped before it started")
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError(f"{what} did not start within {timeout} s")
        await asyncio.sleep(0.02)


async def wait_event(event: asyncio.Event, task: asyncio.Task, timeout: float, what: str = "server") -> None:
    """Wait for `event`, but raise the serving task's error as soon as it dies (the event would never be set)."""
    waiting = asyncio.ensure_future(event.wait())
    try:
        done, _ = await asyncio.wait({waiting, task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not waiting.done():
            waiting.cancel()
    if waiting in done:
        return
    if task in done:
        task.result()
        raise RuntimeError(f"{what} stopped before the event was set")
    raise asyncio.TimeoutError(f"{what}: the event was not set within {timeout} s")
