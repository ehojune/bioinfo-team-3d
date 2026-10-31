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


async def run_with_halt(tmp: Path, *, emit_s: float, finish_after_s: float):
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(fake_claude(tmp, finish_after_s))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m", tools=["Read"])
    halt, events = asyncio.Event(), []

    async def emit(kind, data):
        events.append((kind, data))
        if kind == "agent.status" and data.get("model"):
            halt.set()  # the PI halts as soon as the CLI is up
        if kind == "agent.log" and "멈춤" in str(data.get("text")):
            await asyncio.sleep(emit_s)  # the durable commit of the halt notice

    workdir = tmp / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt, halt_grace_s=0.0)
    return await asyncio.wait_for(get_adapter(agent.engine, s).run(ctx), 30), events


@pytest.mark.asyncio
async def test_a_result_that_arrives_while_the_halt_is_logged_stays_a_result(tmp_path):
    result, events = await run_with_halt(tmp_path, emit_s=1.5, finish_after_s=0.4)
    assert result.error_kind != "halted"
    assert result.ok and result.text == "done"


@pytest.mark.asyncio
async def test_a_halt_that_finds_the_cli_still_running_still_stops_it(tmp_path):
    result, events = await run_with_halt(tmp_path, emit_s=0.0, finish_after_s=20)
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
        if kind == "agent.status" and data.get("model"):
            halt.set()
        if kind == "agent.log" and "멈춤" in str(data.get("text")):
            await asyncio.sleep(0.6)  # the halt notice commits while the result event is being handled
        if kind == "agent.usage":
            await asyncio.sleep(1.5)  # the handler is still awaiting here, result_seen already set

    workdir = tmp_path / "wd"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a1", prompt="x"), agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=emit, prompt="x", extra_dirs=[], claude_settings={}, halt=halt, halt_grace_s=0.0)
    result = await asyncio.wait_for(get_adapter(agent.engine, s).run(ctx), 30)
    assert result.error_kind != "halted" and result.ok
