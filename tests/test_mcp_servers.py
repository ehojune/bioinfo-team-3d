import json
import os
import shlex
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.models import McpServerSpec
from labhq.settings import HpcSettings, Settings
from labhq.tools._mcpcompat import list_tools
from labhq.tools.scheduler import build_script

REPO = Path(__file__).resolve().parents[1]
ENV = {"PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": "http://127.0.0.1:9", "LABHQ_BROKER_TOKEN": "x"}


async def test_builtin_servers_expose_tools(tmp_path):
    env = {**ENV, "LABHQ_WORKDIR": str(tmp_path)}
    hpc = await list_tools(McpServerSpec(name="hpc", command=sys.executable, args=["-m", "labhq.tools.hpc_mcp"], env=env))
    assert {"hpc_submit", "hpc_status", "hpc_queue", "hpc_cancel"} <= set(hpc)
    appr = await list_tools(McpServerSpec(name="a", command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env=env))
    assert appr == ["approval_prompt"]


async def test_approval_prompt_contract(tmp_path):
    import os

    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"],
                                   env={**os.environ, **ENV, "LABHQ_WORKDIR": str(tmp_path)})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            ok = await session.call_tool("approval_prompt", {"tool_name": "Read", "input": {"file_path": "README.md"}})
            # Claude Code rejects anything but a single text block (P1 real-CLI finding)
            assert len(ok.content) == 1 and ok.content[0].type == "text"
            assert (getattr(ok, "structured_content", None) or getattr(ok, "structuredContent", None)) is None
            payload = json.loads(ok.content[0].text)
            assert payload == {"behavior": "allow", "updatedInput": {"file_path": "README.md"}}
            # "ask" with an unreachable broker must fail closed
            risky = await session.call_tool("approval_prompt", {"tool_name": "Bash", "input": {"command": "rm -rf /tmp/x"}})
    assert json.loads(risky.content[0].text)["behavior"] == "deny"


@pytest.mark.skipif(os.name == "nt", reason="POSIX chgrp and setgid directory modes required")
def test_hpc_job_files_are_shared_with_job_group(tmp_path):
    import grp
    import pwd

    from labhq.tools.hpc_mcp import _prepare_job_files

    group = grp.getgrgid(os.getgid()).gr_name
    workdir = tmp_path / "task"
    old_umask = os.umask(0o022)
    try:
        workdir.mkdir()
        (workdir / "TASK.md").write_text("private task input")
        (workdir / "outputs").mkdir()
        (workdir / "outputs" / "input.txt").write_text("private input")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(workdir.stat().st_mode) == 0o755
    assert stat.S_IMODE((workdir / "TASK.md").stat().st_mode) == 0o644
    script = workdir / "jobs" / "job.sh"
    logs = workdir / "jobs" / "logs"
    output_dir = workdir / "hpc_out"
    _prepare_job_files(workdir, script, logs, build_script("echo ok", str(output_dir), umask="007"),
                       group, tmp_path, pwd.getpwuid(os.getuid()).pw_name)
    gid = grp.getgrnam(group).gr_gid
    assert f"cd {output_dir}" in script.read_text()
    assert script.stat().st_gid == logs.stat().st_gid == output_dir.stat().st_gid == gid
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    assert stat.S_IMODE(logs.stat().st_mode) == 0o2770
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o2770
    assert stat.S_IMODE(script.parent.stat().st_mode) == 0o2750
    assert workdir.stat().st_gid == gid and stat.S_IMODE(workdir.stat().st_mode) == 0o710
    assert stat.S_IMODE((workdir / "TASK.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((workdir / "outputs").stat().st_mode) == 0o700
    assert stat.S_IMODE((workdir / "outputs" / "input.txt").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX chgrp and directory traverse permissions required")
def test_first_task_on_new_date_opens_only_managed_workspace_parents(tmp_path):
    import grp
    import pwd

    from labhq.models import AgentSpec, Task
    from labhq.runner.workspace import TaskWorkspace
    from labhq.tools.hpc_mcp import _prepare_job_files

    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    root = tmp_path / "runs"
    outside_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    old_umask = os.umask(0o077)
    try:
        task = Task(agent_id="worker", prompt="make an aggregate")
        agent = AgentSpec(id="worker", name="Worker", role="test")
        workspace = TaskWorkspace(root, task, agent)
        workspace.write_task_md()
        workdir = workspace.dir
    finally:
        os.umask(old_umask)
    date_dir = workdir.parent
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(date_dir.stat().st_mode) == 0o700
    script = workdir / "jobs" / "job.sh"
    logs = workdir / "jobs" / "logs"
    _prepare_job_files(workdir, script, logs, "echo ok", group, root, user)
    gid = grp.getgrnam(group).gr_gid
    for parent in (root, date_dir):
        assert parent.stat().st_gid == gid
        assert stat.S_IMODE(parent.stat().st_mode) == 0o710
    assert stat.S_IMODE(tmp_path.stat().st_mode) == outside_mode
    assert stat.S_IMODE(workdir.stat().st_mode) == 0o710
    assert stat.S_IMODE((workdir / "TASK.md").stat().st_mode) == 0o600
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    assert stat.S_IMODE(logs.stat().st_mode) == 0o2770


@pytest.mark.skipif(os.name == "nt", reason="POSIX account and directory traverse permissions required")
def test_inaccessible_parent_outside_workspace_root_is_not_modified(tmp_path, monkeypatch):
    import grp
    import pwd

    from labhq.tools.hpc_mcp import _prepare_job_files

    outer = tmp_path / "private"
    outer.mkdir(mode=0o700)
    root = outer / "runs"
    workdir = root / "2026-09-27" / "task"
    workdir.mkdir(parents=True, mode=0o700)
    group = grp.getgrgid(os.getgid()).gr_name
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid() + 10000,
                                                                    pw_gid=os.getgid()))
    monkeypatch.setattr(os, "getgrouplist", lambda *_: [os.getgid()])
    with pytest.raises(RuntimeError, match="cannot traverse outside runner.workspace_root"):
        _prepare_job_files(workdir, workdir / "jobs" / "job.sh", workdir / "jobs" / "logs",
                           "echo ok", group, root, "data-account")
    assert stat.S_IMODE(outer.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (workdir / "jobs").exists()


async def test_hpc_submit_reports_switched_output_dir(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    settings = Settings(hpc=HpcSettings(submit_prefix=["sudo"], job_group="lab-jobs", user="data-account"))
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    seen = {}

    def prepare(workdir, script_path, logs, body, job_group, workspace_root, job_user):
        seen["body"] = body
        seen["group"] = job_group
        seen["root"] = workspace_root
        seen["user"] = job_user
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(body)

    async def broker(path, payload, timeout):
        return {}

    monkeypatch.setattr(hpc, "S", settings)
    monkeypatch.setattr(hpc, "WORKDIR", tmp_path)
    monkeypatch.setattr(hpc, "SCHED", SimpleNamespace(submit=lambda *args: "123"))
    monkeypatch.setattr(hpc, "_prepare_job_files", prepare)
    monkeypatch.setattr(hpc, "_broker", broker)
    result = json.loads(await hpc.hpc_submit("echo ok", "job"))
    assert result["submitted"] and result["output_dir"] == str(tmp_path / "hpc_out")
    assert f"cd {shlex.quote(str(tmp_path / 'hpc_out'))}" in seen["body"]
    assert "umask 007" in seen["body"]
    assert seen["group"] == "lab-jobs"
    assert seen["root"] == settings.path(settings.runner.workspace_root)
    assert seen["user"] == "data-account"
