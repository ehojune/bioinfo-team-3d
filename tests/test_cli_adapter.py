"""engine: cli — an external agent (like bioinfo-agent) speaking the labhq JSONL protocol."""

import json
import stat
import sys
from pathlib import Path

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.settings import Settings

AGENT = r'''
import json, sys, pathlib
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
    script.write_text(f"#!{sys.executable}\n{AGENT}")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    cli = CliSpec(command=[str(script), "run", "--task", "{prompt_file}", "--outputs", "{outputs}"],
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
    assert cmd[-2:] == ["--resume", "ba-1"] and cmd[3].endswith("prompt.md")


async def test_missing_binary_fails_cleanly(tmp_path):
    ctx, _ = _ctx(tmp_path, CliSpec(command=["definitely-not-installed-bioinfo-agent"]))
    res = await get_adapter(Engine.cli, ctx.settings).run(ctx)
    assert not res.ok and "executable not found" in res.error
