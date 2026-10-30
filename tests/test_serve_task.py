import asyncio
import logging
import socket

import pytest
import uvicorn

from tests.support.serve_task import serve_task, wait_started


async def _app(scope, receive, send):
    pass


def _server(port: int) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))


@pytest.fixture
def taken_port():
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    yield holder.getsockname()[1]
    holder.close()


async def test_a_failed_startup_ends_only_that_test_with_the_cause(taken_port):
    server = _server(taken_port)
    task = serve_task(server.serve())
    with pytest.raises(RuntimeError) as failed:
        await wait_started(lambda: server.started, task, 10)
    assert "exit code 3" in str(failed.value)
    assert "address" in str(failed.value).lower() or "socket" in str(failed.value).lower()
    await asyncio.sleep(0)  # the loop is alive: a bare task SystemExit would have left it
    assert task.done() and isinstance(task.exception(), RuntimeError)


async def test_a_normal_server_starts_and_stops_cleanly():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = _server(port)
    task = serve_task(server.serve())
    await wait_started(lambda: server.started, task, 10)
    assert not task.done()
    server.should_exit = True
    await asyncio.wait_for(task, 10)
    assert task.exception() is None


async def test_a_serve_that_exits_without_a_log_line_still_says_so():
    async def broker_serve():
        raise SystemExit(5)

    task = serve_task(broker_serve())
    with pytest.raises(RuntimeError, match="exit code 5: no uvicorn error line was logged"):
        await wait_started(lambda: False, task, 10)


async def test_wait_started_gives_up_with_a_timeout_when_nothing_happens():
    async def never():
        await asyncio.sleep(30)

    task = serve_task(never())
    try:
        with pytest.raises(asyncio.TimeoutError, match="did not start within 0.1 s"):
            await wait_started(lambda: False, task, 0.1, "gateway")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_log_handler_is_removed_when_the_task_ends():
    logger = logging.getLogger("uvicorn.error")
    before = list(logger.handlers)

    async def done():
        return None

    task = serve_task(done())
    await task
    await asyncio.sleep(0)
    assert logger.handlers == before
