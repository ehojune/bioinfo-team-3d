import asyncio
import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.runner.workspace import TaskWorkspace
from labhq.settings import Settings


async def _emit(_kind, _data):
    pass


def test_atomic_write_replaces_a_same_directory_temp_file(tmp_path, monkeypatch):
    import labhq.util as util

    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    calls = []
    original = util.os.replace

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        original(source, destination)

    monkeypatch.setattr(util.os, "replace", replace)
    util.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert len(calls) == 1 and calls[0][0].parent == target.parent and calls[0][1] == target


def test_manifest_updates_use_atomic_write(tmp_path, monkeypatch):
    import labhq.adapters.owned as workspace_module  # every workspace file write goes through owned (#165)

    writes = []
    original = workspace_module.atomic_write_text

    def record(path, text):
        writes.append(Path(path))
        original(path, text)

    monkeypatch.setattr(workspace_module, "atomic_write_text", record)
    agent = AgentSpec(id="a", name="A", role="test", builtin_mcp=[])
    task = Task(agent_id="a", prompt="x")
    workspace = TaskWorkspace(tmp_path / "runs", task, agent)
    workspace.update_run(task.id, started_at=1.0)
    workspace.update_run(task.id, ended_at=2.0)
    manifest = workspace.dir / "manifest.json"
    assert writes == [manifest, manifest], "one atomic write per update; a missing manifest is not written twice"
    assert not list(manifest.parent.glob(f".{manifest.name}.*.tmp"))


def _process_exited(pid: int) -> bool:
    synchronize = 0x00100000
    wait_object_0 = 0
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_object_0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _posix_process_exited(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    status = Path(f"/proc/{pid}/status")
    try:
        return "State:\tZ" in status.read_text(encoding="utf-8")
    except OSError:
        return False


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree termination")
@pytest.mark.asyncio
async def test_windows_timeout_kills_cli_and_grandchild(tmp_path):
    pids = tmp_path / "pids.txt"
    script = tmp_path / "tree.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}', encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    settings = Settings()
    settings.runner.task_timeout_s = 1
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.cli, builtin_mcp=[],
                      cli=CliSpec(command=[sys.executable, str(script), str(pids)]))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=workdir,
                     settings=settings, mcp_servers=[], env={}, emit=_emit, prompt="x")
    result = await get_adapter(agent.engine, settings).run(ctx)
    assert not result.ok and result.error == "timeout after 1s"
    parent, child = map(int, pids.read_text(encoding="utf-8").split())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (_process_exited(parent) and _process_exited(child)):
        await asyncio.sleep(0.05)
    assert _process_exited(parent) and _process_exited(child)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group termination")
@pytest.mark.asyncio
async def test_posix_timeout_kills_sigterm_ignoring_grandchild(tmp_path):
    pids = tmp_path / "pids.txt"
    script = tmp_path / "tree.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}', encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    settings = Settings()
    settings.runner.task_timeout_s = 1
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.cli, builtin_mcp=[],
                      cli=CliSpec(command=[sys.executable, str(script), str(pids)]))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=workdir,
                     settings=settings, mcp_servers=[], env={}, emit=_emit, prompt="x")
    child = None
    try:
        result = await get_adapter(agent.engine, settings).run(ctx)
        assert not result.ok and result.error == "timeout after 1s"
        _parent, child = map(int, pids.read_text(encoding="utf-8").split())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _posix_process_exited(child):
            await asyncio.sleep(0.05)
        assert _posix_process_exited(child)
    finally:
        if child is not None and not _posix_process_exited(child):
            os.kill(child, 9)
