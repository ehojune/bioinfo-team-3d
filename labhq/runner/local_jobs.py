"""Long computations that run as local jobs the runner owns (#675).

A staff member submits a command and its expected time through the `labhq_job` tool. The runner starts it (through
`local_job_wrapper.py`, so its exit code outlives a runner restart), keeps its pid and creation time in the job row,
polls it like an HPC job and wakes the step when it ends. Cancelling the step or request kills the job's process tree.
Logs and the exit file live in the runner's state folder, never in the staff-writable workspace.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

PREFIX = "local-"
WRAPPER = Path(__file__).with_name("local_job_wrapper.py")
TAIL_BYTES = 4000
TERM_WAIT_S = 5.0


def is_local(job_id: object) -> bool:
    return isinstance(job_id, str) and job_id.startswith(PREFIX)


def new_id() -> str:
    return PREFIX + uuid.uuid4().hex[:10]


def shell_tool() -> str:
    """The policy tool name that judges this shell's commands."""
    return "PowerShell" if os.name == "nt" else "Bash"


# PowerShell -Command exits 1 for any failing native command; the job should report the program's own exit code.
_PS_EXIT = "\n$__ok = $?; $__code = $LASTEXITCODE; if ($__code) { exit $__code } elseif (-not $__ok) { exit 1 }"


def shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command + _PS_EXIT]
    # The approval policy judged this as a Bash command, so run it with Bash where there is one (sh may be dash).
    return [shutil.which("bash") or "/bin/sh", "-c", command]


def job_dir(state_dir: Path, job_id: str) -> Path:
    return Path(state_dir) / "local_jobs" / job_id


def _created(pid: int) -> str | None:
    from ..cli import _process_created

    return _process_created(pid)


def alive(pid: int, created: str | None) -> bool:
    """The recorded process is still running: same pid and same creation time, so a reused pid does not count."""
    if not created:
        return False
    return _created(pid) == created


def start(state_dir: Path, job_id: str, command: str, workdir: Path,
          env: dict[str, str]) -> tuple[dict, subprocess.Popen]:
    """Start the job in its own process group: the facts to store in its row, and the process to reap."""
    folder = job_dir(state_dir, job_id)
    folder.mkdir(parents=True, exist_ok=True)
    out, err, exit_file = folder / "out.log", folder / "err.log", folder / "exit"
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
             if os.name == "nt" else {"start_new_session": True})
    with open(out, "ab") as out_handle, open(err, "ab") as err_handle:
        process = subprocess.Popen([sys.executable, str(WRAPPER), str(exit_file), *shell_argv(command)],
                                   cwd=str(workdir), env=env, stdin=subprocess.DEVNULL, stdout=out_handle,
                                   stderr=err_handle, **group)
    facts = {"pid": process.pid, "exit_file": str(exit_file), "out": str(out), "err": str(err),
             "started_at": time.time()}
    try:
        facts["created"] = _created(process.pid)
    except Exception as exc:  # no identity means the job could never be watched or cancelled: do not leave it running
        facts["created"] = None
        kill(facts, process)
        raise OSError(f"the job started but its process identity could not be read ({exc}); it was ended") from exc
    return facts, process


def status(local: dict, process: subprocess.Popen | None = None) -> tuple[str, int | None]:
    """(state, exit status) of a job row's process: completed/failed from the exit file, running while alive.

    `process` is the child this runner started: polling it reaps a finished wrapper (POSIX keeps it as a zombie that
    still looks alive), which a job adopted after a restart does not have."""
    try:
        text = Path(local["exit_file"]).read_text(encoding="ascii").strip()
        code = int(text)
        return ("completed" if code == 0 else "failed"), code
    except (OSError, ValueError, KeyError):
        pass
    if process is not None and process.poll() is not None:
        return "unknown_finished", None
    if alive(int(local.get("pid") or 0), local.get("created")):
        return "running", None
    return "unknown_finished", None  # gone without an exit file: killed from outside or lost with the machine


def _group_alive(pid: int) -> bool:
    """POSIX: some process of the job's group is left. The wrapper leader can be gone while a descendant that ignored
    SIGTERM still runs, so the job is over only when the whole group is."""
    if os.name == "nt":
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _gone(pid: int, created: str | None, process: subprocess.Popen | None) -> bool:
    """The job is over. With the child in hand, its group may still hold a descendant that outlived the leader. A job
    adopted after a restart has no child: only a leader that is still the recorded process proves the group is ours,
    since a pid and group id can be reused by an unrelated process once the leader is gone."""
    if process is not None:
        return process.poll() is not None and not _group_alive(pid)  # poll() reaps; an unreaped child reads as alive
    return not alive(pid, created)


def kill(local: dict, process: subprocess.Popen | None = None) -> bool:
    """End the job's whole process tree if it is still the recorded process. True when nothing of it is left."""
    pid, created = int(local.get("pid") or 0), local.get("created")
    if not pid or _gone(pid, created, process):
        return True
    if os.name == "nt":
        from .. import proc_tree

        proc_tree.kill_tree(pid, created=proc_tree.parse_created(created))
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + TERM_WAIT_S
        while time.monotonic() < deadline and not _gone(pid, created, process):
            time.sleep(0.1)
        # The group was verified as ours when SIGTERM went out, so what ignored it is ended now even if the leader
        # is already gone (an adopted job has no child to say whether the group is empty).
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + TERM_WAIT_S
    while time.monotonic() < deadline and not _gone(pid, created, process):
        time.sleep(0.1)
    return _gone(pid, created, process)


def tail(path: str | None) -> str:
    """The last part of a job log, for the status tool."""
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - TAIL_BYTES))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
