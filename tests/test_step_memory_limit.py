"""#709: every staff CLI gets a bounded process tree and a useful failure cause."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq.adapters.base import AgentAdapter, RunContext
from labhq.models import AgentSpec, Task
from labhq.runner.memory_limit import (
    MEMORY_LIMIT_ENV,
    StepMemoryGuard,
    configured_limit_bytes,
    usable_memory_bytes,
)
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


def test_default_limit_is_sixty_percent_of_runner_memory():
    settings = Settings()
    total = usable_memory_bytes()
    limit = configured_limit_bytes(settings.runner)
    assert total > 0 and limit > 0
    assert abs(limit - int(total * 0.60)) <= 2 * 1024 * 1024


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
    assert "Job object" in row["detail"] if os.name == "nt" else "RLIMIT_AS" in row["detail"]
    settings.runner.step_memory_limit_gb = 0
    assert _memory_limit_row(settings)["status"] == "skip"


def test_posix_guard_wraps_command_and_zero_has_no_wrapper():
    command = ["python", "job.py"]
    wrapped = StepMemoryGuard(1024, platform="posix").wrap_command(command)
    assert wrapped[-len(command):] == command and wrapped[:3] == [sys.executable, "-m", "labhq.runner.memory_limit"]
    assert StepMemoryGuard(0, platform="posix").spawn_kwargs() == {}
    assert StepMemoryGuard(0, platform="posix").wrap_command(command) == command


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
