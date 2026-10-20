"""Only fake subprocesses and threads: no browser, SDK, process/proc_tree or network integration."""
import asyncio
from unittest.mock import Mock

import pytest

from labhq.tools import annot_literature as lit, annot_regulatory as reg, external_wait as wait


@pytest.mark.parametrize("cancel_kind", ["deadline", "caller"])
async def test_browser_cancellation_reaps_owned_process_before_return(tmp_path, monkeypatch, cancel_kind):
    stopped = asyncio.Event()
    started = asyncio.Event()

    class FakeProcess:
        returncode = None

        async def wait(self):
            started.set()
            await stopped.wait()
            return self.returncode

        def kill(self):
            self.returncode = -9
            stopped.set()

    proc = FakeProcess()
    proc.kill = Mock(wraps=proc.kill)

    async def spawn(*args, **kwargs):
        assert args[0] == "fixture-browser"
        return proc

    async def legacy_worker(*_args, **_kwargs):
        # Simulate the legacy thread's owned browser staying alive after its waiter is cancelled.
        return await proc.wait()

    monkeypatch.setattr(lit.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(lit.asyncio, "to_thread", legacy_worker)
    transport = lit.BrowserInstitutionTransport(None, None, "fixture-browser")
    destination = tmp_path / "paper.pdf"
    if cancel_kind == "deadline":
        with pytest.raises(wait.LookupTimeout):
            await wait.run_lookup(lambda: transport.fetch("10.1000/key", destination),
                                  tool="key_fulltext", workdir=tmp_path,
                                  timeout_s=.08, clock=lambda: 100.0)
    else:
        task = asyncio.create_task(transport.fetch("10.1000/key", destination))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    proc.kill.assert_called_once_with()
    assert stopped.is_set() and proc.returncode is not None
    assert not destination.exists()


@pytest.mark.parametrize("cancel_kind", ["deadline", "caller", "backend_deadline"])
async def test_cancelled_sdk_thread_counts_toward_stuck_limit(tmp_path, monkeypatch, cancel_kind):
    threads = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            assert daemon
            self.alive = True
            self.target = target
            threads.append(self)

        def start(self):
            pass  # Never run a real thread or SDK call.

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(reg.threading, "Thread", FakeThread)
    monkeypatch.setattr(reg, "_AG_STUCK", [])
    monkeypatch.setattr(reg, "AG_MAX_STUCK", 1)
    monkeypatch.setattr(reg, "AG_TIMEOUT_S", .08 if cancel_kind == "backend_deadline" else 300)
    sdk = Mock(side_effect=AssertionError("the fake thread must never call the SDK"))
    if cancel_kind == "deadline":
        with pytest.raises(wait.LookupTimeout):
            await wait.run_lookup(lambda: reg._alphagenome_call(sdk), tool="alphagenome",
                                  workdir=tmp_path, timeout_s=.08, clock=lambda: 100.0)
    elif cancel_kind == "backend_deadline":
        with pytest.raises(asyncio.TimeoutError):
            await reg._alphagenome_call(sdk)
    else:
        task = asyncio.create_task(reg._alphagenome_call(sdk))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert reg._AG_STUCK == threads
    with pytest.raises(reg.AlphaGenomeUnavailable, match="1개가 아직"):
        await reg._alphagenome_call(sdk)
    assert len(threads) == 1
    sdk.assert_not_called()
    # A finished cancelled thread no longer occupies a slot. Complete the next fake synchronously.
    threads[0].alive = False
    monkeypatch.setattr(FakeThread, "start", lambda self: self.target())
    assert await reg._alphagenome_call(lambda: "mock result") == "mock result"
    assert reg._AG_STUCK == [] and len(threads) == 2


@pytest.mark.parametrize("outcome", ["valid", "invalid", "timeout", "launch_failure"])
async def test_owned_browser_completion_and_local_deadline(tmp_path, monkeypatch, outcome):
    stopped = asyncio.Event()

    class FakeProcess:
        returncode = None

        async def wait(self):
            if outcome == "timeout":
                await stopped.wait()
            else:
                self.returncode = 0
            return self.returncode

        def kill(self):
            self.returncode = -9
            stopped.set()

    proc = FakeProcess()
    proc.kill = Mock(wraps=proc.kill)
    destination = tmp_path / "paper.pdf"

    async def spawn(*args, **kwargs):
        from pathlib import Path
        if outcome == "launch_failure":
            raise OSError("fake launch refusal")
        staged = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--print-to-pdf=")))
        assert staged != destination and staged.parent.parent == tmp_path
        staged.write_bytes((b"%PDF-" if outcome == "valid" else b"wrong") + b"x" * 1100)
        assert not destination.exists()
        return proc

    monkeypatch.setattr(lit.asyncio, "create_subprocess_exec", spawn)
    result = await lit.BrowserInstitutionTransport(None, None, "fixture-browser", timeout_s=.02).fetch(
        "10.1000/key", destination)
    assert result.status == {"valid": "success", "invalid": "session_expired",
                             "timeout": "session_expired", "launch_failure": "unavailable"}[outcome]
    assert destination.exists() == (outcome == "valid")
    assert list(tmp_path.glob(".labhq-institution-*")) == []
    assert proc.kill.call_count == (outcome == "timeout")


async def test_browser_cancellation_during_launch_still_reaps_owned_handle(tmp_path, monkeypatch):
    starting, release = asyncio.Event(), asyncio.Event()

    class FakeProcess:
        returncode = None

        def kill(self):
            self.returncode = -9

        async def wait(self):
            assert self.returncode == -9
            return self.returncode

    proc = FakeProcess()
    proc.kill = Mock(wraps=proc.kill)

    async def spawn(*args, **kwargs):
        starting.set()
        await release.wait()
        return proc

    monkeypatch.setattr(lit.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(lit.BrowserInstitutionTransport(None, None, "fixture-browser").fetch(
        "10.1000/key", tmp_path / "paper.pdf"))
    await starting.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    proc.kill.assert_called_once_with()
    assert list(tmp_path.iterdir()) == []
