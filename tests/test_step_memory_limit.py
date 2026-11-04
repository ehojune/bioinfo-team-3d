"""#709: every staff CLI gets a bounded process tree and a useful failure cause."""
import asyncio
import builtins
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq.adapters.base import AgentAdapter, RunContext
from labhq.models import AgentSpec, Task
from labhq.runner.memory_limit import (
    MEMORY_LIMIT_ENV,
    StepMemoryGuard,
    _cgroup_memory_bytes,
    _exec_with_limit,
    configured_limit_bytes,
    usable_memory_bytes,
)
from labhq.runner import memory_limit
from labhq.settings import Settings


class _ExitAdapter(AgentAdapter):
    engine = "base"

    def __init__(self, settings, command):
        super().__init__(settings)
        self.command = command

    def build_command(self, ctx):
        return self.command

    async def handle_line(self, line, st, ctx):
        st.text_parts.append(line)


def _context(tmp_path: Path, settings: Settings) -> RunContext:
    workdir = tmp_path / "work"
    workdir.mkdir()
    return RunContext(
        task=Task(id="step", agent_id="analyst", prompt="run"),
        agent=AgentSpec(id="analyst", name="Analyst", role="analysis"),
        workdir=workdir,
        settings=settings,
        mcp_servers=[],
        env={},
        emit=lambda *_: asyncio.sleep(0),
        prompt="run",
    )


def test_default_limit_matches_the_available_tree_protection():
    settings = Settings()
    total = usable_memory_bytes()
    limit = configured_limit_bytes(settings.runner)
    assert total > 0 and limit > 0
    expected = 0.60 if os.name == "nt" or memory_limit.posix_tree_memory_limit_supported() else 0.40
    assert abs(limit - int(total * expected)) <= 2 * 1024 * 1024


def test_posix_per_process_fallback_default_is_forty_percent(monkeypatch):
    monkeypatch.setattr(memory_limit, "usable_memory_bytes", lambda: 10 * 1024 ** 3)
    monkeypatch.setattr(memory_limit, "posix_tree_memory_limit_supported", lambda: False)
    assert memory_limit.default_step_memory_limit_gb("posix") == 4.0


def test_zero_disables_limit_and_negative_is_rejected():
    settings = Settings.model_validate({"runner": {"step_memory_limit_gb": 0}})
    assert configured_limit_bytes(settings.runner) == 0
    with pytest.raises(ValidationError):
        Settings.model_validate({"runner": {"step_memory_limit_gb": -1}})


def test_doctor_reports_limit_mechanism_and_disabled_state():
    from labhq.doctor import _memory_limit_row

    settings = Settings()
    settings.runner.step_memory_limit_gb = 1.5
    row = _memory_limit_row(settings)
    assert row["status"] == "ok" and "1.50 GiB" in row["detail"]
    if os.name == "nt":
        assert "Job object" in row["detail"] and "단계 트리 합산 상한" in row["detail"]
    elif memory_limit.posix_tree_memory_limit_supported():
        assert "MemoryMax" in row["detail"] and "단계 트리 합산 상한" in row["detail"]
    else:
        assert "RLIMIT_AS" in row["detail"] and "프로세스별 상한" in row["detail"]
    settings.runner.step_memory_limit_gb = 0
    assert _memory_limit_row(settings)["status"] == "skip"


def test_doctor_calls_posix_rlimit_a_per_process_limit(monkeypatch):
    from labhq.doctor import _memory_limit_row

    mechanism = memory_limit.memory_limit_mechanism
    monkeypatch.setattr(memory_limit, "posix_tree_memory_limit_supported", lambda: False, raising=False)
    monkeypatch.setattr(memory_limit, "memory_limit_mechanism", lambda: mechanism("posix"))
    settings = Settings()
    settings.runner.step_memory_limit_gb = 1
    row = _memory_limit_row(settings)
    assert "POSIX RLIMIT_AS" in row["detail"]
    assert "프로세스별 상한" in row["detail"]


def test_posix_guard_wraps_command_and_zero_has_no_wrapper():
    command = ["python", "job.py"]
    wrapped = StepMemoryGuard(1024, platform="posix", posix_tree=False).wrap_command(command)
    assert wrapped[-len(command):] == command and wrapped[:3] == [sys.executable, "-m", "labhq.runner.memory_limit"]
    assert StepMemoryGuard(0, platform="posix").spawn_kwargs() == {}
    assert StepMemoryGuard(0, platform="posix").wrap_command(command) == command


def test_posix_tree_guard_uses_user_systemd_scope():
    command = ["python", "job.py"]
    wrapped = StepMemoryGuard(1024, platform="posix", posix_tree=True).wrap_command(command)
    assert wrapped[:3] == ["systemd-run", "--user", "--scope"]
    assert "--property=MemoryMax=1024" in wrapped
    assert wrapped[-len(command):] == command


def test_systemd_probe_rejects_a_scope_that_does_not_enforce_memorymax(monkeypatch):
    class Completed:
        returncode = 0
        stdout = b"LABHQ_SCOPE_STARTED\nLABHQ_SCOPE_UNBOUNDED\n"

    monkeypatch.setattr(memory_limit.subprocess, "run", lambda *_a, **_kw: Completed())
    assert not memory_limit._systemd_memory_max_probe()


def test_systemd_probe_accepts_a_started_scope_that_blocks_the_allocation(monkeypatch):
    class Completed:
        returncode = 1
        stdout = b"LABHQ_SCOPE_STARTED\n"

    monkeypatch.setattr(memory_limit.subprocess, "run", lambda *_a, **_kw: Completed())
    assert memory_limit._systemd_memory_max_probe()


def test_systemd_missing_executable_keeps_the_runner_error_contract():
    stderr = "Failed to find executable definitely-not-installed: No such file or directory"
    assert StepMemoryGuard(1024, platform="posix", posix_tree=True).executable_missing(stderr)


def test_windows_wrapper_joins_job_before_it_starts_cli(monkeypatch):
    command = ["staff-cli", "run"]
    wrapped = StepMemoryGuard(1024, platform="nt").wrap_command(command)
    assert wrapped[-len(command):] == command
    assert wrapped[:3] == [sys.executable, "-m", "labhq.runner.memory_limit"]
    assert "--windows-job" in wrapped

    events = []

    class Job:
        def __init__(self, pid, limit):
            events.append(("job", pid, limit))

        def peak_bytes(self):
            return 0

        def close(self):
            events.append(("close",))

    class Completed:
        returncode = 0

    monkeypatch.setattr(memory_limit, "_WindowsJob", Job)
    monkeypatch.setattr(memory_limit.subprocess, "run",
                        lambda argv, check=False: events.append(("cli", argv, check)) or Completed())

    assert memory_limit._run_windows_job(["--windows-job", "1024", "--", *command]) == 0
    assert [event[0] for event in events] == ["job", "cli"]


def test_posix_wrapper_distinguishes_a_missing_executable(monkeypatch, capsys):
    resource = types.SimpleNamespace(
        RLIMIT_AS=1,
        RLIM_INFINITY=-1,
        getrlimit=lambda _kind: (-1, -1),
        setrlimit=lambda _kind, _value: None,
    )
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setattr(os, "execvpe", lambda *_args: (_ for _ in ()).throw(FileNotFoundError("missing")))

    assert _exec_with_limit(["--exec", "1024", "--", "missing-bin"]) == 127
    assert "LABHQ_EXEC_NOT_FOUND:" in capsys.readouterr().err


def test_cgroup_probe_does_not_use_task_artifact_path_reader(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: pytest.fail("Path.read_text called"))
    monkeypatch.setattr(builtins, "open", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("absent")))
    assert _cgroup_memory_bytes() == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX RLIMIT_AS enforcement")
def test_posix_wrapper_enforces_address_space_limit():
    command = [sys.executable, "-c", "x = bytearray(512 * 1024 * 1024); print(len(x))"]
    wrapped = StepMemoryGuard(128 * 1024 ** 2, platform="posix").wrap_command(command)
    completed = subprocess.run(wrapped, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert completed.returncode != 0


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", [(0, "0"), (1.5, str(int(1.5 * 1024 ** 3)))])
async def test_limit_is_passed_to_staff_environment(tmp_path, monkeypatch, configured, expected):
    settings = Settings()
    settings.runner.step_memory_limit_gb = configured
    captured = {}
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await _ExitAdapter(settings, [sys.executable, "-c", "pass"]).run(_context(tmp_path, settings))

    assert not result.ok  # no engine result is expected from this bare adapter
    assert captured[MEMORY_LIMIT_ENV] == expected


@pytest.mark.asyncio
async def test_limit_hit_is_classified_with_chunked_glm_hint(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.step_memory_limit_gb = 1

    class HitGuard:
        enabled = True

        def spawn_kwargs(self):
            return {}

        def attach(self, pid):
            pass

        def wrap_command(self, command):
            return command

        def exceeded(self, returncode, stderr):
            return True

        def setup_failed(self, stderr):
            return False

        def close(self):
            pass

    monkeypatch.setattr("labhq.adapters.base.memory_limit.step_memory_guard", lambda _limit: HitGuard())
    result = await _ExitAdapter(settings, [sys.executable, "-c", "raise SystemExit(7)"]).run(
        _context(tmp_path, settings)
    )

    assert result.error_kind == "memory_limit"
    assert "메모리 상한" in result.error
    assert "조각 처리" in result.error
    assert "묶은 이항 GLM" in result.error


@pytest.mark.skipif(os.name != "nt", reason="Windows Job object enforcement")
@pytest.mark.asyncio
async def test_windows_job_object_enforces_limit_without_admin(tmp_path):
    settings = Settings()
    settings.runner.step_memory_limit_gb = 0.125
    result = await _ExitAdapter(
        settings,
        [sys.executable, "-c", "x = bytearray(512 * 1024 * 1024); print(len(x))"],
    ).run(_context(tmp_path, settings))
    assert result.error_kind == "memory_limit"


def test_analyst_instructions_prefer_aggregated_glm_and_protect_internal_rows():
    analyst = Path("agents/core/analyst.yaml").read_text(encoding="utf-8")
    steward = Path("agents/core/data_steward.yaml").read_text(encoding="utf-8")
    for text in (analyst, steward):
        assert "열 이름" in text and "10 미만" in text
    assert "백만 행" in analyst and "묶은 이항 GLM" in analyst and "조각 처리" in analyst


# ---------------- #863: what the runner may say about a staff CLI's end (fakes only: no process, Job or WMI) ----------------

def test_windows_wrapper_announces_the_job_after_joining_it_and_before_the_cli(monkeypatch, capsys):
    events = []

    class Job:
        def __init__(self, pid, limit):
            events.append("job")

        def peak_bytes(self):
            return 0

    class Completed:
        returncode = 0

    def run(argv, check=False):
        events.append(("cli", capsys.readouterr().err))  # what the wrapper wrote before the CLI existed
        return Completed()

    monkeypatch.setattr(memory_limit, "_WindowsJob", Job)
    monkeypatch.setattr(memory_limit.subprocess, "run", run)
    assert memory_limit._run_windows_job(["--windows-job", "1024", "--", "cli"]) == 0
    assert events == ["job", ("cli", memory_limit.job_activation_line(os.getpid()) + "\n")]


def test_a_job_that_cannot_be_joined_announces_nothing_and_starts_no_cli(monkeypatch, capsys):
    def refuse(pid, limit):
        raise OSError("fixture: assignment refused")

    monkeypatch.setattr(memory_limit, "_WindowsJob", refuse)
    monkeypatch.setattr(memory_limit.subprocess, "run", lambda *a, **kw: pytest.fail("CLI started without a Job"))
    assert memory_limit._run_windows_job(["--windows-job", "1024", "--", "cli"]) == 126
    err = capsys.readouterr().err
    assert "LABHQ_MEMORY_LIMIT_SETUP:" in err and memory_limit.JOB_ACTIVE not in err


def test_the_job_handle_is_not_inheritable_and_stays_with_the_wrapper(monkeypatch):
    import ctypes

    class Basic(ctypes.Structure):
        _fields_ = [("LimitFlags", ctypes.c_uint32)]

    class Info(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("JobMemoryLimit", ctypes.c_size_t)]

    calls = []
    kernel = types.SimpleNamespace(
        CreateJobObjectW=lambda attributes, name: calls.append(("create", attributes, name)) or 7,
        SetInformationJobObject=lambda handle, kind, info, size: calls.append(
            ("limits", info._obj.BasicLimitInformation.LimitFlags)) or True,
        OpenProcess=lambda rights, inherit, pid: calls.append(("open", inherit)) or 9,
        AssignProcessToJobObject=lambda job, process: calls.append(("assign", job, process)) or True,
        CloseHandle=lambda handle: calls.append(("close", handle)))
    monkeypatch.setattr(memory_limit, "_windows_api", lambda: (kernel, Info))
    job = memory_limit._WindowsJob(1234, 1 << 30)
    assert calls[0] == ("create", None, None)  # no SECURITY_ATTRIBUTES: the Job handle is not inheritable
    assert calls[1][1] & memory_limit._WindowsJob._KILL_ON_CLOSE
    assert ("open", False) in calls and ("assign", 7, 9) in calls
    assert ("close", 9) in calls and ("close", 7) not in calls  # only the process handle; the wrapper keeps the Job
    job.close()
    assert ("close", 7) in calls


def test_only_the_wrappers_own_first_line_confirms_the_job():
    guard = StepMemoryGuard(1024, platform="nt")
    line = memory_limit.job_activation_line(4242)
    assert guard.job_expected and guard.job_confirmed(line, 4242)
    assert not guard.job_confirmed(line, 4243)  # another process's line
    assert not guard.job_confirmed("cli says " + line, 4242)
    assert not StepMemoryGuard(0, platform="nt").job_confirmed(line, 4242)  # no Job: nothing to confirm
    assert not StepMemoryGuard(1024, platform="posix", posix_tree=False).job_confirmed(line, 4242)


def test_cleanup_verdict_gives_unknown_without_activation_and_a_finished_wrapper():
    from labhq.adapters.base import cleanup_verdict
    done = {"job": "active", "wait": "completed", "returncode": 1}
    assert cleanup_verdict(done)["status"] == "job_close_contract_enforced"  # a killed wrapper is not a failed cleanup
    assert cleanup_verdict(done)["active_processes_measured"] is False
    assert cleanup_verdict({**done, "wait": "not_finished", "returncode": None})["reason"] == "wrapper_wait_unfinished"
    assert cleanup_verdict({**done, "job": "unconfirmed"})["reason"] == "windows_job_unconfirmed"
    assert cleanup_verdict({**done, "job": "disabled"})["reason"] == "windows_job_disabled"
    assert cleanup_verdict({})["status"] == "UNKNOWN"  # missing metadata
    assert cleanup_verdict({**done, "spawn": "cancelled"})["reason"] == "spawn_cancelled_before_handover"
    assert cleanup_verdict({"spawn": "failed"})["status"] == "not_started"


class _Lines:
    def __init__(self, lines, hold=None):
        self.lines, self.hold = [line.encode() + b"\n" for line in lines], hold

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.lines:
            return self.lines.pop(0)
        if self.hold is not None:
            await self.hold.wait()
        raise StopAsyncIteration


class _Wrapper:
    """A wrapper child double: pid, the two streams and an exit code; nothing is started."""
    def __init__(self, stderr, stdout=("done",), code=0, hold=None, pid=4242):
        self.pid, self.stdin, self.returncode, self._code, self.hold = pid, None, None, code, hold
        self.stdout, self.stderr = _Lines(stdout, hold), _Lines(stderr, hold)

    async def wait(self):
        if self.hold is not None:
            await self.hold.wait()
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode

    def kill(self):
        pytest.fail("the fake kill below must be used")


def _fake_run(monkeypatch, tmp_path, spawn, limit=1024):
    killed = []

    async def kill(proc):
        killed.append(proc.pid)
        if proc.returncode is None:
            proc.returncode = 1  # the tree kill's exit status
        if getattr(proc, "hold", None) is not None:
            proc.hold.set()
        return {"requested": True, "tree": {"killed": 2, "skipped": 0, "failed": 0, "spared": 0}, "wait": "completed"}

    settings = Settings()
    settings.runner.step_memory_limit_gb = 1
    monkeypatch.setattr("labhq.adapters.base.memory_limit.step_memory_guard",
                        lambda _limit: StepMemoryGuard(limit, platform="nt"))
    monkeypatch.setattr("labhq.adapters.base.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("labhq.adapters.base._wrapper_identity", lambda pid: 133000000000000000)
    monkeypatch.setattr(AgentAdapter, "_kill", staticmethod(kill))
    reports = []
    ctx = _context(tmp_path, settings)
    ctx.on_cleanup = reports.append
    return _ExitAdapter(settings, ["cli"]), ctx, reports, killed


@pytest.mark.parametrize("stderr,code,status", [
    ([memory_limit.job_activation_line(4242), "cli warning"], 0, "job_close_contract_enforced"),
    ([memory_limit.job_activation_line(4242)], 1, "job_close_contract_enforced"),  # non-zero exit is still a clean end
    (["cli warning", memory_limit.job_activation_line(4242)], 0, "UNKNOWN"),  # the CLI cannot forge a later line
    ([memory_limit.job_activation_line(4243)], 0, "UNKNOWN"),  # not this wrapper
    ([], 0, "UNKNOWN"),  # nothing announced
])
@pytest.mark.asyncio
async def test_a_finished_run_records_trusted_cleanup_evidence(tmp_path, monkeypatch, stderr, code, status):
    async def spawn(*argv, **kwargs):
        return _Wrapper(stderr, stdout=[memory_limit.job_activation_line(4242)], code=code)  # stdout is never trusted

    adapter, ctx, reports, _ = _fake_run(monkeypatch, tmp_path, spawn)
    result = await adapter.run(ctx)
    evidence = result.cleanup_evidence
    assert evidence["status"] == status and evidence["wrapper_pid"] == 4242
    assert evidence["wrapper_created"] == 133000000000000000 and evidence["returncode"] == code
    assert evidence["active_processes_measured"] is False and not result.shutdown_evidence and reports == []
    tail = (ctx.workdir / ".labhq" / "stderr_tail.txt").read_text(encoding="utf-8")
    # Only a confirmed announcement leaves the CLI's stderr; an unconfirmed line is kept as what it is: CLI output.
    assert (memory_limit.JOB_ACTIVE in tail) is (status == "UNKNOWN" and any(memory_limit.JOB_ACTIVE in x for x in stderr))


@pytest.mark.asyncio
async def test_without_a_job_the_end_is_unknown(tmp_path, monkeypatch):
    async def spawn(*argv, **kwargs):
        return _Wrapper([])

    adapter, ctx, _reports, _ = _fake_run(monkeypatch, tmp_path, spawn, limit=0)
    assert (await adapter.run(ctx)).cleanup_evidence["reason"] == "windows_job_disabled"


@pytest.mark.asyncio
async def test_a_cancelled_run_reports_its_wrapper_end_before_the_cancel_propagates(tmp_path, monkeypatch):
    hold = asyncio.Event()
    wrapper = _Wrapper([memory_limit.job_activation_line(4242)], stdout=[], hold=hold)

    async def spawn(*argv, **kwargs):
        return wrapper

    adapter, ctx, reports, killed = _fake_run(monkeypatch, tmp_path, spawn)
    run = asyncio.ensure_future(adapter.run(ctx))
    for _ in range(200):
        if (ctx.workdir / ".labhq").exists() and wrapper.stderr.lines == []:
            break
        await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert killed == [4242] and len(reports) == 1
    assert reports[0]["status"] == "job_close_contract_enforced" and reports[0]["returncode"] == 1
    assert reports[0]["kill"]["tree"]["killed"] == 2


@pytest.mark.parametrize("outcome", ["created", "missing"])
@pytest.mark.asyncio
async def test_a_cancel_during_spawn_takes_over_the_created_wrapper(tmp_path, monkeypatch, outcome):
    release, entered = asyncio.Event(), asyncio.Event()

    async def spawn(*argv, **kwargs):
        entered.set()
        await release.wait()
        if outcome == "missing":
            raise FileNotFoundError("fixture: no executable")
        return _Wrapper([memory_limit.job_activation_line(4242)])

    adapter, ctx, reports, killed = _fake_run(monkeypatch, tmp_path, spawn)
    run = asyncio.ensure_future(adapter.run(ctx))
    await asyncio.wait_for(entered.wait(), 5)
    run.cancel()
    await asyncio.sleep(0)
    release.set()  # the OS call finishes after the cancel arrived
    with pytest.raises(asyncio.CancelledError):
        await run
    assert len(reports) == 1
    if outcome == "created":
        assert killed == [4242] and reports[0]["wrapper_pid"] == 4242 and reports[0]["kill"]["requested"] is True
        assert reports[0]["status"] == "UNKNOWN" and reports[0]["reason"] == "spawn_cancelled_before_handover"
    else:
        assert killed == [] and reports[0]["status"] == "not_started"


def test_interruption_evidence_is_merged_with_cleanup_and_a_restart_alone_is_unknown():
    from labhq.models import TaskResult
    from labhq.runner.daemon import Runner
    runner = types.SimpleNamespace(_stopping=True, s=None, started_at=0, jobs={})
    cleanup = {"status": "job_close_contract_enforced", "active_processes_measured": False}
    result = TaskResult(task_id="t", agent_id="a", ok=False, error="cancelled", cleanup_evidence=cleanup,
                        shutdown_evidence={"kind": "earlier"})
    merged = Runner._shutdown_result(runner, result, cancelled=True).shutdown_evidence
    assert merged["kind"] == "earlier" and merged["cancelled"] is True and merged["cleanup"] == cleanup
    runner._shutdown_result = lambda result, **kw: Runner._shutdown_result(runner, result, **kw)
    restarted = Runner._interrupted_result(runner, "t", "a")
    assert restarted.cleanup_evidence["reason"] == "runner_restart_only"
    assert restarted.shutdown_evidence["cleanup"]["status"] == "UNKNOWN"
