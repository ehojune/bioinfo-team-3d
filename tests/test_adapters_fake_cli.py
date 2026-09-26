"""Run each real adapter against a fake CLI that replays that CLI's JSON event stream."""

import json
import stat
import sys
from pathlib import Path

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.settings import Settings

FAKES = {
    "claude": [
        {"type": "system", "subtype": "init", "session_id": "sess-1", "model": "claude-opus", "mcp_servers": [{"name": "labhq_hpc"}]},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__labhq_hpc__hpc_submit", "input": {"job_name": "x"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "분석 완료"}]}},
        {"type": "result", "subtype": "success", "result": "분석 완료", "session_id": "sess-1", "total_cost_usd": 0.42,
         "num_turns": 3, "structured_output": {"verdict": "accept"}},
    ],
    "codex": [
        {"type": "thread.started", "thread_id": "thr-9"},
        {"type": "item.started", "item": {"type": "command_execution", "command": "pytest -q"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{\"verdict\": \"revise\"}"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ],
    "gemini": [
        {"type": "init", "session_id": "g-1", "model": "gemini-pro"},
        {"type": "tool_use", "tool_name": "google_web_search", "parameters": {"query": "CD276"}},
        {"type": "message", "role": "assistant", "content": "문헌 3편 ", "delta": True},
        {"type": "message", "role": "assistant", "content": "요약\n", "delta": True},
        {"type": "result", "status": "success", "stats": {"total_tokens": 99}},
    ],
}


def _fake_cli(tmp: Path, name: str) -> Path:
    events = tmp / f"{name}.jsonl"
    events.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in FAKES[name]) + "\n")
    script = tmp / name
    script.write_text(
        f"#!{sys.executable}\nimport sys, json, pathlib\n"
        f"pathlib.Path({str(tmp / (name + '.argv'))!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.stdout.write(open({str(events)!r}).read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


async def _run(tmp: Path, engine: Engine, name: str, schema: dict | None = None):
    s = Settings()
    getattr(s.engines, engine.value).bin = str(_fake_cli(tmp, name))
    if engine == Engine.codex:  # hermetic: the host's ~/.codex/AGENTS.md would refuse the staff session
        (tmp / "codex-home").mkdir()
        s.engines.codex.env = {"CODEX_HOME": str(tmp / "codex-home")}
    agent = AgentSpec(id="a1", name="A", role="r", engine=engine, model="m", tools=["Read", "Bash(ls *)"],
                      system_prompt="ROLE")
    task = Task(agent_id="a1", prompt="do it", output_schema=schema)
    events = []

    async def emit(t, d):
        events.append((t, d))

    wd = tmp / f"wd_{name}"
    wd.mkdir()
    mcp = [McpServerSpec(name="labhq_approval", command="python", args=["-m", "x"], env={"K": "v"}),
           McpServerSpec(name="paper_x", command="/venv/bin/python", args=["server.py"], cwd="/opt/x")]
    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=s, mcp_servers=mcp, env={}, emit=emit,
                     prompt="do it", extra_dirs=["/data/proj"], claude_settings={"permissions": {"deny": ["Read(//d/**)"]}},
                     use_permission_tool=True)
    res = await get_adapter(engine, s).run(ctx)
    argv = json.loads((tmp / f"{name}.argv").read_text())
    return res, argv, events, wd


async def test_claude_code_adapter(tmp_path):
    res, argv, events, wd = await _run(tmp_path, Engine.claude_code, "claude", {"type": "object"})
    assert res.ok and res.text == "분석 완료" and res.session_id == "sess-1" and res.cost_usd == 0.42
    assert res.structured == {"verdict": "accept"}
    assert argv[:2] == ["-p", "do it"]
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__labhq_approval__approval_prompt"
    assert argv[-4:] == ["--allowedTools", "Read", "Bash(ls *)"][-3:] or argv[-3:] == ["--allowedTools", "Read", "Bash(ls *)"]
    cfg = json.loads((wd / ".labhq" / "mcp.json").read_text())["mcpServers"]
    assert cfg["paper_x"]["command"] == "bash" and "cd /opt/x" in cfg["paper_x"]["args"][1]
    assert any(t == "agent.tool" for t, _ in events)


async def test_codex_adapter(tmp_path):
    res, argv, events, wd = await _run(tmp_path, Engine.codex, "codex", {"type": "object"})
    assert res.ok and res.session_id == "thr-9" and res.structured == {"verdict": "revise"}
    assert argv[0] == "exec" and "--json" in argv and argv[argv.index("-s") + 1] == "workspace-write"
    assert 'mcp_servers.labhq_approval.env={K = "v"}' in argv
    assert argv[-1].startswith("<task>") and "<structured_output_contract>" in argv[-1]
    assert (wd / "AGENTS.md").read_text().startswith("ROLE")


async def test_gemini_adapter(tmp_path):
    res, argv, events, wd = await _run(tmp_path, Engine.gemini, "gemini")
    assert res.ok and res.text == "문헌 3편 요약\n" and res.session_id == "g-1"
    assert argv[:2] == ["-p", "do it"] and "--include-directories" in argv
    settings = json.loads((wd / ".gemini" / "settings.json").read_text())
    assert set(settings["mcpServers"]) == {"labhq_approval", "paper_x"}
