"""Run each real adapter against a fake CLI that replays that CLI's JSON event stream."""

import errno
import json
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
    events.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in FAKES[name]) + "\n", encoding="utf-8")
    script = tmp / name
    script.write_text(
        "# coding: utf-8\nimport sys, json, pathlib\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        f"pathlib.Path({str(tmp / (name + '.argv'))!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.stdout.write(open({str(events)!r}, encoding='utf-8').read())\n",
        encoding="utf-8",
    )
    return script


async def _run(tmp: Path, engine: Engine, name: str, schema: dict | None = None):
    s = Settings()
    s.policy.approvals.timeout_s = 15
    getattr(s.engines, engine.value).bin = sys.executable
    getattr(s.engines, engine.value).prefix_args = [str(_fake_cli(tmp, name))]
    if engine == Engine.codex:  # hermetic: the host's ~/.codex/AGENTS.md would refuse the staff session
        (tmp / "codex-home").mkdir()
        s.engines.codex.env = {"CODEX_HOME": str(tmp / "codex-home")}
    agent = AgentSpec(id="a1", name="A", role="r", engine=engine, model="m", tools=["Read", "Bash(ls *)"],
                      system_prompt="ROLE")
    task = Task(agent_id="a1", prompt="do it", output_schema=schema)
    events = []
    run_fields = {}

    async def emit(t, d):
        events.append((t, d))

    wd = tmp / f"wd_{name}"
    wd.mkdir()
    mcp = [McpServerSpec(name="labhq_approval", command="python", args=["-m", "x"], env={"K": "v"}),
           McpServerSpec(name="paper_x", command="/venv/bin/python", args=["server.py"], cwd="/opt/x")]
    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=s, mcp_servers=mcp, env={}, emit=emit,
                     prompt="do it", extra_dirs=["/data/proj"], claude_settings={"permissions": {"deny": ["Read(//d/**)"]}},
                     use_permission_tool=True, record_run=lambda **fields: run_fields.update(fields))
    res = await get_adapter(engine, s).run(ctx)
    argv = json.loads((tmp / f"{name}.argv").read_text())
    return res, argv, events, wd, run_fields


async def test_claude_code_adapter(tmp_path):
    res, argv, events, wd, run_fields = await _run(tmp_path, Engine.claude_code, "claude", {"type": "object"})
    assert res.ok and res.text == "분석 완료" and res.session_id == "sess-1" and res.cost_usd == 0.42
    assert res.structured == {"verdict": "accept"}
    assert argv[:2] == ["-p", "do it"]
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__labhq_approval__approval_prompt"
    assert argv[-4:] == ["--allowedTools", "Read", "Bash(ls *)"][-3:] or argv[-3:] == ["--allowedTools", "Read", "Bash(ls *)"]
    cfg = json.loads((wd / ".labhq" / "mcp.json").read_text())["mcpServers"]
    assert cfg["paper_x"]["command"] == "bash" and "cd /opt/x" in cfg["paper_x"]["args"][1]
    assert cfg["labhq_approval"]["timeout"] == 135_000 and "timeout" not in cfg["paper_x"]
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["permissions"]["deny"] == ["Read(//d/**)", "Agent", "Task", "Workflow", "TeamCreate",
                                               "TeamDelete", "SendMessage", "ListAgents"]
    assert run_fields["model_id"] == "claude-opus"
    assert any(t == "agent.tool" for t, _ in events)


async def test_codex_adapter(tmp_path):
    res, argv, events, wd, run_fields = await _run(tmp_path, Engine.codex, "codex", {"type": "object"})
    assert res.ok and res.session_id == "thr-9" and res.structured == {"verdict": "revise"}
    assert argv[0] == "exec" and "--json" in argv and argv[argv.index("-s") + 1] == "workspace-write"
    assert 'mcp_servers.labhq_approval.env={K = "v"}' in argv
    assert "mcp_servers.labhq_approval.tool_timeout_sec=135" in argv
    assert not any("mcp_servers.paper_x.tool_timeout_sec" in arg for arg in argv)
    assert argv[-1].startswith("<task>") and "<structured_output_contract>" in argv[-1]
    assert (wd / "AGENTS.md").read_text(encoding="utf-8").startswith("ROLE")
    assert "model_id" not in run_fields


async def test_gemini_adapter(tmp_path):
    res, argv, events, wd, run_fields = await _run(tmp_path, Engine.gemini, "gemini")
    assert res.ok and res.text == "문헌 3편 요약\n" and res.session_id == "g-1"
    assert argv[:2] == ["-p", "do it"] and "--include-directories" in argv
    settings = json.loads((wd / ".gemini" / "settings.json").read_text())
    assert set(settings["mcpServers"]) == {"labhq_approval", "paper_x"}
    assert run_fields["model_id"] == "gemini-pro"


async def _run_long_prompt(tmp: Path, monkeypatch, *, pointer: str | None, prompt: str = "x" * 8000):
    """A prompt that would push the command line past a (lowered) Windows limit (#222 rerun)."""
    from labhq.adapters import base
    monkeypatch.setattr(base, "command_line_limit", lambda: 6000, raising=False)
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(_fake_cli(tmp, "claude"))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m", tools=["Read"],
                      system_prompt="ROLE")
    task = Task(agent_id="a1", prompt="long", output_schema={"type": "object"})
    wd = tmp / "wd"
    wd.mkdir()

    async def emit(t, d):
        pass

    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=s, mcp_servers=[], env={}, emit=emit,
                     prompt=prompt, prompt_pointer=pointer)
    res = await get_adapter(Engine.claude_code, s).run(ctx)
    argv_file = tmp / "claude.argv"
    return res, json.loads(argv_file.read_text()) if argv_file.exists() else None


async def test_posix_argument_byte_limit_goes_by_task_file(tmp_path, monkeypatch):
    from labhq.adapters import base
    monkeypatch.setattr(base, "command_line_limit", lambda: None)
    monkeypatch.setattr(base, "argument_byte_limit", lambda: 6000)
    pointer = "Read TASK.md in the current directory (it is long) and carry out the instruction there."
    res, argv = await _run_long_prompt(
        tmp_path, monkeypatch, pointer=pointer, prompt="한" * 2500,
    )
    assert res.ok, res.error
    assert argv[argv.index("-p") + 1] == pointer


async def test_spawn_e2big_is_a_failed_result(tmp_path, monkeypatch):
    from labhq.adapters import base

    async def e2big(*args, **kwargs):
        raise OSError(errno.E2BIG, "argument list too long")

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", e2big)
    res, argv = await _run_long_prompt(tmp_path, monkeypatch, pointer=None, prompt="short")
    assert argv is None
    assert not res.ok and "argument list too long" in res.error.lower()


async def test_claude_pointer_prompt_requires_read_builtin(tmp_path, monkeypatch):
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(_fake_cli(tmp_path, "claude"))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m",
                      builtin_tools="Glob,Grep", system_prompt="ROLE")
    task = Task(agent_id="a1", prompt="long")
    wd = tmp_path / "wd"
    wd.mkdir()

    async def emit(t, d):
        pass

    pointer = "Read TASK.md in the current directory (it is long) and carry out the instruction there."
    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=s, mcp_servers=[], env={}, emit=emit,
                     prompt=pointer, prompt_pointer=pointer)
    res = await get_adapter(Engine.claude_code, s).run(ctx)
    assert not (tmp_path / "claude.argv").exists()
    assert not res.ok and "Read" in res.error and "long" in res.error


async def test_prompt_over_the_command_line_limit_goes_by_task_file(tmp_path, monkeypatch):
    # Windows refused the CSO re-plan (33,091 characters) and Python called it a missing executable.
    pointer = "Read TASK_wake_t1.md in the current directory (it is long) and carry out the instruction there."
    res, argv = await _run_long_prompt(tmp_path, monkeypatch, pointer=pointer)
    assert res.ok, res.error
    assert argv[argv.index("-p") + 1] == pointer


async def test_command_line_still_too_long_is_reported_as_such(tmp_path, monkeypatch):
    res, argv = await _run_long_prompt(tmp_path, monkeypatch, pointer=None)
    assert argv is None  # never started
    assert not res.ok and "command line" in res.error and "executable not found" not in res.error


async def test_command_line_limit_counts_utf16_code_units(tmp_path, monkeypatch):
    # CreateProcessW counts UTF-16 units: 2,500 non-BMP characters are 5,000 units; the rest of the command
    # (about 1,600 characters) keeps a character count under the 6,000 limit and the unit count over it.
    pointer = "Read TASK.md in the current directory (it is long) and carry out the instruction there."
    res, argv = await _run_long_prompt(tmp_path, monkeypatch, pointer=pointer, prompt="\U0001F9EC" * 2500)
    assert res.ok, res.error
    assert argv[argv.index("-p") + 1] == pointer


async def test_settings_move_to_a_file_when_the_pointer_is_not_enough(tmp_path, monkeypatch):
    # v0.5 trial (2026-10-08): with the prompt already a pointer, settings, schema and per-folder rules still
    # passed 32,000 units for a step with many upstream folders, and the QC step was refused.
    from labhq.adapters import base
    monkeypatch.setattr(base, "command_line_limit", lambda: 6000, raising=False)
    s = Settings()
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(_fake_cli(tmp_path, "claude"))]
    agent = AgentSpec(id="a1", name="A", role="r", engine=Engine.claude_code, model="m", tools=["Read"],
                      system_prompt="ROLE")
    task = Task(agent_id="a1", prompt="long", output_schema={"type": "object"})
    wd = tmp_path / "runs" / "task_1"
    wd.mkdir(parents=True)

    async def emit(t, d):
        pass

    deny = [f"Read(//c:/private/folder-{i}/**)" for i in range(300)]
    pointer = "Read TASK.md in the current directory (it is long) and carry out the instruction there."
    ctx = RunContext(task=task, agent=agent, workdir=wd, settings=s, mcp_servers=[], env={}, emit=emit,
                     prompt=pointer, prompt_pointer=pointer, claude_settings={"permissions": {"deny": deny}})
    res = await get_adapter(Engine.claude_code, s).run(ctx)

    assert res.ok, res.error
    argv = json.loads((tmp_path / "claude.argv").read_text())
    value = argv[argv.index("--settings") + 1]
    settings_file = Path(value)
    assert settings_file.parent == (tmp_path / "runs" / ".labhq-settings").resolve()  # beside, not inside
    assert deny[0] in json.loads(settings_file.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert not any(deny[0] in arg for arg in argv)


async def test_settings_stay_inline_when_the_command_fits(tmp_path, monkeypatch):
    pointer = "Read TASK.md in the current directory (it is long) and carry out the instruction there."
    res, argv = await _run_long_prompt(tmp_path, monkeypatch, pointer=pointer)
    assert res.ok, res.error
    assert argv[argv.index("--settings") + 1].startswith("{")
    assert not (tmp_path / ".labhq-settings").exists()
