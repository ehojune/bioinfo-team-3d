"""Fake signal callbacks and coroutines only; never send signals or launch processes."""
import asyncio
import json
import signal
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from labhq.cli import _run_runner_with_interrupts
from labhq.interruption import watch_shutdown_signals
from labhq.runner.daemon import Runner


@pytest.fixture
def signals(monkeypatch):
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    handlers = {signal.SIGINT: signal.default_int_handler,
                signal.SIGTERM: signal.SIG_DFL, signal.SIGHUP: signal.SIG_DFL}
    monkeypatch.setattr(signal, "getsignal", handlers.__getitem__)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))
    return handlers


@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGHUP"])
@pytest.mark.parametrize("initial", [signal.SIG_DFL, signal.SIG_IGN])
async def test_default_runner_signal_enters_graceful_shutdown(signals, sig_name, initial):
    sig = getattr(signal, sig_name)
    signals[sig] = initial
    original = dict(signals)
    calls = []

    class FakeRunner:
        tasks = {}
        _stopping = False

        async def run_forever(self):
            handler = signals[sig]
            assert callable(handler), "standard TERM/HUP must install a shutdown handler"
            handler(sig, None)
            await asyncio.Event().wait()

        async def shutdown(self):
            assert self._stopping
            calls.append("graceful shutdown")

        def stop(self):
            calls.append("stop")

    diagnostics = SimpleNamespace(attach_loop=Mock(), note_signal=Mock())
    await asyncio.wait_for(_run_runner_with_interrupts(FakeRunner(), diagnostics), 1)
    assert calls == ["graceful shutdown", "stop"]
    diagnostics.note_signal.assert_called_once_with(sig)
    assert signals == original


async def test_custom_handler_is_marked_and_delegated_without_forcing_shutdown(signals):
    calls = []
    runner = SimpleNamespace(_stopping=False, tasks={})

    def custom(signum, frame):
        assert runner._stopping
        calls.append((signum, frame))

    signals[signal.SIGTERM] = custom
    original = dict(signals)

    async def run_forever():
        signals[signal.SIGTERM](signal.SIGTERM, None)

    runner.run_forever = run_forever
    runner.stop = Mock()
    runner.shutdown = Mock(side_effect=AssertionError("custom handler owns shutdown"))
    await _run_runner_with_interrupts(runner)
    assert calls == [(signal.SIGTERM, None)]
    runner.shutdown.assert_not_called()
    assert signals == original


def test_generic_watcher_still_leaves_default_handlers_untouched(signals):
    original = dict(signals)
    restore = watch_shutdown_signals(Mock(), interrupt=False)
    assert signals == original
    restore()
    assert signals == original


@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGHUP"])
async def test_standard_entry_cancels_with_evidence_and_mock_gateway_opens_resume(
        tmp_path, monkeypatch, signals, sig_name, record_property):
    from labhq.models import Task, TaskResult
    from tests.test_shutdown_recovery import make_hub, runner_for, run_request_with_result, resume_cards

    hub = make_hub(tmp_path)
    runner = runner_for(hub, tmp_path)
    runner.connected.set()
    started = asyncio.Event()
    stages = []
    pending_at_shutdown_return = []
    task = Task(id="signal-task", agent_id="cso", request_id="r", prompt="mock work")

    async def work(_task):
        started.set()
        await asyncio.Event().wait()

    async def run_forever():
        runner.tasks[task.id] = asyncio.create_task(runner._run_guarded(task))
        await asyncio.Event().wait()

    async def result_ready():
        while True:
            rows = runner.store.pending()
            results = [row for row in rows if row["type"] == "task.result"]
            if results:
                result = TaskResult.model_validate(results[0]["data"])
                return result, max(r["runner_seq"] for r in rows)
            await asyncio.sleep(.001)

    async def phase(name, awaitable):
        began = time.monotonic()
        try:
            # These are fixture operations, not a new product shutdown deadline. The former
            # three-second gather also covered gateway work and ACK, while shutdown allows five.
            return await asyncio.wait_for(awaitable, 5)
        except asyncio.TimeoutError:
            pending = runner.store.pending()
            pytest.fail(f"shutdown phase={name} timed out; prior={stages}; "
                        f"pending_results={sum(row['type'] == 'task.result' for row in pending)}; "
                        f"active_tasks={sum(not job.done() for job in runner.tasks.values())}")
        finally:
            stages.append({"phase": name, "elapsed_s": round(time.monotonic() - began, 4)})

    monkeypatch.setattr(runner, "run_task", work)
    monkeypatch.setattr(runner, "run_forever", run_forever)
    shutdown = runner.shutdown

    async def observed_shutdown():
        await shutdown()
        pending_at_shutdown_return.append(len(runner.store.pending()))

    monkeypatch.setattr(runner, "shutdown", observed_shutdown)
    entry = asyncio.create_task(_run_runner_with_interrupts(runner))
    try:
        await phase("work_ready", started.wait())
        signals[getattr(signal, sig_name)](getattr(signal, sig_name), None)
        result, sequence = await phase("result_durable", result_ready())
        await phase("gateway_resume", run_request_with_result(hub, result, monkeypatch))
        assert not entry.done(), f"runner exited before ACK: {stages}"
        await phase("ack_durable", runner._on_message({"type": "runner.ack", "runner_seq": sequence}))
        await phase("entry_finished", entry)
    finally:
        if not entry.done():
            entry.cancel()
        await asyncio.gather(entry, return_exceptions=True)
        record_property("shutdown_stages", json.dumps(stages))
        print("Shutdown stages: " + json.dumps(stages))
    assert result.shutdown_evidence["stopping"] is True
    assert result.shutdown_evidence["cancelled"] is True
    assert hub.requests["r"]["status"] == "interrupted"
    assert len(resume_cards(hub)) == 1
    assert runner.store.pending() == []
    assert pending_at_shutdown_return == [0], "shutdown returned before durable ACK"


@pytest.mark.parametrize("connected", [True, False])
async def test_shutdown_keeps_unacknowledged_results_and_has_a_bounded_wait(connected):
    pending = [{"type": "task.result"}]
    runner = SimpleNamespace(tasks={}, _dispatches=set(), connected=SimpleNamespace(is_set=lambda: connected),
                             store=SimpleNamespace(pending=lambda: pending), stop=Mock(), _ack_drain=AsyncMock())
    await asyncio.wait_for(Runner.shutdown(runner, timeout_s=.02), 1)
    runner.stop.assert_called_once_with()
    assert pending == [{"type": "task.result"}]


async def test_repeated_signal_starts_only_one_shutdown(signals):
    shutdown = Mock()

    class FakeRunner:
        tasks = {}

        async def run_forever(self):
            signals[signal.SIGTERM](signal.SIGTERM, None)
            signals[signal.SIGTERM](signal.SIGTERM, None)
            await asyncio.Event().wait()

        async def shutdown(self):
            shutdown()

        def stop(self):
            pass

    await asyncio.wait_for(_run_runner_with_interrupts(FakeRunner()), 1)
    shutdown.assert_called_once_with()


async def test_runner_may_finish_while_graceful_shutdown_is_still_draining(signals):
    class FakeRunner:
        tasks = {}

        async def run_forever(self):
            signals[signal.SIGTERM](signal.SIGTERM, None)

        async def shutdown(self):
            await asyncio.sleep(.01)

        def stop(self):
            pass

    await asyncio.wait_for(_run_runner_with_interrupts(FakeRunner()), 1)


@pytest.mark.parametrize("initial", [signal.SIG_DFL, signal.SIG_IGN, "custom"])
async def test_diagnostics_context_preserves_runner_shutdown_disposition(tmp_path, monkeypatch, signals, initial):
    from labhq import process_diagnostics as module

    custom = Mock()
    signals[signal.SIGTERM] = custom if initial == "custom" else initial
    if hasattr(signal, "SIGBREAK"):
        signals[signal.SIGBREAK] = signal.SIG_DFL
    original = dict(signals)
    monkeypatch.setattr(module, "_resources", lambda: {})
    monkeypatch.setattr(module.threading, "Thread", lambda **kw: SimpleNamespace(start=Mock(), join=Mock()))
    monkeypatch.setattr(module.faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(module.faulthandler, "enable", Mock())
    monkeypatch.setattr(module.faulthandler, "disable", Mock())
    graceful = Mock()

    class FakeRunner:
        tasks = {}

        async def run_forever(self):
            signals[signal.SIGTERM](signal.SIGTERM, None)
            if initial != "custom":
                await asyncio.Event().wait()

        async def shutdown(self):
            assert self._stopping
            graceful()

        def stop(self):
            pass

    with module.ProcessDiagnostics(tmp_path / "logs", "runner") as diagnostics:
        installed = dict(signals)
        try:
            await asyncio.wait_for(_run_runner_with_interrupts(FakeRunner(), diagnostics), 1)
        except SystemExit:
            pytest.fail("diagnostics must not turn the default TERM disposition into immediate exit")
        assert signals == installed
    assert signals == original
    assert graceful.call_count == (initial != "custom")
    assert custom.call_count == (initial == "custom")
    assert diagnostics.reason == "signal:SIGTERM"


@pytest.mark.parametrize("stop_at", ["before_dispatch", "during_accept"])
async def test_shutdown_refuses_new_dispatch_even_if_stopping_changes_during_accept(
        tmp_path, monkeypatch, stop_at):
    from labhq.models import Task, TaskResult
    from tests.test_shutdown_recovery import make_hub, runner_for

    runner = runner_for(make_hub(tmp_path), tmp_path)
    task = Task(id="late", agent_id="cso", request_id="r", prompt="must not execute")
    launch = AsyncMock()
    monkeypatch.setattr(runner, "_run_guarded", launch)
    send = runner._send_durably

    async def accept_then_stop(message, **kwargs):
        await send(message, **kwargs)
        if message["type"] == "task.accepted" and stop_at == "during_accept":
            runner._stopping = True

    monkeypatch.setattr(runner, "_send_durably", accept_then_stop)
    runner._stopping = stop_at == "before_dispatch"
    message = {"type": "task.dispatch", "task": task.model_dump(mode="json")}
    await runner._on_message(message)
    await asyncio.sleep(0)
    launch.assert_not_called()
    assert task.id not in runner.tasks
    results = [row for row in runner.store.pending() if row["type"] == "task.result"]
    assert len(results) == 1
    result = TaskResult.model_validate(results[0]["data"])
    assert not result.ok and result.shutdown_evidence["stopping"] is True
    assert runner.store.get("accepted_task", task.id)["state"] == "finished"
    await runner._on_message(message)  # A duplicate cannot start work or overwrite the shutdown result.
    launch.assert_not_called()
    assert len([row for row in runner.store.pending() if row["type"] == "task.result"]) == 1
