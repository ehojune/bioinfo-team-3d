"""#219: Claude file-tool write paths on Windows, judged where Claude actually writes them.

The fixture records a real Claude Code 2.1.282 run on Windows 11 with the workdir below TEMP: Git Bash prints the
workdir as `/tmp/...`, the model copies that into Write, Claude passes `\\tmp\\...` to the permission gate and,
when allowed, writes `<drive>:\\tmp\\...` (outside the workdir). A bare `Write` in --allowedTools does so unasked.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.policy import claude_allowed_tools, claude_rule_path, evaluate_tool
from labhq.settings import Settings

REPO = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((REPO / "tests" / "fixtures" / "real" / "claude_code" / "claude_windows_write_paths.json")
                     .read_text(encoding="utf-8"))
TMP = "C:\\Users\\u\\AppData\\Local\\Temp"
WORKDIR = TMP + "\\claude\\C--Users-u-Desktop-lab\\ws"
WINDOWS_ENV = {"TMP": TMP, "TEMP": TMP}


def _fill(value: str) -> str:
    for key, real in (("<WORKDIR>", "<TMP>\\claude\\<PROJECT>\\ws"), ("<TMP_POSIX>", "/c/Users/u/AppData/Local/Temp"),
                      ("<TMP>", TMP), ("<DRIVE>", "C:"), ("<PROJECT>", "C--Users-u-Desktop-lab")):
        value = value.replace(key, real)
    return value


def _probe(probe_id: str) -> dict:
    def fill(value):
        return _fill(value) if isinstance(value, str) else [fill(v) for v in value] if isinstance(value, list) else value
    return {k: fill(v) for k, v in next(p for p in FIXTURE["probes"] if p["id"] == probe_id).items()}


def _write(path: str, *, roots=(WORKDIR,), environ=WINDOWS_ENV, policy=None, tool="Write"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return evaluate_tool(tool, {key: path, "content": "x"}, policy or Settings().policy, allowed_roots=list(roots),
                         workdir=WORKDIR, windows=True, environ=environ)


def test_fixture_matches_the_workdir_these_tests_use():
    assert _fill(FIXTURE["workdir"]) == WORKDIR
    assert _probe("preapproved_tmp")["written_to"].startswith("C:\\tmp\\")  # the observed escape


@pytest.mark.parametrize("probe_id", ["gate_tmp", "gate_updated_input", "scoped_rule_tmp"])
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_git_bash_tmp_write_inside_workdir_is_allowed_and_respelled(probe_id, tool):
    probe = _probe(probe_id)
    for spelling in (probe["gate_input_file_path"], probe["model_file_path"]):
        decision = _write(spelling, tool=tool)
        assert decision.action == "allow", decision.reason
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        # Claude writes the path the gate hands back (fixture probe gate_updated_input), so it lands in the workdir.
        assert decision.updated_input[key] == WORKDIR + "\\outputs\\probe.txt"
        assert decision.updated_input["content"] == "x"


def test_gate_input_claude_already_resolved_is_unchanged():
    decision = _write(_probe("gate_msys_drive")["gate_input_file_path"])
    assert decision.action == "allow" and decision.updated_input is None


def test_file_tool_write_does_not_expand_environment_variables(monkeypatch):
    monkeypatch.setenv("HOMEPATH", r"\Users\u")
    root = r"C:\Users\u\.labhq\runs\x\ws"
    decision = evaluate_tool(
        "Write",
        {"file_path": r"C:\%HOMEPATH%\.labhq\runs\x\ws\a.md", "content": "x"},
        Settings().policy,
        allowed_roots=[root],
        workdir=root,
        windows=True,
    )
    assert decision.action == "ask"
    assert "%HOMEPATH%" in decision.reason


@pytest.mark.parametrize("spelling,shown", [
    ("\\tmp\\claude\\C--Users-u-Desktop-lab\\other\\a.md", "C:\\tmp\\claude\\C--Users-u-Desktop-lab\\other\\a.md"),
    ("/tmp/elsewhere/a.md", "C:\\tmp\\elsewhere\\a.md"),
    ("\\tmp\\claude\\C--Users-u-Desktop-lab\\ws\\..\\a.md", "C:\\tmp\\claude\\C--Users-u-Desktop-lab\\a.md"),
    ("/etc/passwd", "C:\\etc\\passwd"),
])
def test_write_outside_workdir_still_asks_with_the_path_claude_would_write(spelling, shown):
    decision = _write(spelling)
    assert decision.action == "ask"
    assert shown in decision.reason
    assert decision.updated_input["file_path"] == shown  # a PI approval approves exactly what was shown


@pytest.mark.parametrize("environ", [{}, {"TEMP": TMP, "TMP": "D:\\other"}, {"TEMP": "relative\\temp"}])
def test_unknown_git_bash_tmp_fails_closed(environ):
    decision = _write(_probe("gate_tmp")["gate_input_file_path"], environ=environ)
    assert decision.action == "ask"
    assert decision.updated_input["file_path"] == "C:\\tmp\\claude\\C--Users-u-Desktop-lab\\ws\\outputs\\probe.txt"


def test_respelled_write_is_still_judged_against_restricted_zones():
    project = TMP + "\\claude\\C--Users-u-Desktop-lab"
    policy = Settings(policy={"data_zones": [{"path": project + "\\cohort", "level": "restricted"}]}).policy
    decision = _write("\\tmp\\claude\\C--Users-u-Desktop-lab\\cohort\\raw.tsv", roots=(WORKDIR, project), policy=policy)
    assert decision.action == "deny" and "restricted data zone" in decision.reason


def test_posix_hosts_keep_tmp_as_a_real_path():
    decision = evaluate_tool("Write", {"file_path": "/tmp/run/ws/a.md"}, Settings().policy,
                             allowed_roots=["/tmp/run/ws"], workdir="/tmp/run/ws", windows=False, environ=WINDOWS_ENV)
    assert decision.action == "allow" and decision.updated_input is None


def _stream(name: str) -> list[dict]:
    path = REPO / "tests" / "fixtures" / "real" / "claude_code" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _blocks(events: list[dict], kind: str) -> list[dict]:
    return [b for ev in events if isinstance(ev.get("message"), dict)
            for b in ev["message"].get("content") or [] if isinstance(b, dict) and b.get("type") == kind]


STREAM_WORKDIR = TMP + "\\<WORKDIR_BELOW_TMP>"


def test_real_run_after_the_fix_writes_the_git_bash_spelling_into_the_workdir():
    events = _stream("claude_write_tmp_respelled")
    assert events[0]["claude_code_version"] == "2.1.282"
    assert [s["name"] for s in events[0]["mcp_servers"]] == ["labhq_approval"]
    writes = [b for b in _blocks(events, "tool_use") if b["name"] == "Write"]
    assert [w["input"]["file_path"] for w in writes] == ["/tmp/<WORKDIR_BELOW_TMP>/outputs/probe.txt"]
    result = next(b for b in _blocks(events, "tool_result") if b["tool_use_id"] == writes[0]["id"])
    assert result["content"].startswith("File created successfully at: <WORKDIR>\\outputs\\probe.txt")
    # The gate's answer for that call, judged the same way here.
    decision = evaluate_tool("Write", {"file_path": "\\tmp\\<WORKDIR_BELOW_TMP>\\outputs\\probe.txt"},
                             Settings().policy, allowed_roots=[STREAM_WORKDIR], workdir=STREAM_WORKDIR,
                             windows=True, environ=WINDOWS_ENV)
    assert decision.action == "allow"
    assert decision.updated_input["file_path"] == STREAM_WORKDIR + "\\outputs\\probe.txt"


def test_real_run_after_the_fix_still_sends_an_outside_write_to_approval():
    events = _stream("claude_write_tmp_outside_asks")
    result = next(ev for ev in events if ev.get("type") == "result")
    denied = [d["tool_input"]["file_path"] for d in result["permission_denials"]]
    assert denied == ["\\tmp\\<WORKDIR_BELOW_TMP>_outside\\probe.txt"]
    error = next(b for b in _blocks(events, "tool_result") if b.get("is_error"))
    assert "approval broker unreachable" in error["content"]
    decision = evaluate_tool("Write", {"file_path": denied[0]}, Settings().policy, allowed_roots=[STREAM_WORKDIR],
                             workdir=STREAM_WORKDIR, windows=True, environ=WINDOWS_ENV)
    assert decision.action == "ask" and decision.updated_input["file_path"] == "C:" + denied[0]


def _claude_cmd(tmp_path, tools, extra_dirs=()):
    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.claude_code, tools=list(tools),
                      builtin_mcp=["approval"])
    (tmp_path / "ws" / ".labhq").mkdir(parents=True, exist_ok=True)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path / "ws",
                     settings=Settings(), mcp_servers=[], env={}, emit=None, prompt="x",
                     extra_dirs=[str(d) for d in extra_dirs], claude_settings={})
    cmd = get_adapter(agent.engine, Settings()).build_command(ctx)
    i = cmd.index("--allowedTools")
    end = cmd.index("--disallowedTools") if "--disallowedTools" in cmd else len(cmd)
    return cmd[i + 1:end]


def test_bare_file_tools_are_preapproved_only_inside_the_write_roots(tmp_path):
    """A bare Write let the fixture's `/tmp/...` write land in C:\\tmp unasked; outside writes must reach the gate."""
    project = tmp_path / "project"
    allowed = _claude_cmd(tmp_path, ["Read", "Write", "Edit", "Bash(ls *)"], [project])
    assert not {"Write", "Edit", "MultiEdit", "NotebookEdit"} & set(allowed)
    assert allowed == ["Read", "Bash(ls *)", f"Edit(/{claude_rule_path(str((tmp_path / 'ws').resolve()))}/**)",
                       f"Edit(/{claude_rule_path(str(project.resolve()))}/**)"]
    # The rule spelling Claude honoured in the fixture (probes scoped_rule_*).
    assert _probe("scoped_rule_relative")["allowed_tools"][1] == (
        f"Edit(/{claude_rule_path(_fill('<WORKDIR>'))}/**)")


def _link_dir(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_write_root_spelled_through_a_link_gets_a_rule_for_each_spelling(tmp_path):
    """Claude pre-approved a link-spelled absolute write only when both spellings had a rule (probes junction_*)."""
    probes = {p["id"]: p for p in FIXTURE["probes"]}
    assert probes["junction_real_rule_relative"]["written_to"]  # Claude resolves its own cwd
    assert probes["junction_real_rule_absolute"]["written_to"] is None
    assert probes["junction_link_rule_absolute"]["written_to"] is None
    assert probes["junction_both_rules_absolute"]["written_to"]
    fixed = probes["junction_labhq_rules_absolute"]  # this function's rules, run for real
    assert fixed["allowed_tools"] == ["Edit(/<LINK_POSIX>/**)", "Edit(/<REAL_POSIX>/**)"] and fixed["written_to"]
    real = tmp_path / "real"
    (real / "ws").mkdir(parents=True)
    _link_dir(tmp_path / "link", real)
    root = tmp_path / "link" / "ws"
    assert os.path.realpath(root) != os.path.abspath(root)
    assert claude_allowed_tools(["Read", "Write"], [root]) == [
        "Read", f"Edit(/{claude_rule_path(os.path.abspath(root))}/**)",
        f"Edit(/{claude_rule_path(os.path.realpath(root))}/**)"]


def test_agents_without_file_tools_get_no_edit_rule(tmp_path):
    assert _claude_cmd(tmp_path, ["Read", "WebSearch"]) == ["Read", "WebSearch"]


@pytest.mark.skipif(os.name != "nt", reason="Git Bash /tmp spelling exists only on Windows")
async def test_approval_gate_respells_git_bash_tmp_write_on_windows(tmp_path):
    temp = Path(os.path.realpath(os.environ.get("TEMP") or os.environ.get("TMP") or ""))
    workdir = (tmp_path / "ws").resolve()
    workdir.mkdir()
    try:
        relative = workdir.relative_to(temp)
    except ValueError:
        pytest.skip("pytest's temp folder is not below TEMP")
    spelled = "\\tmp\\" + str(relative / "outputs" / "a.md")
    env = {**os.environ, "PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": "http://127.0.0.1:9",
           "LABHQ_BROKER_TOKEN": "x", "LABHQ_WORKDIR": str(workdir)}
    env.pop("LABHQ_CONFIG", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            inside = await session.call_tool("approval_prompt", {"tool_name": "Write",
                                                                 "input": {"file_path": spelled, "content": "x"}})
            outside = await session.call_tool("approval_prompt", {"tool_name": "Write", "input": {
                "file_path": "\\tmp\\" + str(relative.parent / "elsewhere" / "a.md"), "content": "x"}})
    assert json.loads(inside.content[0].text) == {
        "behavior": "allow", "updatedInput": {"file_path": str(workdir / "outputs" / "a.md"), "content": "x"}}
    assert json.loads(outside.content[0].text)["behavior"] == "deny"  # asked, and the broker is unreachable


async def test_approval_gate_allows_shell_write_to_link_spelled_workdir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _link_dir(link, real)
    env = {**os.environ, "PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": "http://127.0.0.1:9",
           "LABHQ_BROKER_TOKEN": "x", "LABHQ_WORKDIR": str(link)}
    env.pop("LABHQ_CONFIG", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.call_tool("approval_prompt", {"tool_name": "Bash", "input": {
                "command": f'echo x > "{link / "a.txt"}"'}})
    assert json.loads(result.content[0].text) == {
        "behavior": "allow", "updatedInput": {"command": f'echo x > "{link / "a.txt"}"'}}
