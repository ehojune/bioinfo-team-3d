"""Quiet staff CLIs warn once and retry only when their process tree is certainly idle (#382, #473)."""

import asyncio
import sys

import pytest

import labhq.adapters.base as base
from labhq.models import AgentSpec, Engine, Task
from labhq.orchestrator.cso import failure_kind
from labhq.runner.daemon import Runner
from labhq.settings import Settings


class _QuietProc:
    """A Codex CLI that says nothing for `quiet` seconds, then finishes its turn."""

    stdin = None

    def __init__(self, quiet, *, finish=True, session_first=False, prelude=()):
        self.quiet = quiet
        self.finish = finish
        self.session_first = session_first
        self.prelude = prelude
        self.returncode = None
        self.pid = 41073
        self.done = asyncio.Event()
        self.stdout, self.stderr = self._out(), self._err()

    async def _out(self):
        if self.session_first:
            yield b'{"type":"thread.started","thread_id":"t1"}\n'
        for line in self.prelude:
            yield line
        if not self.finish:
            await self.done.wait()
            return
        await asyncio.sleep(self.quiet)
        for line in (b'{"type":"thread.started","thread_id":"t1"}\n',
                      b'{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}\n',
                      b'{"type":"turn.completed","usage":{}}\n'):
            yield line
        self.returncode = 0
        self.done.set()

    async def _err(self):
        for line in ():
            yield line

    async def wait(self):
        if self.finish:
            await asyncio.sleep(self.quiet)
            if self.returncode is None:
                self.returncode = 0
                self.done.set()
        else:
            await self.done.wait()
        return self.returncode

    def end(self, returncode=-9):
        self.returncode = returncode
        self.done.set()


def _runner(tmp_path, monkeypatch, quiet, stall_warn_s, *, stall_retry_s=0, finish=True, session_first=False,
            prelude=()):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    settings.runner.stall_warn_s = stall_warn_s
    settings.runner.stall_retry_s = stall_retry_s
    process = _QuietProc(quiet, finish=finish, session_first=session_first, prelude=prelude)

    async def fake_exec(*cmd, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda cmd, env, engine: cmd)
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=[],
                      system_prompt="You are the worker.")
    monkeypatch.setattr(runner.registry, "get", lambda _id: agent)
    runner.test_process = process
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


def _snapshot(*children):
    return [base._ProcessInfo(41073, 1, "codex.exe", "codex exec"), *children]


def _fake_kill(monkeypatch, killed):
    async def kill(proc):
        killed.append(proc.pid)
        proc.end()

    monkeypatch.setattr(base.AgentAdapter, "_kill", staticmethod(kill))


def test_tool_activity_pairs_codex_and_claude_calls():
    codex = base._ToolActivity("codex")
    codex.observe('{"type":"item.started","item":{"id":"m1","type":"mcp_tool_call"}}')
    codex.observe('{"type":"item.started","item":{"call_id":"f1","type":"function_call"}}')
    assert codex.interpretable and codex.active == 2
    codex.observe('{"type":"item.completed","item":{"id":"m1","type":"mcp_tool_call"}}')
    codex.observe('{"type":"item.completed","item":{"call_id":"f1","type":"function_call"}}')
    assert codex.active == 0

    claude = base._ToolActivity("claude_code")
    claude.observe('{"type":"assistant","message":{"content":['
                   '{"type":"tool_use","id":"toolu_1","name":"mcp__labhq_ask__ask"}]}}')
    assert claude.interpretable and claude.active == 1
    claude.observe('{"type":"user","message":{"content":['
                   '{"type":"tool_result","tool_use_id":"toolu_1"}]}}')
    assert claude.active == 0


@pytest.mark.asyncio
async def test_quiet_cli_without_a_command_is_ended_as_transient(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=60, stall_warn_s=0.02, stall_retry_s=0.04,
                     finish=False, session_first=True)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert killed == [41073]
    assert not result.ok and result.session_id == "t1"
    assert result.error == "engine stream timed out: no output for 1 min and no running command"
    assert failure_kind(result) == "transient"
    assert "labhq가 끊고 다시 시도합니다" in _logs(runner)[0][1]
    assert "연구 lane에서 단계를 취소하면 요청이 실패" in _logs(runner)[0][1]


@pytest.mark.asyncio
async def test_started_mcp_call_without_completion_is_not_ended(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    prelude = (b'{"type":"item.started","item":{"id":"call-1","type":"mcp_tool_call",'
               b'"server":"labhq_approval","tool":"ask"}}\n',)
    runner = _runner(tmp_path, monkeypatch, quiet=0.2, stall_warn_s=0.02, stall_retry_s=0.04,
                     prelude=prelude)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert result.ok, result.error
    assert killed == []


@pytest.mark.asyncio
async def test_completed_mcp_call_then_quiet_idle_cli_is_transient(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    prelude = (
        b'{"type":"item.started","item":{"id":"call-1","type":"mcp_tool_call",'
        b'"server":"labhq_ask","tool":"ask"}}\n',
        b'{"type":"item.completed","item":{"id":"call-1","type":"mcp_tool_call",'
        b'"server":"labhq_ask","tool":"ask","status":"completed"}}\n',
    )
    runner = _runner(tmp_path, monkeypatch, quiet=60, stall_warn_s=0.02, stall_retry_s=0.04,
                     finish=False, session_first=True, prelude=prelude)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert killed == [41073]
    assert not result.ok and failure_kind(result) == "transient"


@pytest.mark.asyncio
async def test_quiet_cli_with_a_long_child_command_is_not_ended(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot(
        base._ProcessInfo(41074, 41073, "python.exe", "python long_analysis.py")))
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=0.2, stall_warn_s=0.02, stall_retry_s=0.04)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert result.ok, result.error
    assert killed == []


@pytest.mark.asyncio
async def test_quiet_cli_with_only_resident_helpers_is_ended(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    mcp_command = f'"{sys.executable}" -m labhq.tools.ask_mcp'
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot(
        base._ProcessInfo(41074, 41073, "conhost.exe", None),
        base._ProcessInfo(41075, 41073, "codex-code-mode-host.exe", None),
        base._ProcessInfo(41076, 41073, sys.executable, mcp_command)))
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=60, stall_warn_s=0.02, stall_retry_s=0.04,
                     finish=False)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert not result.ok and failure_kind(result) == "transient"
    assert killed == [41073]


@pytest.mark.asyncio
async def test_pending_uac_keeps_a_quiet_cli_running(tmp_path, monkeypatch):
    async def prompts():
        return True

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=0.2, stall_warn_s=0.02, stall_retry_s=0.04)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert result.ok, result.error
    assert killed == []
    assert _logs(runner)[0][0] == "alert"
    assert "연구 lane에서 단계를 취소하면 요청이 실패" in _logs(runner)[0][1]


@pytest.mark.asyncio
async def test_uac_that_disappears_is_rechecked_then_cli_is_ended(tmp_path, monkeypatch):
    checks = 0

    async def prompts():
        nonlocal checks
        checks += 1
        return checks < 4

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=60, stall_warn_s=0.02, stall_retry_s=0.04,
                     finish=False, session_first=True)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert checks >= 5
    assert killed == [41073]
    assert not result.ok and failure_kind(result) == "transient"
    assert len(_logs(runner)) == 1
    uac_logs = [text for _level, text in _logs(runner) if "UAC" in text]
    assert len(uac_logs) == 1


@pytest.mark.asyncio
async def test_retry_shorter_than_warning_uses_the_warning_limit(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: _snapshot())
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=0.05, stall_warn_s=0.1, stall_retry_s=0.01)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert result.ok, result.error
    assert killed == [] and _logs(runner) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [None, [base._ProcessInfo(41073, 1, "codex.exe", "codex exec"),
                                               base._ProcessInfo(41074, 41073, "", None)]])
async def test_unreadable_or_unknown_process_tree_is_not_ended(tmp_path, monkeypatch, snapshot):
    async def no_prompts():
        return False

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", lambda: snapshot)
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=0.2, stall_warn_s=0.02, stall_retry_s=0.04)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert result.ok, result.error
    assert killed == []


@pytest.mark.asyncio
async def test_temporary_process_snapshot_failure_is_rechecked(tmp_path, monkeypatch):
    async def no_prompts():
        return False

    snapshots = 0

    def snapshot():
        nonlocal snapshots
        snapshots += 1
        return None if snapshots <= 2 else _snapshot()

    killed = []
    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    monkeypatch.setattr(base, "_process_snapshot", snapshot)
    _fake_kill(monkeypatch, killed)
    runner = _runner(tmp_path, monkeypatch, quiet=60, stall_warn_s=0.02, stall_retry_s=0.04,
                     finish=False, session_first=True)

    result = await asyncio.wait_for(
        runner.run_task(Task(agent_id="worker", request_id="r", prompt="q", meta={"kind": "step"})), 2)

    assert snapshots >= 4
    assert killed == [41073]
    assert not result.ok and failure_kind(result) == "transient"
    warnings = [e for e in runner.store.pending() if e["type"] == "agent.log"
                and "process tree를 확인하지 못해" in e["data"].get("text", "")]
    assert len(warnings) == 1
