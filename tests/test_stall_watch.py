"""A staff CLI that goes quiet gets one warning; on Windows it names pending UAC prompts (#382)."""

import asyncio

import pytest

import labhq.adapters.base as base
from labhq.models import AgentSpec, Engine, Task
from labhq.runner.daemon import Runner
from labhq.settings import Settings


class _QuietProc:
    """A Codex CLI that says nothing for `quiet` seconds, then finishes its turn."""

    returncode = 0
    stdin = None

    def __init__(self, quiet):
        self.quiet = quiet
        self.stdout, self.stderr = self._out(), self._err()

    async def _out(self):
        await asyncio.sleep(self.quiet)
        for line in (b'{"type":"thread.started","thread_id":"t1"}\n',
                     b'{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}\n',
                     b'{"type":"turn.completed","usage":{}}\n'):
            yield line

    async def _err(self):
        for line in ():
            yield line

    async def wait(self):
        await asyncio.sleep(self.quiet)
        return 0


def _runner(tmp_path, monkeypatch, quiet, stall_warn_s):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    settings.runner.stall_warn_s = stall_warn_s

    async def fake_exec(*cmd, **kwargs):
        return _QuietProc(quiet)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda cmd, env, engine: cmd)
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=[],
                      system_prompt="You are the worker.")
    monkeypatch.setattr(runner.registry, "get", lambda _id: agent)
    return runner


def _logs(runner):
    return [(e["data"].get("level"), e["data"].get("text", "")) for e in runner.store.pending()
            if e["type"] == "agent.log" and "출력이" in e["data"].get("text", "")]


@pytest.mark.asyncio
@pytest.mark.parametrize("uac", [False, True])
async def test_a_quiet_cli_gets_one_warning_and_pending_uac_is_named(tmp_path, monkeypatch, uac):
    async def prompts():
        return uac

    monkeypatch.setattr(base, "pending_uac_prompts", prompts)
    runner = _runner(tmp_path, monkeypatch, quiet=0.4, stall_warn_s=0.1)

    result = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"}))

    assert result.ok, result.error
    logs = _logs(runner)
    assert len(logs) == 1, logs  # once per run, however long the silence
    level, text = logs[0]
    if uac:
        assert level == "alert" and "Windows 권한 알림(UAC)" in text and "#382" in text
    else:
        assert level == "warn" and "멈췄다면 단계를 취소하세요" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("quiet,stall_warn_s", [(0.3, 0), (0.05, 5)])
async def test_no_warning_when_off_or_the_cli_speaks_in_time(tmp_path, monkeypatch, quiet, stall_warn_s):
    runner = _runner(tmp_path, monkeypatch, quiet=quiet, stall_warn_s=stall_warn_s)
    result = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"}))
    assert result.ok, result.error
    assert _logs(runner) == []
