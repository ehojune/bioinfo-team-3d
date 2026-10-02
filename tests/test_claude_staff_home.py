"""Claude staff get their own config folder (engines.claude_code.env.CLAUDE_CONFIG_DIR, #298 ⑤, PI decision 2026-10-03).

Claude saves long tool output under <config>/projects/<slug of its working folder>/ and reads it back with Read.
The PI's ~/.claude stays closed to staff as a whole; in the staff folder only this task's project folder opens, and
only for reading. Without CLAUDE_CONFIG_DIR nothing changes."""

import json
import os
import sys
from pathlib import Path

import pytest

import labhq.private_paths as private_paths
from labhq import doctor
from labhq.adapters.base import RunContext, role_footer
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import AgentSpec, Engine, Task
from labhq.policy import claude_deny_private, claude_rule_path, evaluate_tool
from labhq.private_paths import (ENV_VAR, OPEN_READS_ENV_VAR, claude_project_slug, gate_private_paths,
                                 resolve_private_paths, staff_claude_project_dirs)
from labhq.settings import PolicySettings, Settings
from tests.test_private_paths import _capture_runner, _home, _link_dir, _no_short_names, _runner_settings  # noqa: F401

REAL_LABHQ_ENTRIES = private_paths._labhq_entries  # conftest replaces it for every test
FIXTURE = Path(__file__).parent / "fixtures" / "real" / "claude_code" / "claude_project_slug.json"
STAFF_LABEL = "~/.labhq/claude-staff"


def _rule(path) -> str:
    return "/" + claude_rule_path(str(path))


def _staff(tmp_path, monkeypatch, *, private=None, config=None):
    """The PI home with ~/.claude, and a staff config folder holding a login, history and two tasks' folders."""
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    (home / ".claude" / "projects").mkdir(parents=True)
    staff = home / ".labhq" / "claude-staff"
    ws = home / ".labhq" / "runs" / "t1"
    ws.mkdir(parents=True)
    mine = staff / "projects" / claude_project_slug(str(ws))
    (mine / "s1" / "tool-results").mkdir(parents=True)
    (mine / "s1" / "tool-results" / "out.txt").write_text("long output", encoding="utf-8")
    other = staff / "projects" / claude_project_slug(str(home / ".labhq" / "runs" / "t2"))
    other.mkdir(parents=True)
    (other / "s2.jsonl").write_text("another task", encoding="utf-8")
    for name in (".credentials.json", ".claude.json", "history.jsonl"):
        (staff / name).write_text("staff secret", encoding="utf-8")
    env = {} if config == "" else {"CLAUDE_CONFIG_DIR": config or str(staff)}
    data = {"engines": {"claude_code": {"bin": "claude", "env": env}},
            "gateway": {"state_dir": str(tmp_path / "gateway")}}
    if private is not None:
        data["policy"] = {"private_paths": private}
    return home, staff, ws, mine, other, Settings.model_validate(data)


def _found(settings, ws):
    return resolve_private_paths(settings, [ws], open_reads=staff_claude_project_dirs(settings, ws))


def _gate(found, home, ws, tool, tool_input):
    return evaluate_tool(tool, tool_input, PolicySettings(), allowed_roots=[str(ws)], workdir=str(ws), environ={},
                         private_paths=found.paths, private_open_reads=found.open_reads,
                         private_enabled=found.enabled, home=str(home))


# ---------------- the folder Claude saves output in ----------------

def test_the_project_slug_is_the_rule_the_real_claude_used():
    """Probed on Claude 2.1.282 / Windows 11 (fixture): every character but ASCII letters and digits becomes `-`;
    past 200 characters the first 200 plus `-` and base36 |djb2| of the folder path."""
    probes = json.loads(FIXTURE.read_text(encoding="utf-8"))["probes"]
    assert len(probes) == 2 and len(probes[1]["slug"]) > 200
    for probe in probes:
        assert claude_project_slug(probe["cwd"]) == probe["slug"]
    assert claude_project_slug("/home/pi/.labhq/runs/t1") == "-home-pi--labhq-runs-t1"


def test_the_staff_folder_is_private_and_only_this_tasks_project_folder_opens(tmp_path, monkeypatch):
    home, staff, ws, mine, _other, settings = _staff(tmp_path, monkeypatch)
    opened = staff_claude_project_dirs(settings, ws)
    projects = os.path.realpath(staff / "projects")  # the real spelling is opened too (8.3 names, links)
    assert str(mine) in opened and all(os.path.realpath(Path(p).parent) == projects for p in opened)
    found = _found(settings, ws)
    assert {"~/.claude", STAFF_LABEL} <= set(found.labels) and str(staff) in found.paths
    assert found.open_reads == tuple(opened)
    # `${VAR}` in the engine env expands like the CLI's own env does.
    monkeypatch.setenv("LABHQ_TEST_STAFF_HOME", str(home))
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "${LABHQ_TEST_STAFF_HOME}/.labhq/claude-staff"}
    assert [Path(p) for p in staff_claude_project_dirs(settings, ws)] == [Path(p) for p in opened]


def test_the_gate_lets_claude_read_its_saved_output_and_nothing_else_in_the_staff_folder(tmp_path, monkeypatch):
    home, staff, ws, mine, other, settings = _staff(tmp_path, monkeypatch)
    found = _found(settings, ws)
    saved = mine / "s1" / "tool-results" / "out.txt"
    for tool, tool_input in (("Read", {"file_path": str(saved)}), ("Grep", {"pattern": "long", "path": str(mine)}),
                             ("Glob", {"pattern": "**/*.txt", "path": str(mine)})):
        assert _gate(found, home, ws, tool, tool_input).action == "allow", (tool, tool_input)
    closed = [("Read", {"file_path": str(staff / ".credentials.json")}),
              ("Read", {"file_path": str(staff / ".claude.json")}),
              ("Read", {"file_path": str(staff / "history.jsonl")}),
              ("Read", {"file_path": str(other / "s2.jsonl")}),
              ("Read", {"file_path": str(mine / ".." / other.name / "s2.jsonl")}),
              ("Read", {"file_path": str(mine) + "-x" + os.sep + "y"}),  # a longer slug sharing the prefix
              ("Glob", {"pattern": "../*", "path": str(mine)}),
              ("Glob", {"pattern": "*/s2.jsonl", "path": str(staff / "projects")}),
              ("Grep", {"pattern": "x", "path": str(staff)}),
              ("Write", {"file_path": str(mine / "s1" / "tool-results" / "new.txt"), "content": "x"}),
              ("Edit", {"file_path": str(saved), "old_string": "long", "new_string": "short"}),
              ("Read", {"file_path": str(home / ".claude" / "projects" / mine.name / "x.jsonl")})]
    for tool, tool_input in closed:
        decision = _gate(found, home, ws, tool, tool_input)
        assert decision.action == "deny" and "private_paths" in decision.reason, (tool, tool_input)
    # A shell command naming the folder still goes to the PI: shell can write.
    assert _gate(found, home, ws, "Bash", {"command": f"cat {saved}"}).action == "ask"


WIN = "C:\\Users\\pi"
WIN_STAFF = WIN + "\\.labhq\\claude-staff"
WIN_WS = WIN + "\\.labhq\\runs\\t1"
WIN_MINE = WIN_STAFF + "\\projects\\" + claude_project_slug(WIN_WS)


@pytest.mark.parametrize("path,action", [
    (WIN_MINE + "\\s1\\tool-results\\out.txt", "allow"),
    ((WIN_MINE + "\\S1\\TOOL-RESULTS\\OUT.TXT").upper(), "allow"),  # the same file on Windows
    (WIN_STAFF.upper() + "\\.CREDENTIALS.JSON", "deny"),
    ("c:/users/PI/.LABHQ/Claude-Staff/History.jsonl", "deny"),
    (WIN_MINE.lower() + "\\..\\..\\.credentials.json", "deny"),
    (WIN_MINE + "-other\\s.jsonl", "deny"),
    ("\\\\localhost\\C$\\Users\\pi\\.labhq\\claude-staff\\.credentials.json", "deny"),
    ("\\\\?\\" + WIN_STAFF + "\\.claude.json", "deny"),
    (WIN + "\\.CLAUDE\\projects\\" + claude_project_slug(WIN_WS) + "\\x.jsonl", "deny"),
])
def test_case_and_prefix_spellings_do_not_widen_the_open_folder(path, action):
    decision = evaluate_tool("Read", {"file_path": path}, PolicySettings(), allowed_roots=[WIN_WS], workdir=WIN_WS,
                             windows=False, environ={}, private_paths=[WIN_STAFF, WIN + "\\.claude"],
                             private_open_reads=[WIN_MINE], home=WIN)
    assert decision.action == action, path


def test_a_link_out_of_the_open_folder_is_judged_by_where_it_leads(tmp_path, monkeypatch):
    home, staff, ws, mine, _other, settings = _staff(tmp_path, monkeypatch)
    _link_dir(mine / "alias", staff)
    _link_dir(ws / "to-staff", staff)
    _link_dir(ws / "to-mine", mine)
    found = _found(settings, ws)
    for path in (mine / "alias" / ".credentials.json", ws / "to-staff" / "history.jsonl"):
        assert _gate(found, home, ws, "Read", {"file_path": str(path)}).action == "deny", path
    # A link to its own saved output is still that output.
    to_mine = ws / "to-mine" / "s1" / "tool-results" / "out.txt"
    assert _gate(found, home, ws, "Read", {"file_path": str(to_mine)}).action == "allow"


# ---------------- Claude's own rules and pre-approvals ----------------

def test_claude_rules_close_the_staff_folder_except_the_open_project_folder(tmp_path, monkeypatch):
    home, staff, ws, mine, other, settings = _staff(tmp_path, monkeypatch)
    (staff / "history.jsonl").unlink()  # not written yet: still ruled
    (staff / "todos").mkdir()  # any other top-level entry is ruled as it stands
    found = _found(settings, ws)
    deny = claude_deny_private({}, found.paths, str(home), open_reads=found.open_reads)["permissions"]["deny"]
    assert f"Read({_rule(staff)})" not in deny and f"Read({_rule(staff)}/**)" not in deny
    for tool in ("Edit", "Write"):
        assert f"{tool}({_rule(staff)}/**)" in deny  # writes stay closed in the whole folder
    for path in (staff / ".credentials.json", staff / ".claude.json", staff / "history.jsonl", staff / "todos"):
        assert f"Read({_rule(path)})" in deny and f"Read({_rule(path)}/**)" in deny, path
    # Nothing under projects/ is named: other tasks' folders are left to the gate (it refuses them, test above).
    assert not any(rule.startswith(f"Read({_rule(staff / 'projects')}") for rule in deny), other
    assert f"Read({_rule(home / '.claude')}/**)" in deny  # the PI's folder stays closed as a whole


def _command(tmp_path, found, settings, tools=("Read", "Grep", "Glob", "Write", "Bash")):
    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.claude_code, tools=list(tools),
                      builtin_mcp=["approval"])
    ws = tmp_path / "cmd-ws"
    (ws / ".labhq").mkdir(parents=True, exist_ok=True)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=ws, settings=settings,
                     mcp_servers=[], env={}, emit=None, prompt="x",
                     claude_settings=claude_deny_private({}, found.paths, open_reads=found.open_reads),  # as the runner
                     private_paths=list(found.paths), private_enabled=found.enabled,
                     private_open_reads=list(found.open_reads))
    return ClaudeCodeAdapter(settings).build_command(ctx), ctx


def test_earlier_tasks_project_folders_do_not_lengthen_the_command(tmp_path, monkeypatch):
    """Every Claude task leaves a project folder in the staff config folder (its own work folder). Ruling each one
    by name grew --settings by ~340 characters a folder until no Claude task could start (Windows 32,000 limit).
    Those folders are not pre-approved, so the gate refuses reads of them; the rules stop at the top level."""
    from labhq.adapters.base import _command_too_long

    home, staff, ws, mine, other, settings = _staff(tmp_path, monkeypatch)
    before, _ctx = _command(tmp_path, _found(settings, ws), settings)
    for i in range(300):
        (staff / "projects" / claude_project_slug(str(home / ".labhq" / "runs" / f"t{i:04d}_analyst"))).mkdir()
    found = _found(settings, ws)
    cmd, _ctx = _command(tmp_path, found, settings)
    assert _command_too_long(cmd) is None
    assert len(cmd[cmd.index("--settings") + 1]) == len(before[before.index("--settings") + 1])
    sibling = staff / "projects" / claude_project_slug(str(home / ".labhq" / "runs" / "t0299_analyst"))
    assert _gate(found, home, ws, "Read", {"file_path": str(sibling / "s.jsonl")}).action == "deny"
    assert _gate(found, home, ws, "Read", {"file_path": str(mine / "s1" / "tool-results" / "out.txt")}).action \
        == "allow"


def test_claude_pre_approves_reads_of_the_open_folder_and_never_writes(tmp_path, monkeypatch):
    _home_, staff, ws, mine, _other, settings = _staff(tmp_path, monkeypatch)
    cmd, _ctx = _command(tmp_path, _found(settings, ws), settings)
    allowed = cmd[cmd.index("--allowedTools") + 1:]
    assert f"Read({_rule(mine)}/**)" in allowed
    assert not any(rule.startswith(("Edit(", "Write(")) and claude_rule_path(str(staff)) in rule for rule in allowed)
    assert str(mine) not in [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--add-dir"]


def test_the_isolation_settings_follow_the_staff_config_folder(tmp_path, monkeypatch):
    """adapters.claude_code.user_config_isolation reads CLAUDE_CONFIG_DIR from the CLI's env (child_config_dirs)."""
    _home_, staff, ws, _mine, _other, settings = _staff(tmp_path, monkeypatch)
    cmd, ctx = _command(tmp_path, _found(settings, ws), settings)
    assert ClaudeCodeAdapter(settings).staff_env(ctx)["CLAUDE_CONFIG_DIR"] == str(staff)
    excludes = json.loads(cmd[cmd.index("--settings") + 1])["claudeMdExcludes"]
    assert (staff / "CLAUDE.md").as_posix() in excludes and (staff / "rules").as_posix() + "/**" in excludes


def test_the_footer_tells_claude_staff_the_saved_output_is_readable(tmp_path):
    agent = AgentSpec(id="w", name="W", role="test", engine=Engine.claude_code)

    def footer(open_reads):
        return role_footer(RunContext(task=Task(agent_id="w", prompt="x"), agent=agent, workdir=tmp_path,
                                      settings=Settings(), mcp_servers=[], env={}, emit=None, prompt="x",
                                      private_labels=["~/.claude", STAFF_LABEL], private_open_reads=open_reads))

    opened = footer([str(tmp_path / "staff" / "projects" / "x")])
    assert "Read로 다시 읽을 수 있습니다" in opened and "다시 열리지 않을 수 있습니다" not in opened
    assert str(tmp_path) not in opened  # labels only, never an absolute path
    assert "다시 열리지 않을 수 있습니다" in footer([])


# ---------------- unset: the behavior before ----------------

def test_without_a_staff_config_folder_nothing_opens(tmp_path, monkeypatch):
    home, _staff_dir, ws, mine, _other, settings = _staff(tmp_path, monkeypatch, config="")
    assert staff_claude_project_dirs(settings, ws) == []
    found = _found(settings, ws)
    assert found.open_reads == () and STAFF_LABEL not in found.labels
    assert claude_deny_private({}, found.paths, str(home), open_reads=found.open_reads) == \
        claude_deny_private({}, found.paths, str(home))
    pi_saved = home / ".claude" / "projects" / claude_project_slug(str(ws)) / "s" / "tool-results" / "o.txt"
    assert _gate(found, home, ws, "Read", {"file_path": str(pi_saved)}).action == "deny"
    for inside_pi in (home / ".claude", home / ".claude" / "staff"):  # never opens the PI's own folder
        settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(inside_pi)}
        assert staff_claude_project_dirs(settings, ws) == []


def test_the_gate_reads_the_open_folders_the_runner_set():
    env = {ENV_VAR: "/h/.labhq/claude-staff", OPEN_READS_ENV_VAR: os.pathsep.join(["/h/a", "/h/b"])}
    assert gate_private_paths(env, lambda: None).open_reads == ("/h/a", "/h/b")
    assert gate_private_paths({ENV_VAR: "/h/x"}, lambda: None).open_reads == ()


@pytest.mark.asyncio
async def test_runner_opens_the_project_folder_to_a_claude_task_only(tmp_path, monkeypatch):
    home, staff, _ws, _mine, _other, _settings = _staff(tmp_path, monkeypatch)
    settings = _runner_settings(tmp_path, None)
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff)}
    runner, seen = _capture_runner(settings, monkeypatch)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    ctx = seen["ctx"]
    expected = str(staff / "projects" / claude_project_slug(os.path.abspath(str(ctx.workdir))))
    assert expected in ctx.private_open_reads and STAFF_LABEL in ctx.private_labels
    assert ctx.env[OPEN_READS_ENV_VAR].split(os.pathsep) == ctx.private_open_reads
    assert f"Read({_rule(staff)}/**)" not in ctx.claude_settings["permissions"]["deny"]
    runner2, seen2 = _capture_runner(settings, monkeypatch, engine=Engine.codex)
    assert (await runner2.run_task(Task(id="t2", agent_id="worker", request_id="r", prompt="q"))).ok
    assert seen2["ctx"].private_open_reads == [] and seen2["ctx"].env[OPEN_READS_ENV_VAR] == ""
    assert STAFF_LABEL in seen2["ctx"].private_labels  # Codex staff never need the Claude login


@pytest.mark.asyncio
async def test_the_approval_server_opens_only_the_folders_the_runner_named(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    workdir, staff = tmp_path / "ws", tmp_path / "claude-staff"
    mine = staff / "projects" / claude_project_slug(str(workdir))
    workdir.mkdir()
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "LABHQ_BROKER_URL": "http://127.0.0.1:9", "LABHQ_BROKER_TOKEN": "x", "LABHQ_WORKDIR": str(workdir),
           ENV_VAR: str(staff), private_paths.ENABLED_ENV_VAR: "1", OPEN_READS_ENV_VAR: str(mine)}
    env.pop("LABHQ_CONFIG", None)
    seen = {}
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            for name, path in (("mine", mine / "s" / "tool-results" / "o.txt"), ("login", staff / ".credentials.json")):
                result = await session.call_tool("approval_prompt", {"tool_name": "Read",
                                                                     "input": {"file_path": str(path)}})
                seen[name] = json.loads(result.content[0].text)["behavior"]
    assert seen == {"mine": "allow", "login": "deny"}


# ---------------- doctor ----------------

def _doctor_rows(tmp_path, monkeypatch, env, *, login_rc=1, engine="claude_code", private=None, pi_claude=True):
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    if pi_claude:
        (home / ".claude").mkdir()
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").write_text(f"id: worker\nname: Worker\nrole: test\nengine: {engine}\n", encoding="utf-8")
    data = {"runner": {"state_dir": str(tmp_path / "state"), "workspace_root": str(tmp_path / "runs"),
                       "agents_dir": str(agents.parent)},
            "gateway": {"state_dir": str(tmp_path / "gateway")}, "hpc": {"scheduler": "none"},
            "engines": {"claude_code": {"bin": "claude", "env": env}}}
    if private is not None:
        data["policy"] = {"private_paths": private}
    settings = Settings.model_validate(data)
    claude = str(tmp_path / "bin" / "claude")
    monkeypatch.setattr(doctor, "_resolve_command", lambda cmd, env, name: [claude] if name == "claude_code" else cmd)
    monkeypatch.setattr(doctor.shutil, "which", lambda name, **kw: claude if name == claude else None)
    probes = []

    def probe(argv, env):
        probes.append((argv, env.get("CLAUDE_CONFIG_DIR")))
        return (login_rc, "") if argv[-2:] == ["auth", "status"] else (0, "2.1.282 (Claude Code)")

    monkeypatch.setattr(doctor, "_probe", probe)
    rows = doctor.collect(settings)["checks"]
    return home, [r for r in rows if r["name"] == "claude staff config"], probes


def test_doctor_warns_when_claude_staff_share_the_pi_folder(tmp_path, monkeypatch):
    home, rows, _probes = _doctor_rows(tmp_path, monkeypatch, {})
    assert len(rows) == 1 and rows[0]["status"] == "warn" and "긴 출력" in rows[0]["detail"]
    assert "CLAUDE_CONFIG_DIR" in rows[0]["hint"] and ".labhq/claude-staff" in rows[0]["hint"]
    assert str(home) not in rows[0]["detail"] + rows[0]["hint"]


@pytest.mark.parametrize("inside", ["", "staff"])
def test_doctor_fails_a_staff_folder_that_is_the_pi_folder_or_inside_it(tmp_path, monkeypatch, inside):
    env = {"CLAUDE_CONFIG_DIR": "${LABHQ_TEST_PI_CLAUDE}" + (f"/{inside}" if inside else "")}
    monkeypatch.setenv("LABHQ_TEST_PI_CLAUDE", str(tmp_path / "home" / ".claude"))
    _home_, rows, _probes = _doctor_rows(tmp_path, monkeypatch, env)
    assert len(rows) == 1 and rows[0]["status"] == "fail"


@pytest.mark.parametrize("login_rc,status", [(1, "warn"), (0, "ok")])
def test_doctor_checks_the_login_in_the_staff_folder(tmp_path, monkeypatch, login_rc, status):
    staff = tmp_path / "home" / ".labhq" / "claude-staff"
    staff.mkdir(parents=True)
    _home_, rows, probes = _doctor_rows(tmp_path, monkeypatch, {"CLAUDE_CONFIG_DIR": str(staff)}, login_rc=login_rc)
    assert len(rows) == 1 and rows[0]["status"] == status
    assert any(argv[-2:] == ["auth", "status"] and folder == str(staff) for argv, folder in probes)
    if status == "warn":
        assert "/login" in rows[0]["hint"] and "CLAUDE_CONFIG_DIR" in rows[0]["hint"]


@pytest.mark.parametrize("case", ["off", "no_pi_folder", "codex_only"])
def test_doctor_says_nothing_when_the_pi_folder_is_not_closed_to_claude_staff(tmp_path, monkeypatch, case):
    _home_, rows, _probes = _doctor_rows(tmp_path, monkeypatch, {}, private=[] if case == "off" else None,
                                         pi_claude=case != "no_pi_folder",
                                         engine="codex" if case == "codex_only" else "claude_code")
    assert rows == []
