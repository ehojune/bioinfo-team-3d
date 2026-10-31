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


def fake_lingering_claude(tmp: Path) -> Path:
    """Prints init and its result at once, then lingers like a CLI that has not exited yet."""
    script = tmp / "claude"
    script.write_text(chr(10).join([
        "import sys, json, time", "sys.stdout.reconfigure(encoding='utf-8')",
        f"print(json.dumps({INIT!r}), flush=True)", f"print(json.dumps({RESULT!r}), flush=True)",
        "time.sleep(4)", ""]), encoding="utf-8")
    return script


@pytest.mark.asyncio
async def test_a_halt_that_comes_after_the_result_keeps_the_result_and_does_not_claim_a_kill(tmp_path):
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(fake_lingering_claude(tmp_path))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m", tools=["Read"])
    halt, events = asyncio.Event(), []

    async def emit(kind, data):
        events.append((kind, data))
        if kind == "agent.usage":  # the result handler has set result_seen and is still running
            halt.set()

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt, halt_grace_s=0.0)
    result = await asyncio.wait_for(get_adapter(agent.engine, s).run(ctx), 30)
    assert result.error_kind != "halted" and result.ok and result.text == "done"
    texts = [str(d.get("text")) for kind, d in events if kind == "agent.log"]
    assert not any("프로세스 트리를 끝냈습니다" in text for text in texts)  # nothing was ended, so nothing is claimed


@pytest.mark.asyncio
async def test_a_halt_that_finds_the_cli_still_running_still_stops_it(tmp_path):
    result, events = await run_with_halt(tmp_path, finish_after_s=20)
    assert result.error_kind == "halted" and not result.ok
    assert result.session_id == "sess-1"  # the session is kept for the resume
    assert json.dumps(events, ensure_ascii=False).count("멈춤") >= 1


@pytest.mark.asyncio
async def test_a_completed_turn_whose_handler_is_still_awaiting_is_not_halted(tmp_path):
    """Gemini's result handler sets `result_seen` and then awaits `agent.usage`; `result_arrived` follows after that."""
    script = tmp_path / "gemini"
    events = [{"type": "init", "session_id": "g-1", "model": "gemini-pro"},
              {"type": "message", "role": "assistant", "content": "done", "delta": True},
              {"type": "result", "status": "success", "stats": {"total_tokens": 9}}]
    nl = chr(10)
    script.write_text(nl.join([
        "import sys, json, time", "sys.stdout.reconfigure(encoding='utf-8')",
        f"for e in {events!r}:", "    print(json.dumps(e), flush=True)", "    time.sleep(0.1)",
        "time.sleep(4)", ""]), encoding="utf-8")  # the CLI lingers after its result
    s = Settings()
    s.engines.gemini.bin = sys.executable
    s.engines.gemini.prefix_args = [str(script)]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.gemini, model="m", tools=[])
    halt = asyncio.Event()

    async def emit(kind, data):
        if kind == "agent.usage":
            halt.set()  # the PI halts while the handler of the result event is still running (result_seen already set)
            await asyncio.sleep(1.5)

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt, halt_grace_s=0.0)
    result = await asyncio.wait_for(get_adapter(agent.engine, s).run(ctx), 30)
    assert result.error_kind != "halted" and result.ok
