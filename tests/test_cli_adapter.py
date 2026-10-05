"""engine: cli — an external agent (like bioinfo-agent) speaking the labhq JSONL protocol."""

import json
import sys
from pathlib import Path

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.settings import Settings

AGENT = r'''
import json, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")
args = sys.argv[1:]
prompt = sys.stdin.read()
pathlib.Path(args[args.index("--outputs") + 1], "done.txt").write_text("ok")
emit = lambda **e: print(json.dumps(e, ensure_ascii=False), flush=True)
emit(type="status", state="working", task="batch QC")
emit(type="tool", name="fastqc", input={"n": 12})
print("plain progress line")
emit(type="log", text="12/12 samples passed")
emit(type="result", ok=True, text="표준 QC 완료: " + prompt[:10], structured={"passed": 12},
     session_id="ba-1", cost_usd=0.05)
'''


def _ctx(tmp: Path, cli: CliSpec, resume: str | None = None):
    events = []

    async def emit(t, d):
        events.append((t, d))

    agent = AgentSpec(id="bioinfo-agent", name="수달", role="routine", engine=Engine.cli, cli=cli, system_prompt="ROLE")
    task = Task(agent_id="bioinfo-agent", prompt="run batch QC", output_schema={"type": "object"},
                resume_session_id=resume)
    wd = tmp / "wd"
    (wd / "outputs").mkdir(parents=True, exist_ok=True)
    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=Settings(), mcp_servers=[], env={},
                     emit=emit, prompt="run batch QC")
    return ctx, events


async def test_jsonl_protocol_and_placeholders(tmp_path):
    script = tmp_path / "bioinfo-agent"
    script.write_text("# coding: utf-8\n" + AGENT, encoding="utf-8")
    cli = CliSpec(command=[sys.executable, str(script), "run", "--task", "{prompt_file}", "--outputs", "{outputs}"],
                  stdin="prompt", output="jsonl", resume_args=["--resume", "{session_id}"])
    ctx, events = _ctx(tmp_path, cli)
    res = await get_adapter(Engine.cli, ctx.settings).run(ctx)
    assert res.ok and res.session_id == "ba-1" and res.cost_usd == 0.05
    assert res.structured == {"passed": 12} and res.text.startswith("표준 QC 완료: run batch")
    kinds = [t for t, _ in events]
    assert "agent.tool" in kinds and ("agent.status", {"state": "working", "task": "batch QC"}) in events
    assert any(t == "agent.log" and d.get("text") == "plain progress line" for t, d in events)
    assert (ctx.workdir / "outputs" / "done.txt").exists()
    assert json.loads((ctx.workdir / ".labhq" / "mcp.json").read_text()) == {"mcpServers": {}}

    ctx2, _ = _ctx(tmp_path, cli, resume="ba-1")
    cmd = get_adapter(Engine.cli, ctx2.settings).build_command(ctx2)
    assert cmd[-2:] == ["--resume", "ba-1"] and cmd[4].endswith("prompt.md")


async def test_missing_binary_fails_cleanly(tmp_path):
    ctx, _ = _ctx(tmp_path, CliSpec(command=["definitely-not-installed-bioinfo-agent"]))
    res = await get_adapter(Engine.cli, ctx.settings).run(ctx)
    assert not res.ok and "executable not found" in res.error


async def test_jsonl_usage_keeps_cumulative_token_counters(tmp_path):
    script = tmp_path / "agent.py"
    events = [
        {"type": "usage", "tokens": {"input_tokens": 2, "cache_read_tokens": 1}},
        {"type": "usage", "tokens": {"input_tokens": 7, "output_tokens": 4, "custom_tokens": 0}},
        {"type": "usage", "tokens": {"input_tokens": -1, "output_tokens": True, "bad": "9"}},
        {"type": "usage", "cost_usd": 0.3},
        {"type": "result", "ok": True, "text": "done"},
    ]
    script.write_text("import json\n" + "\n".join(f"print({json.dumps(json.dumps(ev))})" for ev in events))
    ctx, emitted = _ctx(tmp_path, CliSpec(command=[sys.executable, str(script)], output="jsonl"))
    result = await get_adapter(Engine.cli, ctx.settings).run(ctx)
    assert result.ok and result.cost_usd == 0.3
    assert result.usage == {"input_tokens": 7, "cache_read_tokens": 1, "output_tokens": 4, "custom_tokens": 0}
    assert any(t == "agent.usage" and d.get("tokens", {}).get("input_tokens") == 7
               for t, d in emitted if isinstance(d.get("tokens"), dict))


async def test_cli_usage_reaches_manifest_and_request(tmp_path, monkeypatch):
    from labhq.gateway.server import create_app
    from labhq.runner.daemon import Runner

    script = tmp_path / "agent.py"
    script.write_text('print(\'{"type":"status","state":"working","model_id":"resolved-cli-2026"}\')\n'
                      'print(\'{"type":"usage","tokens":{"input_tokens":7,"output_tokens":4}}\')\n'
                      'print(\'{"type":"result","ok":true,"text":"done"}\')\n')
    settings = Settings()
    settings.runner.state_dir = settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.cli, builtin_mcp=[],
                      cli=CliSpec(command=[sys.executable, str(script)], output="jsonl"))
    runner = Runner(settings)
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    task = Task(agent_id=agent.id, request_id="r", prompt="test")
    result = await runner.run_task(task)
    manifest = json.loads((runner.workspaces[task.id].dir / "manifest.json").read_text(encoding="utf-8"))
    assert result.ok
    assert manifest["runs"][task.id]["usage"] == {"input_tokens": 7, "output_tokens": 4}
    assert manifest["runs"][task.id]["model_id"] == "resolved-cli-2026"
    assert result.provenance["runs"][task.id]["model_id"] == "resolved-cli-2026"
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running"}
    await hub.on_runner_message("runner", {"type": "task.result", "task_id": task.id,
                                          "request_id": "r", "data": result.model_dump()})
    assert hub.request_summary(hub.requests["r"])["usage"] == {"input_tokens": 7, "output_tokens": 4}


async def test_a_cli_that_never_reads_its_stdin_still_times_out_and_gets_the_stall_warning(tmp_path, monkeypatch):
    """PR #433 review: the prompt was written before the timeout and the stall watch began, so a CLI that never read a
    prompt larger than the pipe buffer held labhq forever."""
    import asyncio
    import labhq.adapters.base as base

    async def no_prompts():
        return False

    monkeypatch.setattr(base, "pending_uac_prompts", no_prompts)
    script = tmp_path / "deaf-agent"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    cli = CliSpec(command=[sys.executable, str(script)], stdin="prompt", output="jsonl")
    ctx, events = _ctx(tmp_path, cli)
    ctx.prompt = "x" * 4_000_000  # far past any pipe buffer
    ctx.settings.runner.task_timeout_s = 3
    ctx.settings.runner.stall_warn_s = 1

    res = await asyncio.wait_for(get_adapter(Engine.cli, ctx.settings).run(ctx), 45)

    assert not res.ok and "timeout after 3s" in (res.error or "")
    assert any(t == "agent.log" and d.get("level") == "warn" and "출력이" in d.get("text", "") for t, d in events)
