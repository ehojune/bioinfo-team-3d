import asyncio
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
from labhq.runner.hpc_jobs import submit_job
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


async def _install_gate(tmp_path: Path, command: str, *, environment_step: bool) -> dict:
    tmp_path.mkdir(exist_ok=True)
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env={
        **os.environ, **ENV, "LABHQ_WORKDIR": str(tmp_path),
        "LABHQ_ENVIRONMENT_STEP": "1" if environment_step else "0",
        "LABHQ_SHARED_ENVIRONMENT_PROTECTED": "1",
    })
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.call_tool("approval_prompt", {
                "tool_name": "Bash", "input": {"command": command}})
    return json.loads(result.content[0].text)


async def test_parallel_steps_cannot_install_into_the_shared_environment(tmp_path):
    command = "/shared/env/bin/python -m pip install scanpy"
    decisions = await asyncio.gather(*(
        _install_gate(tmp_path / step, command, environment_step=False) for step in ("left", "right")))
    assert [decision["behavior"] for decision in decisions] == ["deny", "deny"]
    assert all("Only the environment step" in decision["message"] for decision in decisions)


@pytest.mark.parametrize("command", [
    "/shared/env/bin/pip install scanpy",
    r"C:\shared\env\Scripts\pip.exe install scanpy",
    "python3 -m pip install scanpy",
    "py -3 -m pip install scanpy",
    "uv pip install scanpy",
    "conda install scanpy",
    "mamba install scanpy",
])
async def test_installer_paths_and_supported_package_managers_are_denied(tmp_path, command):
    decision = await _install_gate(tmp_path, command, environment_step=False)
    assert decision["behavior"] == "deny"
    assert "Only the environment step" in decision["message"]


async def test_each_r_install_call_needs_its_own_task_library(tmp_path):
    decision = await _install_gate(
        tmp_path,
        "Rscript -e \"install.packages('a'); install.packages('b', lib='./.rlib')\"",
        environment_step=False,
    )
    assert decision["behavior"] == "deny"
    assert "shared R library" in decision["message"]


async def test_environment_step_and_task_local_package_installs_are_allowed(tmp_path):
    shared = await _install_gate(
        tmp_path / "env", "./.venv/bin/python -m pip install --only-binary=:all: scanpy",
        environment_step=True)
    local_python = await _install_gate(
        tmp_path / "python", "python -m pip install --target ./.pylib scanpy", environment_step=False)
    local_r = await _install_gate(
        tmp_path / "r", "Rscript -e \"install.packages('limma', lib='./.rlib')\"", environment_step=False)
    local_r_env = await _install_gate(
        tmp_path / "r-env", "$env:R_LIBS_USER='./.rlib'; Rscript -e \"install.packages('limma')\"",
        environment_step=False)
    local_r_calls = await _install_gate(
        tmp_path / "r-calls",
        "Rscript -e \"install.packages('a', lib='./.rlib'); remotes::install_github('x/y', lib='./.rlib')\"",
        environment_step=False)
    assert (shared["behavior"] == local_python["behavior"] == local_r["behavior"] ==
            local_r_env["behavior"] == local_r_calls["behavior"] == "allow")


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX group traversal and chmod required")
def test_second_job_submission_never_removes_group_traverse(tmp_path, monkeypatch):
    import grp
    import pwd
    import labhq.tools.hpc_mcp as hpc

    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    workdir = root / "task"
    workdir.mkdir(mode=0o700)
    jobs = workdir / "jobs"
    logs = jobs / "logs"
    hpc._prepare_job_files(workdir, jobs / "first.sh", logs, "echo first", group, root, user)
    assert stat.S_IMODE(workdir.stat().st_mode) == 0o710

    real_chmod, real_walk = os.chmod, os.walk

    def checked_chmod(path, mode, *args, **kwargs):
        if Path(path) in {workdir, jobs}:
            assert mode & stat.S_IXGRP
        real_chmod(path, mode, *args, **kwargs)

    def checked_walk(*args, **kwargs):
        for entry in real_walk(*args, **kwargs):
            assert workdir.stat().st_mode & stat.S_IXGRP
            yield entry

    with monkeypatch.context() as m:
        m.setattr(os, "chmod", checked_chmod)
        m.setattr(os, "walk", checked_walk)
        hpc._prepare_job_files(workdir, jobs / "second.sh", logs, "echo second", group, root, user)
    assert stat.S_IMODE(workdir.stat().st_mode) == 0o710
    assert (jobs / "first.sh").read_text() == "echo first"
    assert (jobs / "second.sh").read_text() == "echo second"
    assert stat.S_IMODE((jobs / "first.sh").stat().st_mode) == 0o750


@pytest.mark.skipif(os.name == "nt", reason="POSIX job-group and private file modes required")
def test_existing_jobs_inputs_become_private_before_sharing(tmp_path):
    import grp
    import pwd

    from labhq.tools.hpc_mcp import _prepare_job_files

    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    workdir = root / "task"
    jobs = workdir / "jobs"
    old_umask = os.umask(0o022)
    try:
        jobs.mkdir(parents=True)
        (jobs / "input.txt").write_text("private")
        (jobs / "legacy.sh").write_text("private")
        nested = jobs / "old"
        nested.mkdir()
        (nested / "input.txt").write_text("private")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE((jobs / "input.txt").stat().st_mode) == 0o644
    assert stat.S_IMODE(jobs.stat().st_mode) == 0o755

    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    script = jobs / "new.sh"
    logs = jobs / "logs"
    _prepare_job_files(workdir, script, logs, "echo ok", group, root, user)
    assert stat.S_IMODE(jobs.stat().st_mode) == 0o2750
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    assert stat.S_IMODE(logs.stat().st_mode) == 0o2770
    assert stat.S_IMODE((jobs / "input.txt").stat().st_mode) == 0o600
    assert stat.S_IMODE((jobs / "legacy.sh").stat().st_mode) == 0o600
    assert stat.S_IMODE(nested.stat().st_mode) == 0o700
    assert stat.S_IMODE((nested / "input.txt").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links, symlinks and chmod required")
@pytest.mark.parametrize("link_kind", ["hard", "symlink"])
def test_linked_jobs_input_is_rejected_without_changing_source(tmp_path, link_kind):
    import grp
    import pwd

    from labhq.tools.hpc_mcp import _prepare_job_files

    source = tmp_path / "source.txt"
    source.write_text("private")
    source.chmod(0o644)
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    workdir = root / "task"
    jobs = workdir / "jobs"
    jobs.mkdir(parents=True)
    linked = jobs / "input.txt"
    if link_kind == "hard":
        os.link(source, linked)
    else:
        os.symlink(source, linked)
    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    with pytest.raises(RuntimeError, match="hard link" if link_kind == "hard" else "symlink"):
        _prepare_job_files(workdir, jobs / "new.sh", jobs / "logs", "echo ok", group, root, user)
    assert stat.S_IMODE(source.stat().st_mode) == 0o644
    assert not (jobs / "new.sh").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX job-group link and mode regression")
def test_jobs_replaced_by_a_link_during_prepare_is_rejected_without_changing_target(tmp_path, monkeypatch):
    import grp
    import pwd
    import labhq.tools.hpc_mcp as hpc

    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    root = tmp_path / "runs"
    workdir = root / "task"
    jobs = workdir / "jobs"
    jobs.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    before = stat.S_IMODE(outside.stat().st_mode)
    real_walk = os.walk

    def swap_after_walk(*args, **kwargs):
        yield from real_walk(*args, **kwargs)
        jobs.rename(workdir / "checked-jobs")
        os.symlink(outside, jobs, target_is_directory=True)

    monkeypatch.setattr(os, "walk", swap_after_walk)
    with pytest.raises(RuntimeError, match="link|replaced"):
        hpc._prepare_job_files(workdir, jobs / "new.sh", jobs / "logs", "echo ok", group, root, user)
    assert stat.S_IMODE(outside.stat().st_mode) == before
    assert not (outside / "new.sh").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links, symlinks and chmod required")
@pytest.mark.parametrize("link_kind", ["hard", "symlink"])
def test_linked_task_input_is_rejected_without_changing_source(tmp_path, link_kind):
    import grp
    import pwd

    from labhq.tools.hpc_mcp import _prepare_job_files

    source = tmp_path / "shared.txt"
    source.write_text("shared input")
    source.chmod(0o644)
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    workdir = root / "task"
    workdir.mkdir(mode=0o700)
    linked = workdir / "TASK.md"
    if link_kind == "hard":
        os.link(source, linked)
    else:
        os.symlink(source, linked)
    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    with pytest.raises(RuntimeError, match="hard link" if link_kind == "hard" else "symlink"):
        _prepare_job_files(workdir, workdir / "jobs" / "job.sh", workdir / "jobs" / "logs",
                           "echo ok", group, root, user)
    assert source.read_text() == "shared input"
    assert stat.S_IMODE(source.stat().st_mode) == 0o644
    assert not (workdir / "jobs" / "job.sh").exists()


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
    root = outer / "runs"
    date_dir = root / "2026-09-27"
    workdir = date_dir / "task"
    old_umask = os.umask(0o022)
    try:
        for directory in (outer, root, date_dir, workdir):
            directory.mkdir(mode=0o700)
    finally:
        os.umask(old_umask)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700
               for path in (outer, root, date_dir, workdir))
    group = grp.getgrgid(os.getgid()).gr_name
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid() + 10000,
                                                                    pw_gid=os.getgid()))
    monkeypatch.setattr(os, "getgrouplist", lambda *_: [os.getgid()])
    with pytest.raises(RuntimeError, match="cannot traverse outside runner.workspace_root"):
        _prepare_job_files(workdir, workdir / "jobs" / "job.sh", workdir / "jobs" / "logs",
                           "echo ok", group, root, "data-account")
    assert stat.S_IMODE(outer.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(date_dir.stat().st_mode) == 0o700
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

    async def approve(payload):
        raise AssertionError("below the threshold with no directive: no PI approval")

    async def broker(path, payload, timeout):
        assert path == "/jobs/submit"  # the runner's side, with a stand-in scheduler
        return await submit_job(settings, SimpleNamespace(submit=lambda *args: "123"), tmp_path, payload, approve)

    monkeypatch.setattr(hpc, "S", settings)
    monkeypatch.setattr(hpc, "WORKDIR", tmp_path)
    monkeypatch.setattr(hpc, "_prepare_job_files", prepare)
    monkeypatch.setattr(hpc, "_broker", broker)
    result = json.loads(await hpc.hpc_submit("echo ok", "job"))
    assert result["submitted"] and result["job_id"] == "123" and result["output_dir"] == str(tmp_path / "hpc_out")
    assert f"cd {shlex.quote(str(tmp_path / 'hpc_out'))}" in seen["body"]
    assert "umask 007" in seen["body"]
    assert seen["group"] == "lab-jobs"
    assert seen["root"] == settings.path(settings.runner.workspace_root)
    assert seen["user"] == "data-account"
