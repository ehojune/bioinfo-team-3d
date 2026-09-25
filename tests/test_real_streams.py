"""Replay sanitized streams captured from real Windows CLIs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.settings import Settings
from scripts.redact_stream import Redactor

ROOT = Path(__file__).parent / "fixtures" / "real"

CASES = [
    ("claude_code", "claude_auth_expired", 1, False,
     "Failed to authenticate: OAuth session expired and could not be refreshed", "Failed to authenticate", 0, "<ID_3>"),
    ("codex", "codex_simple", 0, True, "LABHQ_P1_OK", None, 0, "<ID_1>"),
    ("codex", "codex_mcp_call", 0, False,
     "I\u2019ll call the echo tool and return its result exactly.MCP tool call requires approval, but approval policy is never",
     "approval policy is never", 1, "<ID_1>"),
    ("codex", "codex_mcp_call_approved", 0, True,
     "I\u2019ll call the echo tool and return its response verbatim.ECHO:p1", None, 1, "<ID_1>"),
    ("gemini", "gemini_cli_ineligible", 0, False, "", "Gemini CLI 개인 계정", 0, None),
    ("antigravity", "agy_simple", 0, True, "LABHQ_P1_OK\n", None, 0, "<ID_1>"),
    ("antigravity", "agy_bad_model", 1, False, "", "invalid model selection", 0, "<ID_1>"),
    ("antigravity", "agy_tool_allowed", 0, True, "2\n", None, 3, "<ID_1>"),
    ("antigravity", "agy_tool_denied", 0, False, "", "헤드리스에서 거부됨", 1, "<ID_1>"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine,case,exit_code,ok,text,error,tool_count,session_id", CASES)
async def test_captured_stream(tmp_path, engine, case, exit_code, ok, text, error, tool_count, session_id):
    settings = Settings()
    agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=Engine(engine), builtin_mcp=[])
    task = Task(agent_id=agent.id, prompt="test")
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="test")
    adapter = get_adapter(agent.engine, settings)
    state = RunState()
    fixture = ROOT / engine / (case + ".jsonl")
    for line in fixture.read_text(encoding="utf-8").splitlines():
        json.loads(line)
        await adapter.handle_line(line, state, ctx)
    stderr_file = fixture.with_suffix(".stderr.txt")
    if stderr_file.exists():
        state.error = state.error or adapter.stderr_error(stderr_file.read_text(encoding="utf-8"))
    result = adapter.finalize(state, ctx, exit_code)
    assert result.ok is ok
    assert result.text == text
    assert (error is None) or (error in (result.error or ""))
    assert result.session_id == session_id
    assert sum(kind == "agent.tool" for kind, _ in events) == tool_count
    if case in ("codex_mcp_call", "agy_tool_denied", "agy_tool_allowed"):
        assert any(kind == "agent.tool_error" for kind, _ in events)


def test_fixture_privacy_and_valid_json():
    for fixture in ROOT.rglob("*"):
        if not fixture.is_file():
            continue
        text = fixture.read_text(encoding="utf-8")
        assert "admin" not in text.lower()
        assert "@" not in text or not __import__("re").search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        if fixture.suffix == ".jsonl":
            for line in text.splitlines():
                json.loads(line)


def test_redactor_paths_ids_secrets():
    red = Redactor(home=r"C:\Users\admin", tmp=r"C:\Users\admin\AppData\Local\Temp",
                   workdir=r"C:\Users\admin\Desktop\labhq", username="admin")
    source = {"session_id": "abc", "thread_id": "abc", "message": r"C:\Users\admin\Desktop\labhq\x "
              + r"C:/Users/admin/AppData/Local/Temp/y user@example.org " + "sk-" + "12345678901234567890"}
    cleaned = red.object(source)
    assert cleaned["session_id"] == cleaned["thread_id"]
    assert "<WORKDIR>" in cleaned["message"] and "<TMP>" in cleaned["message"]
    assert "<EMAIL>" in cleaned["message"] and "<REDACTED>" in cleaned["message"]
    assert "admin" not in json.dumps(cleaned).lower()


def test_empty_success_stream_fails(tmp_path):
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.gemini, builtin_mcp=[])
    task = Task(agent_id="a", prompt="test")
    async def emit(kind, data):
        pass
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=Settings(),
                     mcp_servers=[], env={}, emit=emit, prompt="test")
    result = get_adapter(agent.engine, ctx.settings).finalize(RunState(), ctx, 0)
    assert not result.ok and "empty CLI stream" in result.error


def test_codex_approves_only_builtin_mcp(tmp_path):
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.codex, builtin_mcp=[])
    task = Task(agent_id="a", prompt="test")
    async def emit(kind, data):
        pass
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[McpServerSpec(name="labhq_hpc", command="x"),
                                  McpServerSpec(name="external", command="y")],
                     env={}, emit=emit, prompt="test")
    cmd = get_adapter(agent.engine, settings).build_command(ctx)
    assert 'mcp_servers.labhq_hpc.default_tools_approval_mode="approve"' in cmd
    assert not any("mcp_servers.external.default_tools_approval_mode" in arg for arg in cmd)


def test_antigravity_rejects_mcp_at_load():
    with pytest.raises(ValidationError, match="does not support"):
        AgentSpec(id="a", name="A", role="r", engine=Engine.antigravity)
    with pytest.raises(ValidationError, match="does not support"):
        AgentSpec(id="a", name="A", role="r", engine=Engine.antigravity, builtin_mcp=[],
                  mcp=[McpServerSpec(name="external", command="x")])


@pytest.mark.parametrize("mode,expected", [
    ("plan", ["--sandbox"]), ("default", ["--sandbox"]), ("manual", ["--sandbox"]),
    ("acceptEdits", ["--sandbox", "--dangerously-skip-permissions"]),
    ("auto", ["--sandbox", "--dangerously-skip-permissions"]),
    ("bypassPermissions", ["--dangerously-skip-permissions"]),
])
def test_antigravity_command(tmp_path, mode, expected):
    settings = Settings()
    settings.runner.task_timeout_s = 37
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.antigravity,
                      model="gemini-3.8-flash-high", builtin_mcp=[], permission_mode=mode,
                      system_prompt="ROLE")
    task = Task(agent_id="a", prompt="test", output_schema={"type": "object"})
    async def emit(kind, data):
        pass
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="TASK")
    adapter = get_adapter(agent.engine, settings)
    adapter.prepare(ctx)
    cmd = adapter.build_command(ctx)
    assert cmd[:2] == ["agy", "-p"] and cmd[2].startswith("ROLE") and "TASK" in cmd[2]
    assert [arg for arg in cmd if arg in ("--sandbox", "--dangerously-skip-permissions")] == expected
    assert cmd[cmd.index("--print-timeout") + 1] == "37s"
    assert Path(cmd[cmd.index("--json-schema") + 1]).exists()


def _init_payloads():
    for fixture in ROOT.rglob("*.jsonl"):
        for line in fixture.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                yield fixture, ev
            elif ev.get("event") == "init":
                yield fixture, ev.get("init") or {}


def test_init_events_keep_only_allowlisted_fields():
    """init events list the capturing machine's skills, agents and MCP servers; fixtures must not."""
    from scripts.redact_stream import INIT_COUNT, INIT_KEEP
    seen = 0
    for fixture, init in _init_payloads():
        seen += 1
        extra = set(init) - INIT_KEEP - INIT_COUNT - {"mcp_servers"}
        assert not extra, (fixture, extra)
        for key in INIT_COUNT & set(init):
            assert isinstance(init[key], str) and init[key].endswith(" items>"), (fixture, key)
        for server in init.get("mcp_servers", []):
            assert str(server.get("name", "")).startswith("labhq_"), (fixture, server)
    assert seen >= 4


def test_fixtures_hide_external_mcp_names():
    import re
    for fixture in ROOT.rglob("*"):
        if fixture.is_file():
            for name in re.findall(r"mcp__[^\s\"']*", fixture.read_text(encoding="utf-8")):
                assert name.startswith(("mcp__labhq_", "mcp__<external>__")), (fixture, name)


def test_redactor_prunes_init_and_masks_external_tools():
    r = Redactor(home="C:/Users/someone", tmp="C:/Temp", workdir="", username="someone")
    init = json.loads(r.line(json.dumps({"type": "system", "subtype": "init", "model": "m",
                                         "skills": ["private-cluster"], "mcp_servers": [{"name": "example_saas"}, {"name": "labhq_hpc"}],
                                         "messaging_socket_path": "x"})))
    assert init == {"type": "system", "subtype": "init", "model": "m", "skills": "<1 items>",
                    "mcp_servers": [{"name": "labhq_hpc"}]}
    tool = r.line(json.dumps({"name": "mcp__example_saas__search", "other": "mcp__labhq_hpc__hpc_status"}))
    assert "example_saas" not in tool and "mcp__labhq_hpc__hpc_status" in tool


@pytest.mark.asyncio
async def test_review_fixes_from_p1_pr(tmp_path):
    """Regression cases from the independent review of PR #5."""
    from labhq.models import CliSpec

    settings = Settings()

    async def emit(kind, data):
        pass

    async def run(engine, lines, returncode=0, **agent_kw):
        agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=Engine(engine), builtin_mcp=[], **agent_kw)
        ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                         mcp_servers=[], env={}, emit=emit, prompt="x")
        adapter, state = get_adapter(agent.engine, settings), RunState()
        for line in lines:
            await adapter.handle_line(json.dumps(line, ensure_ascii=False), state, ctx)
        return adapter.finalize(state, ctx, returncode)

    denied = await run("antigravity", [{"event": "result", "result": {
        "status": "SUCCESS", "response": "no permission", "denied_actions": [{"action": "command"}]}}])
    assert not denied.ok and denied.error

    empty_ok = await run("cli", [{"type": "result", "ok": True, "text": ""}],
                         cli=CliSpec(command=["agent"], output="jsonl"))
    assert empty_ok.ok, empty_ok.error

    recovered = await run("codex", [
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "labhq_hpc", "tool": "hpc_status",
                                            "status": "failed", "error": {"message": "scheduler timeout"}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "fell back to cached status"}},
        {"type": "turn.completed", "usage": {}},
    ])
    assert recovered.ok, recovered.error

    r = Redactor(home="C:/Users/someone", tmp="C:/Temp", workdir="", username="someone")
    masked = json.loads(r.line(json.dumps({"item": {"server": "my_private_db", "tool": "query"},
                                           "other": {"server": "labhq_hpc"}})))
    assert masked["item"]["server"] == "<external>" and masked["other"]["server"] == "labhq_hpc"


CLAUDE_FLOWS = [
    # case, final text (substring), Write/Read tool_use count, tool_error substring or None
    ("claude_simple", "LABHQ_P1_OK", 0, None),
    ("claude_permission_invalid_result", "NOT_WRITTEN", 1, "Permission prompt tool returned an invalid result"),
    ("claude_write_inside", "WROTE", 1, None),
    ("claude_write_ask_approved", "WROTE", 1, None),
    ("claude_write_ask_denied", "NOT_WRITTEN", 1, "P1 deny probe"),
    ("claude_settings_deny_enforced", "denied", 1, "denied by your permission settings"),
    ("claude_settings_deny_windows_failopen", "P1_SECRET_CANARY_4417", 1, None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case,text,tool_uses,tool_error", CLAUDE_FLOWS)
async def test_claude_real_flows(tmp_path, case, text, tool_uses, tool_error):
    """Claude 2.1.282 through the labhq approval MCP and --settings deny rules (Windows 11, real account)."""
    settings = Settings()
    agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=Engine("claude_code"), builtin_mcp=[])
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x")
    adapter, state = get_adapter(agent.engine, settings), RunState()
    for line in (ROOT / "claude_code" / f"{case}.jsonl").read_text(encoding="utf-8").splitlines():
        await adapter.handle_line(line, state, ctx)
    result = adapter.finalize(state, ctx, 0)
    assert result.ok, result.error
    assert text in (result.text or "")
    assert result.session_id
    assert sum(kind == "agent.tool" for kind, _ in events) == tool_uses
    errors = [str(d.get("text")) for kind, d in events if kind == "agent.tool_error"]
    assert (any(tool_error in e for e in errors) if tool_error else not errors), errors


def test_claude_rule_path_matches_claude_normalisation():
    from labhq.policy import claude_rule_path
    assert claude_rule_path("C:\\Users\\x\\restricted") == "/c/Users/x/restricted"
    assert claude_rule_path("D:/data/cohort/") == "/d/data/cohort"
    assert claude_rule_path("/data/cohort") == "/data/cohort"
    assert claude_rule_path("C:\\") == "/c"


def test_claude_system_events_keep_only_identifiers():
    """commands_changed and hook events carried the capturing user's command descriptions (P1 leak)."""
    from scripts.redact_stream import INIT_COUNT, INIT_KEEP, SYSTEM_KEEP
    seen = 0
    for fixture in (ROOT / "claude_code").glob("*.jsonl"):
        for line in fixture.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("type") != "system":
                continue
            seen += 1
            allowed = INIT_KEEP | INIT_COUNT | {"mcp_servers"} if ev.get("subtype") == "init" else SYSTEM_KEEP
            for key, value in ev.items():
                if key not in allowed:
                    assert isinstance(value, str) and (value == "" or value.startswith("<")), (fixture.name, key)
    assert seen >= 8


def test_redactor_prunes_other_system_events():
    r = Redactor(home="C:/Users/someone", tmp="C:/Temp", workdir="", username="someone")
    ev = json.loads(r.line(json.dumps({"type": "system", "subtype": "commands_changed", "session_id": "s",
                                       "commands": [{"name": "private-cluster", "description": "ssh to host x"}]})))
    assert ev["commands"] == "<1 items>" and "private-cluster" not in json.dumps(ev)
    hook = json.loads(r.line(json.dumps({"type": "system", "subtype": "hook_response", "hook_name": "SessionStart:startup",
                                         "stdout": "secret project list", "output": "", "exit_code": 0})))
    assert hook["stdout"] == "<19 chars>" and hook["output"] == "" and hook["exit_code"] == 0
