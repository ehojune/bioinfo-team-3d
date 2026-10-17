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
