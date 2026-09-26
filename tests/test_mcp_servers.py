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

    from labhq.tools.hpc_mcp import _prepare_job_files

    group = grp.getgrgid(os.getgid()).gr_name
    workdir = tmp_path / "task"
    workdir.mkdir(mode=0o700)
    script = workdir / "jobs" / "job.sh"
    logs = workdir / "jobs" / "logs"
    output_dir = workdir / "hpc_out"
    _prepare_job_files(workdir, script, logs, build_script("echo ok", str(output_dir), umask="007"), group)
    gid = grp.getgrnam(group).gr_gid
    assert f"cd {output_dir}" in script.read_text()
    assert script.stat().st_gid == logs.stat().st_gid == output_dir.stat().st_gid == gid
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    assert stat.S_IMODE(logs.stat().st_mode) == 0o2770
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o2770
    assert stat.S_IMODE(script.parent.stat().st_mode) == 0o2750
    assert workdir.stat().st_gid == gid and workdir.stat().st_mode & stat.S_IXGRP
    assert not workdir.stat().st_mode & (stat.S_IRGRP | stat.S_IWGRP)


async def test_hpc_submit_reports_switched_output_dir(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    settings = Settings(hpc=HpcSettings(submit_prefix=["sudo"], job_group="lab-jobs", user="data-account"))
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    seen = {}

    def prepare(workdir, script_path, logs, body, job_group):
        seen["body"] = body
        seen["group"] = job_group
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
