"""PI-only web shutdown, using the same identity checks and stop path as `down`.

The gateway is the process being stopped, so it cannot run the whole of `down` from a request task: `down` signals the
gateway and waits for it to end, and Uvicorn ends only after the request tasks do. Each waits on the other until a
SIGKILL (PR #576 review), which loses the shutdown hooks. Here the task stops the runner, starts a detached watcher
that only force-stops this gateway if its own shutdown stalls, then asks this process to shut down the normal way and
returns.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from fastapi import HTTPException
from pydantic import BaseModel, StrictBool

from .. import cli

# How long the gateway gets to finish its own shutdown (hooks, open sockets) before the watcher steps in.
EXIT_GRACE_SECONDS = 30.0


def _names_this_gateway(record_pid: int) -> bool:
    """The record's PID is this process, or on Windows the launcher that started it: a venv's python.exe starts the
    real interpreter as its child and `up` records the launcher's PID, so os.getpid() never equals the record there.
    (The record was already matched to a live process by its creation time.)"""
    return record_pid == os.getpid() or (sys.platform == "win32" and record_pid == os.getppid())


class ShutdownIn(BaseModel):
    force: StrictBool = False
    confirmation: str = ""


def _exit_this_process() -> None:
    """Start the graceful exit an external SIGTERM would: Uvicorn stops accepting, closes sockets, ends request tasks
    and runs the app's shutdown hooks. raise_signal works for a background process on Windows too, where there is
    no console to signal and os.kill(SIGTERM) would be TerminateProcess."""
    if "pytest" in sys.modules:
        raise RuntimeError("a test must replace WebShutdown.request_exit; this would signal pytest itself")
    signal.raise_signal(signal.SIGTERM)


def spawn_exit_watch(settings, pid: int, created: str) -> subprocess.Popen:
    """Start the detached watcher for gateway `pid` (identity `created`). It outlives the gateway, and is outside its
    process group (POSIX) and job-less console (Windows) so stopping the gateway does not take the watcher along."""
    logs = cli._launch_state(settings) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    command = [sys.executable, "-m", "labhq.gateway.shutdown", settings.config_path or "", str(pid), created]
    options = {"cwd": str(cli.REPO), "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT, "env": env,
               "close_fds": True}
    if sys.platform == "win32":
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        options["start_new_session"] = True
    with (logs / "gateway-exit-watch.log").open("a", encoding="utf-8") as output:
        return subprocess.Popen(command, stdout=output, **options)


def watch_gateway_exit(settings, pid: int, created: str, *, grace: float = EXIT_GRACE_SECONDS) -> int:
    """Wait for the gateway to end by itself. 0: it did. 1: it stalled and was stopped by force. 2: could not tell or
    could not stop it (an unreadable process identity is never killed)."""
    try:
        ended = cli._wait_for_process_end(pid, created, grace)
    except OSError as exc:
        print(f"gateway PID {pid}의 상태를 확인할 수 없어 건드리지 않습니다: {exc}", flush=True)
        return 2
    code = 0
    if ended:
        print(f"gateway PID {pid}이 스스로 정상 종료했습니다.", flush=True)
    else:
        print(f"gateway PID {pid}이 {grace:g}초 안에 끝나지 않아 강제로 종료합니다.", flush=True)
        try:
            cli._terminate_tree(pid, created=created)
        except OSError as exc:
            print(f"gateway PID {pid}을 종료하지 못했습니다: {exc}", flush=True)
            return 2
        code = 1
    _drop_record(settings, pid)
    return code


def _drop_record(settings, pid: int) -> None:
    """The gateway can no longer remove its own PID record. Leave a record that names another process alone."""
    path = cli._pid_file(settings, "gateway")
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == pid:
            path.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError):
        pass  # a missing or unreadable record is cleaned as stale by the next up/down


class WebShutdown:
    def __init__(self, settings, hub, *, request_exit=None):
        self.settings = settings
        self.hub = hub
        self.status = "idle"
        self.request_exit = request_exit or _exit_this_process

    def prepare(self, body: ShutdownIn) -> dict:
        if self.status == "stopping":
            raise HTTPException(409, "이미 종료 중입니다.")
        if body.force and body.confirmation != "interrupt-running-work":
            raise HTTPException(422, "진행 중인 일이 중단된다는 경고를 확인한 뒤 다시 눌러 주세요.")
        readiness = self.hub.shutdown_readiness()
        if not readiness["ready"] and not body.force:
            raise HTTPException(409, {"message": "진행 중인 일이 있어 종료하지 않았습니다.",
                                      "readiness": readiness})
        # Never use records belonging to another gateway/config, even if the token matches.
        for role in ("gateway", "runner"):
            state, record, _ = cli._process_record(self.settings, role, cleanup=False)
            if state != "alive" or (role == "gateway" and not _names_this_gateway(record["pid"])):
                raise HTTPException(409, "이 인스턴스의 PID 기록을 확인할 수 없어 끌 수 없습니다. "
                                    "labhq up으로 시작한 인스턴스에서 사용하세요.")
        self.status = "stopping"
        self.hub.stopping = True
        return {"status": self.status}

    def run(self, force: bool) -> None:
        # Starlette runs this synchronous background task off the event loop, AFTER sending the response, and
        # Uvicorn finishes its shutdown only once such tasks return. So this must return: never wait here for this
        # process to end. `down` takes the up/down lock and checks readiness again before stopping the runner.
        try:
            cli._down(self.settings, force=force, keep_gateway=True)
            self._mark_gateway_stopping()
            watcher = self._start_watcher()
            try:
                self.request_exit()
            except BaseException:
                if watcher is not None:
                    watcher.terminate()  # the shutdown never started; the watcher must not stop a live gateway
                raise
        except (SystemExit, Exception):
            self.status = "failed"
            self.hub.stopping = False
        # On success the status stays "stopping" until this process has exited.

    def _mark_gateway_stopping(self) -> None:
        """This gateway is about to end by itself on purpose: its supervisor must not start it again (#589). A marker
        that cannot be written only matters with `supervisor.restart` on, and must not keep the shutdown from running."""
        try:
            cli._mark_stopping(self.settings, "gateway")
        except OSError as exc:
            print(f"종료 표시를 남기지 못했습니다(supervisor.restart가 켜져 있으면 gateway가 다시 시작될 수 있음): {exc}",
                  file=sys.stderr, flush=True)

    def _start_watcher(self):
        """The runner is already stopped, so a gateway that fails to start its watcher still shuts down: a stalled
        shutdown is then left to `down`. The reason goes to the gateway log."""
        try:
            # Watch the recorded process: on Windows that is the launcher, which ends when the interpreter does.
            state, record, reason = cli._process_record(self.settings, "gateway", cleanup=False)
            if state != "alive":
                raise OSError(reason or f"gateway record is {state}")
            return spawn_exit_watch(self.settings, int(record["pid"]), str(record["created"]))
        except (OSError, KeyError, ValueError) as exc:
            print(f"종료 감시 프로세스를 시작하지 못했습니다(gateway 종료는 계속): {exc}", file=sys.stderr, flush=True)
            return None


def main(argv: list[str] | None = None) -> int:
    from ..settings import Settings

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print("usage: python -m labhq.gateway.shutdown CONFIG PID CREATED", file=sys.stderr)
        return 2
    config, pid, created = args
    return watch_gateway_exit(Settings.load(config or None), int(pid), created)


if __name__ == "__main__":
    raise SystemExit(main())
