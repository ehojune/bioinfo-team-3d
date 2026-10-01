import asyncio
import contextlib
import ctypes
import os
import signal
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


def _posix_exit_state(pid: int) -> tuple[bool, str]:
    """(exited, what /proc says). A zombie counts as exited: the orphan waits for init to reap it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True, "gone"
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError as exc:
        # #227: init reaped the zombie between kill(0) and this read. Ask kill(0) again instead of
        # calling that "alive", so a system without /proc still waits for the pid to go.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True, "gone"
        return False, f"kill(0) ok, /proc unreadable ({type(exc).__name__})"
    fields = dict(line.split(":", 1) for line in status.splitlines() if line.startswith(("State:", "PPid:")))
    state = fields.get("State", "?").strip()
    return state[:1] in ("Z", "X"), f"State {state}, PPid {fields.get('PPid', '?').strip()}"


def _posix_process_exited(pid: int) -> bool:
    return _posix_exit_state(pid)[0]


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
    # The grandchild ignores SIGTERM before it says ready, and the leader writes pids.txt only after that.
    # So a pids.txt proves the timeout's SIGTERM met a grandchild that ignores it (#227).
    script = tmp_path / "tree.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "out = pathlib.Path(sys.argv[1])\n"
        "ready = out.with_name('ready')\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready)], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not ready.exists():\n"
        "    time.sleep(0.01)\n"
        "out.with_suffix('.tmp').write_text(f'{os.getpid()} {child.pid}', encoding='utf-8')\n"
        "os.replace(out.with_suffix('.tmp'), out)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    child = None
    try:
        for timeout in (1, 5):  # a loaded host can take over 1s to start the tree; then try once with more time
            attempt = tmp_path / f"timeout-{timeout}"
            (attempt / "workdir").mkdir(parents=True)
            pids = attempt / "pids.txt"
            settings = Settings()
            settings.runner.task_timeout_s = timeout
            agent = AgentSpec(id="a", name="A", role="test", engine=Engine.cli, builtin_mcp=[],
                              cli=CliSpec(command=[sys.executable, str(script), str(pids)]))
            ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=attempt / "workdir",
                             settings=settings, mcp_servers=[], env={}, emit=_emit, prompt="x")
            result = await get_adapter(agent.engine, settings).run(ctx)
            assert not result.ok and result.error == f"timeout after {timeout}s"
            if pids.exists():
                break
        else:
            pytest.fail("the process tree was not ready before a 5s timeout")
        _parent, child = map(int, pids.read_text(encoding="utf-8").split())
        deadline = time.monotonic() + 5
        exited, state = _posix_exit_state(child)
        while not exited and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            exited, state = _posix_exit_state(child)
        # judge the last probe, not a fresh one: a fresh probe raced the zombie's reaping (#227)
        assert exited, f"SIGTERM-ignoring grandchild {child} still alive 5s after the timeout kill: {state}"
    finally:
        if child is not None and not _posix_process_exited(child):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="Linux /proc")
def test_posix_exit_probe_counts_a_pid_reaped_mid_probe_as_exited(monkeypatch):
    """#227: init reaped the killed grandchild between kill(0) and the /proc read; that is not "alive"."""
    pid = os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], dict(os.environ))
    reaped = False
    try:
        deadline = time.monotonic() + 10
        while not _posix_exit_state(pid)[1].startswith("State Z") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _posix_exit_state(pid)[1].startswith("State Z"), "the unreaped child is a zombie first"
        real_read = Path.read_text

        def reap_then_read(self, *args, **kwargs):
            nonlocal reaped
            if self == Path(f"/proc/{pid}/status") and not reaped:
                os.waitpid(pid, 0)  # plays init, reaping right after kill(0) saw the pid
                reaped = True
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", reap_then_read)
        assert _posix_exit_state(pid) == (True, "gone")
        assert reaped
    finally:
        if not reaped:
            os.waitpid(pid, 0)
