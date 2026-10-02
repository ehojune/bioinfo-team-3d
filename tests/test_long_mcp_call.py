"""#276: a labhq MCP call that answers after 90 seconds completes once under Claude and Codex, with no CLI client,
adapter or runner timeout. The fake CLI applies each real CLI's own tool-timeout rule to the config labhq wrote."""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task
from labhq.runner.daemon import Runner
from labhq.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLI = ROOT / "tests" / "fixtures" / "fake_mcp_client.py"
DELAYED_MCP = ROOT / "scripts" / "fake_delayed_mcp.py"
DELAY_S = 90.0


def _runner(tmp_path: Path, engine: Engine, *, time_scale: float) -> tuple[Runner, list, Path]:
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.policy.approvals.timeout_s = 60  # the call outlives the approval wait and Codex's 60 s default
    binary = getattr(settings.engines, engine.value)
    binary.bin = sys.executable
    binary.prefix_args = [str(FAKE_CLI), engine.value, "labhq_ask"]
    binary.env = {**binary.env, "LABHQ_TEST_TIMEOUT_SCALE": str(time_scale)}
    if engine == Engine.codex:  # hermetic: the host's ~/.codex/AGENTS.md would refuse the staff session
        (tmp_path / "codex-home").mkdir()
        binary.env["CODEX_HOME"] = str(tmp_path / "codex-home")
    runner = Runner(settings)
    calls = tmp_path / "calls.txt"
    built = runner._mcp_servers

    def delayed(agent, env, allow_ask=True):  # the runner's own labhq_ask spec, answered by the slow server
        return [server.model_copy(update={"command": sys.executable, "args": [str(DELAYED_MCP)],
                                          "env": {**server.env, "LABHQ_PROBE_DELAY_S": str(DELAY_S * time_scale),
                                                  "LABHQ_PROBE_CALLS": str(calls)}})
                if server.name == "labhq_ask" else server for server in built(agent, env, allow_ask)]

    events = []
    emit = runner.emit

    async def record(event):
        events.append(event)
        await emit(event)

    agent = AgentSpec(id=f"slow_{engine.value}", name="Slow", role="test", engine=engine, builtin_mcp=[])
    runner._resolve_agent = lambda _task: agent
    runner._mcp_servers = delayed
    runner.emit = record
    return runner, events, calls


async def _run_delayed(tmp_path: Path, engine: Engine, *, time_scale: float):
    root = tmp_path / engine.value
    root.mkdir()
    runner, events, calls = _runner(root, engine, time_scale=time_scale)
    try:
        task = Task(agent_id=f"slow_{engine.value}", request_id=f"req_{engine.value}", prompt="call slow_answer once")
        started = time.monotonic()
        result = await runner.run_task(task)
        return result, time.monotonic() - started, events, calls
    finally:
        runner.store.close()


async def _assert_long_calls_finish(tmp_path: Path, *, time_scale: float, outer_timeout: float) -> None:
    engines = (Engine.claude_code, Engine.codex)
    runs = await asyncio.wait_for(
        asyncio.gather(*(_run_delayed(tmp_path, e, time_scale=time_scale) for e in engines)),
        timeout=outer_timeout,
    )

    for engine, (result, elapsed, events, calls) in zip(engines, runs):
        kinds = [event.type for event in events]
        assert result.ok, f"{engine.value}: {result.error}"
        assert result.text == "LABHQ_DELAY_OK", engine.value
        assert DELAY_S * time_scale <= elapsed < outer_timeout, (engine.value, elapsed)
        assert len(calls.read_text(encoding="utf-8").splitlines()) == 1, engine.value
        assert kinds.count("agent.tool") == 1 and "agent.tool_error" not in kinds, (engine.value, kinds)
        assert kinds.count("task.result") == 1, (engine.value, kinds)


async def test_claude_and_codex_each_finish_one_scaled_90_second_labhq_mcp_call(tmp_path):
    """Exercise the real config and MCP call path with a 1/1000 client/server clock."""
    await _assert_long_calls_finish(tmp_path, time_scale=0.001, outer_timeout=10)


@pytest.mark.skipif(os.environ.get("LABHQ_SLOW_TESTS") != "1", reason="set LABHQ_SLOW_TESTS=1 for the 90 s probe")
async def test_claude_and_codex_each_finish_one_90_second_labhq_mcp_call(tmp_path):
    # The bound only keeps a hung opt-in probe from holding the run; the runner keeps its own 6 h task timeout.
    await _assert_long_calls_finish(tmp_path, time_scale=1, outer_timeout=170)
