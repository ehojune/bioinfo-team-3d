"""#178: another spelling of a project link into a restricted zone (8.3 short name, case) must not pass the gate.

Claude's deny rules compare the path as written, so `DATA~1` or `C:/PROJECT/DATA` slips past `Read(//c/project/data/**)`.
While such a link is denied, Claude pre-approves no shell command and no read outside the task's folders: a read that
resolves outside them reaches the gate (claude_read_scope_alias.json, probe scoped_read_alias), and the gate compares
real paths, which follow the link whatever its spelling.
"""

import json
import os
from pathlib import Path

import pytest

from labhq import private_paths
from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.policy import SHELL_TOOLS, claude_rule_path, evaluate_tool, rule_tool
from labhq.settings import DataZone, PolicySettings, Settings
from tests.test_workspace_boundary import _capture_runner, _zone_settings

FIXTURE = Path(__file__).parent / "fixtures" / "real" / "claude_code" / "claude_read_scope_alias.json"


def _link_dir(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _allowed_tools(tmp_path, project, **fields):
    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.claude_code,
                      tools=["Read", "Grep", "Write", "Bash(python *)", "Bash"], builtin_mcp=["approval"])
    (tmp_path / "ws" / ".labhq").mkdir(parents=True, exist_ok=True)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path / "ws",
                     settings=Settings(), mcp_servers=[], env={}, emit=None, prompt="x",
                     extra_dirs=[str(project)], claude_settings={}, **fields)
    cmd = get_adapter(agent.engine, Settings()).build_command(ctx)
    start = cmd.index("--allowedTools") + 1
    end = cmd.index("--disallowedTools") if "--disallowedTools" in cmd else len(cmd)
    return cmd[start:end]


def test_a_denied_project_link_sends_other_spellings_to_the_gate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    # Private paths off: before #178 bare Read and shell rules stayed, so `DATA~1/raw.tsv` was read unasked.
    plain = _allowed_tools(tmp_path, project)
    assert "Read" in plain and "Bash" in plain
    narrowed = _allowed_tools(tmp_path, project, denied_links=[str(project / "data")])
    assert "Read" not in narrowed and not [t for t in narrowed if rule_tool(t) in SHELL_TOOLS]
    for folder in (tmp_path / "ws", project):
        assert f"Read(/{claude_rule_path(str(folder.resolve()))}/**)" in narrowed
    # Measured on Claude 2.1.282: with a scoped Read rule, a read through a link out of the folder was not pre-approved.
    probes = {p["id"]: p for p in json.loads(FIXTURE.read_text(encoding="utf-8"))["probes"]}
    assert probes["bare_read_alias"]["pre_approved"] and not probes["scoped_read_alias"]["pre_approved"]


@pytest.mark.asyncio
async def test_the_runner_hands_claude_the_denied_project_links(tmp_path, monkeypatch):
    zone, project = tmp_path / "zone", tmp_path / "project"
    zone.mkdir()
    project.mkdir()
    monkeypatch.setattr("labhq.runner.daemon.zone_links", lambda directory, *_args: ([Path(directory) / "raw"], None))
    runner, seen = _capture_runner(tmp_path, monkeypatch, _zone_settings(tmp_path, zone))
    result = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="q",
                                        meta={"project_dirs": [str(project)]}))
    assert result.ok, result.error
    assert seen["ctx"].denied_links == [str(project / "raw")]


def _zone_link(tmp_path):
    zone = tmp_path / "zone"
    zone.mkdir()
    (zone / "raw.tsv").write_text("fake record\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "cohort_data_link"
    try:
        _link_dir(link, zone)
    except OSError as exc:
        pytest.skip(f"directory link unavailable: {exc}")
    return zone, project, link


def _judge(tmp_path, zone, project, path):
    policy = PolicySettings(data_zones=[DataZone(path=str(zone))])
    workdir = str(tmp_path / "ws")
    roots = [workdir, str(project)]
    read = evaluate_tool("Read", {"file_path": path}, policy, roots, workdir)
    shell = evaluate_tool("Bash", {"command": f'wc -l "{path}"'}, policy, roots, workdir)
    return read.action, shell.action


@pytest.mark.skipif(os.name != "nt", reason="8.3 names are a Windows feature")
def test_a_short_name_of_a_zone_link_is_judged_by_its_real_folder(tmp_path):
    """Real fixture: a junction gets an 8.3 alias where the volume makes them. The gate resolves it to the zone."""
    zone, project, link = _zone_link(tmp_path)
    short = private_paths._short_name(str(link))
    if not short:
        pytest.skip("8.3 names are off on this volume")
    assert os.path.basename(short).casefold() != link.name.casefold()
    for spelled in (short, os.path.join(str(project), os.path.basename(short))):
        assert _judge(tmp_path, zone, project, os.path.join(spelled, "raw.tsv")) == ("deny", "ask"), spelled


@pytest.mark.skipif(os.name != "nt", reason="NTFS compares names without case; most POSIX volumes do not")
def test_a_case_variant_of_a_zone_link_is_judged_by_its_real_folder(tmp_path):
    zone, project, link = _zone_link(tmp_path)
    for spelled in (str(link).upper(), str(project / "COHORT_DATA_LINK")):
        assert _judge(tmp_path, zone, project, os.path.join(spelled, "raw.tsv")) == ("deny", "ask"), spelled
