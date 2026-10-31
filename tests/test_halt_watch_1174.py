"""A run that finishes while its halt is being logged keeps its result (#1174 H1)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings

INIT = {"type": "system", "subtype": "init", "session_id": "sess-1", "model": "claude-opus", "mcp_servers": []}
RESULT = {"type": "result", "subtype": "success", "result": "done", "session_id": "sess-1", "total_cost_usd": 0.1,
          "num_turns": 1}


def fake_claude(tmp: Path, finish_after_s: float) -> Path:
    """Prints init, then its result `finish_after_s` later and exits, like a CLI that ends on its own."""
    script = tmp / "claude"
    script.write_text(
        "import sys, json, time\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        f"print(json.dumps({INIT!r}), flush=True)\n"
        f"time.sleep({finish_after_s})\n"
        f"print(json.dumps({RESULT!r}), flush=True)\n", encoding="utf-8")
    return script


async def run_with_halt(tmp: Path, *, finish_after_s: float, halt_on: str = "agent.status"):
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(fake_claude(tmp, finish_after_s))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m", tools=["Read"])
    halt, events = asyncio.Event(), []

    async def emit(kind, data):
        events.append((kind, data))
        if kind == halt_on and (kind != "agent.status" or data.get("model")):
            halt.set()  # the PI halts as soon as the CLI is up, or as soon as its result has been handled

    workdir = tmp / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt, halt_grace_s=0.0)
    return await asyncio.wait_for(get_adapter(agent.engine, s).run(ctx), 30), events


@pytest.mark.asyncio
async def test_a_halt_that_finds_the_cli_still_running_still_stops_it(tmp_path):
    result, events = await run_with_halt(tmp_path, finish_after_s=20)
    assert result.error_kind == "halted" and not result.ok
    assert result.session_id == "sess-1"  # the session is kept for the resume
    assert json.dumps(events, ensure_ascii=False).count("멈춤") >= 1


LIMIT_S = 5  # a run that has not ended by then is a hang in the code under test, not a slow machine


class FakeProc:
    """A CLI that prints its lines, then lingers for `linger_s` before it exits: no OS process, so nothing can be left
    behind when the test ends or fails. Its pid names no process group, so an unexpected kill finds nothing to hit."""

    pid = 2 ** 30
    stdin = None

    def __init__(self, lines, linger_s):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.returncode, self.killed = None, False
        self._done = asyncio.Event()
        self._task = asyncio.ensure_future(self._run(lines, linger_s))

    async def _run(self, lines, linger_s):
        for line in lines:
            self.stdout.feed_data((json.dumps(line) + chr(10)).encode())
            await asyncio.sleep(0.02)
        await asyncio.sleep(linger_s)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = 0 if self.returncode is None else self.returncode
        self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._task.cancel()
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._done.set()

    terminate = send_signal = kill


async def run_fake(tmp_path, monkeypatch, engine, lines, *, linger_s=0.4, halt_on="agent.usage", usage_sleep_s=0.3):
    """Run a real adapter over a FakeProc; the PI halts when `halt_on` is emitted."""
    procs = []

    async def spawn(*_args, **_kwargs):
        procs.append(FakeProc(lines, linger_s))
        return procs[-1]

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *_: command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    settings = Settings()
    agent = AgentSpec(id="a1", name="A", role="r", engine=engine, model="m", tools=[])
    halt, events = asyncio.Event(), []

    async def emit(kind, data):
        events.append((kind, data))
        if kind == halt_on:
            halt.set()
        if kind == "agent.usage":
            await asyncio.sleep(usage_sleep_s)  # the result handler is still running, result_seen already set

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt,
                     halt_grace_s=0.0)
    # A hard cap: `wait_for` would wait again for the run's cleanup after cancelling it, which is the very wait that hangs.
    # `asyncio.wait` returns at the limit; the run is cancelled, its fake process ended, and the test fails without awaiting it.
    run = asyncio.ensure_future(get_adapter(engine, settings).run(ctx))
    finished, _pending = await asyncio.wait({run}, timeout=LIMIT_S)
    if not finished:
        for proc in procs:
            proc.kill()
        run.cancel()
        pytest.fail(f"the run had not ended after {LIMIT_S} s: {[kind for kind, _ in events]}")
    return run.result(), events, procs[0]


@pytest.mark.asyncio
async def test_a_halt_that_comes_after_the_result_keeps_the_result_and_does_not_claim_a_kill(tmp_path, monkeypatch):
    result, events, proc = await run_fake(tmp_path, monkeypatch, Engine.claude_code, [INIT, RESULT])
    assert result.error_kind != "halted" and result.ok and result.text == "done"
    texts = [str(d.get("text")) for kind, d in events if kind == "agent.log"]
    assert not any("프로세스 트리를 끝냈습니다" in text for text in texts)  # nothing was ended, so nothing is claimed
    assert not proc.killed


@pytest.mark.asyncio
async def test_a_completed_turn_whose_handler_is_still_awaiting_is_not_halted(tmp_path, monkeypatch):
    """Gemini's result handler sets `result_seen` and then awaits `agent.usage`; `result_arrived` follows after that."""
    lines = [{"type": "init", "session_id": "g-1", "model": "gemini-pro"},
             {"type": "message", "role": "assistant", "content": "done", "delta": True},
             {"type": "result", "status": "success", "stats": {"total_tokens": 9}}]
    result, events, proc = await run_fake(tmp_path, monkeypatch, Engine.gemini, lines)
    assert result.error_kind != "halted" and result.ok
    assert not proc.killed, "a finished turn must not be killed by a halt that came a moment later"
