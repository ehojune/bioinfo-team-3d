"""No processes, stores, threads or event loops: disabled HPC is not mock HPC."""
import copy
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq.runner import daemon
from labhq.runner.daemon import Runner
from labhq.settings import HpcSettings
from labhq.tools import scheduler
from labhq.tools.scheduler import JobInfo, Scheduler


def finish(coroutine):
    """Drive immediate fake coroutines without creating an asyncio loop or thread."""
    try:
        with pytest.raises(StopIteration):
            coroutine.send(None)
    finally:
        coroutine.close()


@pytest.mark.parametrize("ssh_host", [None, "unused-host.invalid"])
@pytest.mark.parametrize("operation", ["submit", "submit_args", "queue", "status", "cancel", "_run"])
def test_none_operations_fail_before_command_or_ssh(monkeypatch, operation, ssh_host):
    run = Mock(return_value=CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(scheduler.subprocess, "run", run)
    engine = Scheduler(HpcSettings(scheduler="none", user="fake-user", ssh_host=ssh_host))
    args = {
        "submit": ("job.sh", "fake-job"),
        "submit_args": ("job.sh", "fake-job", 1, "4G", "01:00:00", None, "out", "err"),
        "queue": (), "status": ("123",), "cancel": ("123",), "_run": (["qstat"],),
    }
    with pytest.raises(RuntimeError, match="not supported for scheduler=none"):
        getattr(engine, operation)(*args[operation])
    run.assert_not_called()


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_none_reports_disabled_before_job_id_validation(monkeypatch, operation):
    run = Mock()
    monkeypatch.setattr(scheduler.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="not supported for scheduler=none"):
        getattr(Scheduler(HpcSettings(scheduler="none", user="fake-user")), operation)("--all")
    run.assert_not_called()


@pytest.mark.parametrize("backend", ["sge", "pbs", "slurm"])
def test_enabled_backend_still_builds_submits_and_cancels(monkeypatch, backend):
    engine = Scheduler(HpcSettings(scheduler=backend, user="fake-user"))
    calls = []

    def run(args):
        calls.append(args)
        return CompletedProcess(args, 0, "123\n" if args[0] in {"qsub", "sbatch"} else "", "")

    monkeypatch.setattr(engine, "_run", run)
    assert engine.submit("job.sh", "fake-job") == "123"
    assert engine.cancel("123") == ""
    assert calls[0][0] == ("sbatch" if backend == "slurm" else "qsub")
    assert calls[1] == ["scancel" if backend == "slurm" else "qdel", "123"]


@pytest.mark.parametrize("backend", ["sge", "pbs", "slurm"])
def test_enabled_backend_queue_and_status_still_parse_fake_output(monkeypatch, backend):
    engine = Scheduler(HpcSettings(scheduler=backend, user="fake-user"))
    if backend == "sge":
        queue = "123 0.5 fake-job fake-user r 10/21/2026 12:00:00 all.q@node 1\n"
    elif backend == "pbs":
        queue = "123.server fake-user batch fake-job 1 1 1 4gb 01:00:00 R 00:00:01\n"
    else:
        queue = "123|RUNNING|None|fake-job\n"
    calls = []

    def run(args):
        calls.append(args)
        output = "Job Id: 123\n    job_state = R\n" if "-f" in args else queue
        return CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(engine, "_run", run)
    assert engine.queue()[0].state == "running"
    assert engine.status("123").state == "running"
    assert len(calls) == 2


def test_mock_remains_explicit_simulation_without_commands(monkeypatch):
    run = Mock()
    monkeypatch.setattr(scheduler.subprocess, "run", run)
    engine = Scheduler(HpcSettings(scheduler="mock", user="fake-user"))
    assert engine.submit("job.sh", "fake-job").startswith("mock-")
    assert engine.queue() == []
    assert engine.status("mock-123").state == "completed"
    assert engine.cancel("mock-123") == "cancelled (mock)"
    run.assert_not_called()


def fake_runner(backend):
    # Do not call Runner.__init__: no Settings/default HOME, StateStore or native startup.
    runner = object.__new__(Runner)
    runner.s = SimpleNamespace(hpc=SimpleNamespace(scheduler=backend, submit_prefix=[]),
                               runner=SimpleNamespace(id="fake-local", job_poll_s=60))
    runner._stopping = False
    runner.jobs = {
        "123": {"job_id": "123", "terminal": False, "state": "missing", "missing": 2,
                "task_id": "held-task", "agent_id": "analyst", "name": "saved-job"},
        "124": {"job_id": "124", "terminal": True, "state": "completed", "missing": 0,
                "task_id": "finished-task", "agent_id": "analyst", "name": "saved-terminal"},
    }
    runner.scheduler = SimpleNamespace(status=Mock(return_value=JobInfo("123", "completed", exit_status=0)))
    runner.store = SimpleNamespace(put=Mock())
    runner.tasks = {}
    runner.task_req = {"held-task": "saved-request"}
    runner.notified = set()
    runner.emit_all = Mock(side_effect=AssertionError("must not emit or terminalize saved jobs"))
    return runner


def test_none_direct_poll_preserves_all_records_and_notifications(monkeypatch):
    runner = fake_runner("none")
    before = copy.deepcopy((runner.jobs, runner.task_req, runner.notified))

    async def fake_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(daemon.asyncio, "to_thread", fake_thread)
    finish(runner._poll_jobs())
    assert (runner.jobs, runner.task_req, runner.notified) == before
    runner.scheduler.status.assert_not_called()
    runner.store.put.assert_not_called()
    runner.emit_all.assert_not_called()


def test_none_direct_watcher_neither_sleeps_nor_polls(monkeypatch):
    runner = fake_runner("none")
    seen = []

    async def sleep(seconds):
        seen.append("sleep")
        runner._stopping = True  # Bound the old implementation's loop for a safe red test.

    async def poll():
        seen.append("poll")

    monkeypatch.setattr(daemon.asyncio, "sleep", sleep)
    monkeypatch.setattr(runner, "_poll_jobs", poll)
    finish(runner._job_watch_loop())
    assert seen == []


@pytest.mark.parametrize("backend", ["none", "sge", "pbs", "slurm", "mock"])
def test_run_forever_keeps_local_services_but_only_schedules_enabled_watcher(monkeypatch, backend):
    runner = fake_runner(backend)
    seen = []
    runner._check_job_group = Mock()
    runner._check_data_boundary = Mock()
    runner.registry = SimpleNamespace(load=Mock(), agents={})

    async def service():
        seen.append("broker")

    async def connection():
        seen.append("connection")

    async def watcher():
        seen.append("watcher")

    async def gather(*coroutines):
        for coroutine in coroutines:
            await coroutine

    async def drain(deadline):
        seen.append("drain")

    runner.broker = SimpleNamespace(serve=service)
    monkeypatch.setattr(runner, "_connection_loop", connection, raising=False)
    monkeypatch.setattr(runner, "_job_watch_loop", watcher)
    monkeypatch.setattr(daemon.asyncio, "gather", gather)
    monkeypatch.setattr(daemon.durable, "adrain", drain)
    finish(runner.run_forever())
    assert seen == ["broker", "connection", *([] if backend == "none" else ["watcher"]), "drain"]
    runner.registry.load.assert_called_once_with()
    runner.scheduler.status.assert_not_called()
    runner.store.put.assert_not_called()
