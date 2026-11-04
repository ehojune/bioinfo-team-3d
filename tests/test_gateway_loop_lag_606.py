"""#606: a runner's task messages and a PI translation reserve their rows without holding the event loop.

A SQLite commit that waits for its fsync used to run on the loop thread (`store.put` -> `durable.call` waits for the
writer thread). The writer thread is slowed here, so the loop is late by that long whenever it waits for it.
"""
import asyncio
import json
import time

import pytest

from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from test_wsl_setup import settings  # noqa: F401  (the fixture)

# A loop held by the commit is late by about DELAY_S; a busy shared runner makes it late by 0.2 s at most (0.196 s was
# seen on CI). The two have to be far apart, or the limit measures the runner instead of the code (#1076).
DELAY_S = 0.6
LIMIT_S = DELAY_S / 2


def slow_commits(store, delay=DELAY_S):
    """The store's writes take `delay` on the writer thread, as a slow disk's fsync would."""
    for name in ("_put_now", "_delete_now"):
        original = getattr(store, name)

        def slow(*args, _original=original, **kwargs):
            time.sleep(delay)
            return _original(*args, **kwargs)

        setattr(store, name, slow)


class LoopLag:
    """The most the event loop was late while this ran (a 5 ms tick that comes back later than that)."""

    def __init__(self):
        self.worst = 0.0
        self._task = None

    async def __aenter__(self):
        async def tick():
            while True:
                before = time.perf_counter()
                await asyncio.sleep(0.005)
                self.worst = max(self.worst, time.perf_counter() - before - 0.005)

        self._task = asyncio.create_task(tick())
        await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


class Socket:
    def __init__(self, dispatched):
        self.dispatched = dispatched

    async def send_text(self, payload):
        if json.loads(payload)["type"] == "task.dispatch":
            self.dispatched.set()


@pytest.mark.asyncio
async def test_a_runners_accepted_and_result_messages_do_not_hold_the_loop(settings):  # noqa: F811
    hub = Hub(settings)
    dispatched = asyncio.Event()
    hub.register_runner("lab", Socket(dispatched), [{"id": "analyst"}], incarnation="lab-1")
    task = Task(agent_id="analyst", prompt="analysis")
    pending = asyncio.create_task(hub.dispatch(task))
    await asyncio.wait_for(dispatched.wait(), 5)
    await asyncio.sleep(0.2)  # the dispatch has recorded the task and sent it
    slow_commits(hub.store)
    async with LoopLag() as lag:
        await hub.on_runner_message("lab", {"type": "task.accepted", "task_id": task.id})
        await hub.on_runner_message("lab", {"type": "task.result", "task_id": task.id,
            "data": TaskResult(task_id=task.id, agent_id="analyst", ok=True).model_dump(mode="json")})
    assert lag.worst < LIMIT_S, f"the loop was late by {lag.worst:.3f}s while a commit took {DELAY_S}s"
    assert (await asyncio.wait_for(pending, 5)).ok
    row = hub.store.get("task", task.id)
    assert row["accepted"] is True and row["completed"] is True, "the writes still land, in order"


@pytest.mark.asyncio
async def test_a_translation_is_reserved_without_holding_the_loop_and_only_once(settings):  # noqa: F811
    hub = Hub(settings)
    event = {"type": "agent.log", "seq": 1, "ts": 1.0, "request_id": "r", "agent_id": "analyst",
             "data": {"text": "표 확인 중입니다.\n```python\nprint(1)\n```"}}
    calls = []

    async def interpret(self_event, source):
        calls.append(source)

    hub.pi_interpreter._interpret = interpret
    slow_commits(hub.store)
    async with LoopLag() as lag:
        for _ in range(3):  # the same message three times
            hub.pi_interpreter.schedule(event)
        await asyncio.sleep(0.01)
        assert not calls, "the paid call waits for the reservation"
        await asyncio.gather(*list(hub.pi_interpreter.tasks))
    assert lag.worst < LIMIT_S, f"the loop was late by {lag.worst:.3f}s while a commit took {DELAY_S}s"
    assert len(calls) == 1, "one reservation, one paid call"
    assert hub.store.get("pi_translation", "1")["status"] == "pending"


@pytest.mark.asyncio
async def test_a_runner_task_start_does_not_hold_the_loop_for_its_file_system_work(tmp_path, monkeypatch):
    """The workspace folder, the staff skills copy and the private-path resolution used to run on the loop thread."""
    from labhq.models import AgentSpec, Engine
    from labhq.runner import daemon
    from labhq.runner.daemon import Runner
    from labhq.settings import Settings

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)

    class Adapter:
        async def run(self, ctx):
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    slowed = []

    def slow(name):
        original = getattr(daemon, name)

        def wrapper(*args, **kwargs):
            slowed.append(name)
            time.sleep(DELAY_S)
            return original(*args, **kwargs)

        monkeypatch.setattr(daemon, name, wrapper)

    for name in ("TaskWorkspace", "install_required_skills", "resolve_private_paths"):
        slow(name)
    task = Task(agent_id="cso", prompt="hello", meta={})
    try:
        async with LoopLag() as lag:
            result = await runner.run_task(task)
    finally:
        runner.store.close()
    assert result.ok
    assert {"TaskWorkspace", "install_required_skills"} <= set(slowed), slowed
    assert lag.worst < LIMIT_S, (f"the loop was late by {lag.worst:.3f}s while each of {sorted(set(slowed))} "
                                 f"took {DELAY_S}s")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["install_required_skills", "TaskWorkspace"])
async def test_a_cancel_waits_for_the_file_system_work_that_changes_the_workspace(tmp_path, monkeypatch, name):
    """A cancelled step's worker thread cannot be interrupted: the cancel must not be reported before it has finished,
    or a retry in the same folder races a skill copy that is still replacing folders."""
    from labhq.models import AgentSpec, Engine
    from labhq.runner import daemon
    from labhq.runner.daemon import Runner
    from labhq.settings import Settings

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: object())
    original = getattr(daemon, name)
    entered, finished = asyncio.Event(), []
    loop = asyncio.get_running_loop()

    def slow(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        time.sleep(0.4)
        result = original(*args, **kwargs)
        finished.append(name)
        return result

    monkeypatch.setattr(daemon, name, slow)
    running = asyncio.create_task(runner.run_task(Task(agent_id="cso", prompt="hello", meta={})))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        assert finished == [name], "the cancel was reported while the worker was still changing the workspace"
    finally:
        runner.store.close()
