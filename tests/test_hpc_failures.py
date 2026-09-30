"""Issue #80: exercise real MCP error serialization with a fake scheduler."""

import json
import os
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import HpcSettings, Settings
from labhq.tools.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = {"script": "echo fixture", "job_name": "fixture"}


async def call_fixture(tmp_path, scenario, tool, arguments):
    config = tmp_path / "fixture.yaml"
    config.write_text("hpc:\n  scheduler: mock\n", encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "fixtures" / "hpc_failure_server.py"), scenario],
        env={**os.environ, "PYTHONPATH": str(ROOT), "LABHQ_CONFIG": str(config),
             "LABHQ_WORKDIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await session.call_tool(tool, arguments)


@pytest.mark.parametrize("scenario,tool,args,detail", [
    ("permissions", "hpc_submit", SUBMIT, "fixture script permission denied"),
    ("broker", "hpc_submit", SUBMIT, "fixture approval broker unavailable"),
    ("submit", "hpc_submit", SUBMIT, "qsub failed (1): qsub: fixture submission rejected"),
    ("cancel", "hpc_cancel", {"job_id": "123"}, "allow qdel in sudoers"),
    ("queue", "hpc_queue", {}, "qstat failed (255): ssh: fixture connection refused"),
    ("status", "hpc_status", {"job_id": "123"}, "qstat failed (255)"),
    ("accounting", "hpc_status", {"job_id": "123"}, "qacct failed (255)"),
    ("pbs_status", "hpc_status", {"job_id": "123"}, "qstat failed (255)"),
])
async def test_hpc_failures_reach_agent_tool_error(tmp_path, scenario, tool, args, detail):
    response = await call_fixture(tmp_path, scenario, tool, args)
    payload = response.model_dump(by_alias=True)
    assert payload.get("isError") is True
    text = "\n".join(block.text for block in response.content if block.type == "text")
    assert detail in text
    if tool == "hpc_cancel":
        assert "fixture qdel permission denied" in text
    assert '"state": "missing"' not in text

    # Feed the wire result into both staff CLI stream parsers. No real CLI is run.
    for engine in (Engine.claude_code, Engine.codex):
        events = []

        async def emit(kind, data):
            events.append((kind, data))

        settings = Settings()
        agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=engine)
        ctx = RunContext(task=Task(agent_id=agent.id, prompt="fixture"), agent=agent,
                         workdir=tmp_path, settings=settings, mcp_servers=[], env={},
                         emit=emit, prompt="fixture")
        if engine == Engine.claude_code:
            stream = {"type": "user", "message": {"content": [
                {"type": "tool_result", "is_error": payload["isError"], "content": text}]}}
        else:
            stream = {"type": "item.completed", "item": {"type": "mcp_tool_call",
                      "server": "labhq_hpc", "tool": tool, "status": "completed", "result": payload}}
        state = RunState()
        await get_adapter(engine, settings).handle_line(json.dumps(stream), state, ctx)
        assert [kind for kind, _ in events] == ["agent.tool_error"]
        assert detail in events[0][1]["text"]
        assert state.error is None  # the model can recover from an ordinary tool error


async def test_pi_denial_is_normal_result_with_no_resubmit_instruction(tmp_path):
    response = await call_fixture(tmp_path, "denied", "hpc_submit", SUBMIT)
    assert not response.model_dump(by_alias=True).get("isError", False)
    result = json.loads(response.content[0].text)
    assert result["submitted"] is False and result["reason"] == "fixture PI denied"
    assert "Do not resubmit" in result["note"]


@pytest.mark.parametrize("error_key", ["isError", "is_error"])
async def test_codex_completed_mcp_error_result_is_visible(tmp_path, error_key):
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    settings = Settings()
    agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=Engine.codex)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="fixture"), agent=agent,
                     workdir=tmp_path, settings=settings, mcp_servers=[], env={}, emit=emit, prompt="fixture")
    stream = {"type": "item.completed", "item": {"type": "mcp_tool_call", "status": "completed",
              "result": {error_key: True, "content": [{"type": "text", "text": "fixture qstat failed"}]}}}
    await get_adapter(agent.engine, settings).handle_line(json.dumps(stream), RunState(), ctx)
    assert events == [("agent.tool_error", {"text": "fixture qstat failed"})]


@pytest.mark.parametrize("scheduler", ["sge", "pbs"])
@pytest.mark.parametrize("operation", ["queue", "status"])
def test_failed_qstat_is_not_an_empty_queue_or_missing(monkeypatch, scheduler, operation):
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="fixture"))
    monkeypatch.setattr(backend, "_run", lambda args: CompletedProcess(args, 255, "", "fixture ssh failed"))
    with pytest.raises(RuntimeError, match=r"qstat failed \(255\).*fixture ssh failed"):
        backend.queue() if operation == "queue" else backend.status("123")


@pytest.mark.parametrize("returncode,stdout,stderr", [
    (1, "", "fixture accounting permission denied"),
    (0, "unexpected accounting response", ""),
])
def test_failed_accounting_is_not_missing(monkeypatch, returncode, stdout, stderr):
    backend = Scheduler(HpcSettings(scheduler="sge", user="fixture"))

    def run(args):
        return CompletedProcess(args, 0, "", "") if args[0] == "qstat" else CompletedProcess(
            args, returncode, stdout, stderr)

    monkeypatch.setattr(backend, "_run", run)
    with pytest.raises(RuntimeError, match="qacct"):
        backend.status("123")


@pytest.mark.parametrize("scheduler,stderr", [
    ("sge", "error: job id 123 not found"),
    ("pbs", "qstat: Unknown Job Id 123"),
])
def test_confirmed_job_absence_remains_missing(monkeypatch, scheduler, stderr):
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="fixture"))

    def run(args):
        if args == ["qstat", "-u", "fixture"]:
            return CompletedProcess(args, 0, "", "")
        return CompletedProcess(args, 1, "", stderr)

    monkeypatch.setattr(backend, "_run", run)
    info = backend.status("123")
    assert info.state == "missing" and not info.terminal
