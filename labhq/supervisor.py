"""One detached supervisor per role of `labhq up`: the process that stays alive as the gateway's or runner's parent (#589).

`up` exits as soon as everything answers. A launcher `up` started itself keeps naming that dead `up` as its parent,
and Windows hands the PID to an unrelated process later. Anything that collects a tree by parent PID alone and kills
it (a test, a tool, a script using the Windows tree-kill command) then takes the PI's gateway and runner along. The
kill paths inside labhq check creation times since #597, but code outside labhq cannot be made to.

So `up` starts a supervisor per role, and the supervisor starts the role itself and waits for it:

- the role's recorded parent is a live process for the role's whole life, so that PID cannot be reused;
- the supervisor's own parent is the dead `up`, so before it starts anything it holds a handle on each of its nearest
  ancestors (`proc_tree.pin_ancestors`). An open handle keeps a PID reserved after its process has exited;
- the supervisor sees how the role ended and writes its exit code and time as a `child_exit` row in
  `<role>-supervisor-diagnostics.jsonl`. A forced kill leaves the role's own `exit` row unwritten, so this is the only
  record of one.

With `supervisor.restart` on, a role that ended on its own with a non-zero code is started again after a backoff, at
most `supervisor.max_restarts` times within `supervisor.restart_window_s`; exit code 2, labhq's own refusal to start,
is not retried. It is off by default. A role stopped on
purpose is never restarted: `down` and the web shutdown write a marker file before touching anything, and a signal to
the supervisor counts too.

Usage (started by `labhq up`): python -m labhq.supervisor CONFIG ROLE
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from . import cli, proc_tree
from .process_diagnostics import SUPERVISOR_SUFFIX, ProcessDiagnostics
from .settings import Settings, SupervisorSettings
from .util import atomic_write_text

ROLES = ("gateway", "runner")
# What labhq itself exits with when it refuses to start: a rejected config (#559), another config's live process on its
# state folder (#613), a command-line error. Starting it again is refused the same way.
REFUSED_TO_START = 2
TICK_SECONDS = 0.5  # how often a wait looks at the stop marker and the stop flag


@dataclass(frozen=True)
class RestartPolicy:
    enabled: bool = False
    max_restarts: int = 5
    window_s: float = 600.0
    backoff_s: float = 5.0
    backoff_cap_s: float = 120.0

    @classmethod
    def from_settings(cls, settings: SupervisorSettings) -> "RestartPolicy":
        return cls(settings.restart, settings.max_restarts, settings.restart_window_s, settings.backoff_s,
                   settings.backoff_cap_s)

    def delay(self, restarts: list[float], now: float) -> float | None:
        """Seconds to wait before the next restart, or None for no restart: it is off, or `max_restarts` already
        happened within the window. `restarts` holds the times (same clock as `now`) of the earlier ones."""
        if not self.enabled:
            return None
        recent = [stamp for stamp in restarts if now - stamp < self.window_s]
        if len(recent) >= self.max_restarts:
            return None
        return min(self.backoff_cap_s, self.backoff_s * 2 ** len(recent))


def exit_fields(code: int) -> dict:
    """An exit status as the diagnostics row stores it: the code as Popen reports it, its hex form when it looks like
    a Windows status (a crash such as 0xC0000005, or Ctrl+C), and the signal name when a POSIX signal ended it."""
    fields: dict = {"code": code, "code_hex": None, "signal": None}
    if code < 0:
        try:
            fields["signal"] = signal.Signals(-code).name
        except ValueError:
            fields["signal"] = f"signal {-code}"
    elif code > 255:
        fields["code_hex"] = f"0x{code & 0xFFFFFFFF:08X}"
    return fields


class Supervisor:
    def __init__(self, settings: Settings, role: str, diagnostics, *, stop: threading.Event | None = None,
                 popen=None, pin=None, monotonic=time.monotonic, wait=None) -> None:
        self.settings, self.role, self.diag = settings, role, diagnostics
        self.policy = RestartPolicy.from_settings(settings.supervisor)
        self.stop = stop or threading.Event()
        self._popen = popen or subprocess.Popen
        self._pin = pin or proc_tree.pin_ancestors
        self._monotonic = monotonic
        self._wait_for = wait or self.stop.wait  # tests replace it with a fake clock
        self._forwarded = False

    # -- the one loop -----------------------------------------------------------------------------------------

    def run(self) -> int:
        """0 when the role ended cleanly or was stopped on purpose, 1 otherwise (the real code is in the diagnostics)."""
        try:
            pins = self._pin()
        except Exception as exc:  # losing the protection must not stop the role from starting
            pins = proc_tree.PinResult(unpinned={os.getpid(): type(exc).__name__})
        self.diag.write("parents_pinned", pinned=pins.pinned,
                        unpinned={str(pid): reason for pid, reason in pins.unpinned.items()})
        try:
            return self._supervise()
        finally:
            pins.close()

    def _supervise(self) -> int:
        restarts: list[float] = []
        attempt = 0
        while True:
            attempt += 1
            child = self._start(attempt)
            if child is None:
                return 1
            started = self._monotonic()
            code = self._wait_for_exit(child)
            stopping = self._stopping()
            self.diag.write("child_exit", child=self.role, child_pid=child.pid, attempt=attempt,
                            uptime_s=round(self._monotonic() - started, 1), stop_requested=stopping,
                            **exit_fields(code))
            if stopping or code == 0:
                return 0
            if code == REFUSED_TO_START and self.policy.enabled:
                self.diag.write("restart_skipped", child=self.role, reason="refused_to_start")
                return 1
            delay = self.policy.delay(restarts, self._monotonic())
            if delay is None:
                if self.policy.enabled:
                    self.diag.write("restart_giving_up", child=self.role, max_restarts=self.policy.max_restarts,
                                    window_s=self.policy.window_s)
                return 1
            self.diag.write("restart_scheduled", child=self.role, delay_s=delay, attempt=attempt + 1)
            if self._pause(delay):
                return 0
            restarts.append(self._monotonic())

    # -- starting and recording the role ---------------------------------------------------------------------

    def _start(self, attempt: int):
        """Start the role and record it, or None when that cannot be done (the diagnostics say why)."""
        command, options = cli._child_launch(self.settings, self.role)
        logs = cli._launch_state(self.settings) / "logs"
        try:
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / f"{self.role}.log").open("a", encoding="utf-8") as output:
                child = self._popen(command, stdout=output, **options)
        except OSError as exc:
            self.diag.write("child_start_failed", child=self.role, attempt=attempt, reason=type(exc).__name__)
            return None
        try:
            created = cli._process_created(child.pid)
            if created is not None:
                record = {"pid": int(child.pid), "role": self.role, "created": created,
                          "config": self.settings.config_path}
                atomic_write_text(cli._pid_file(self.settings, self.role),
                                  json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, cli.ProcessIdentityError) as exc:
            # A role nobody can find again later is worse than none: `down` could not stop it.
            self._abandon(child)
            self.diag.write("child_start_failed", child=self.role, attempt=attempt, reason=type(exc).__name__)
            return None
        # created is None: the role already ended. The wait below reports its exit code like any other end.
        self.diag.write("child_start", child=self.role, child_pid=child.pid, attempt=attempt, created=created)
        return child

    def _abandon(self, child) -> None:
        """End a role that was started but cannot be recorded. The Popen handle names it for certain."""
        try:
            if os.name == "nt":
                proc_tree.kill_tree(child.pid)
            else:
                os.killpg(child.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            child.kill()
        except OSError:
            pass

    # -- waiting ---------------------------------------------------------------------------------------------

    def _stopping(self) -> bool:
        return self.stop.is_set() or cli._stopping_file(self.settings, self.role).exists()

    def _wait_for_exit(self, child) -> int:
        while True:
            try:
                return child.wait(timeout=TICK_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            if self.stop.is_set() and not self._forwarded:
                self._forwarded = True
                self._forward_stop(child)

    def _forward_stop(self, child) -> None:
        """The supervisor was told to stop: pass it on to the role. The Popen handle names `child` for certain."""
        self.diag.write("stop_forwarded", child=self.role, child_pid=child.pid)
        try:
            if os.name == "nt":
                proc_tree.kill_tree(child.pid)
            else:
                os.killpg(child.pid, signal.SIGTERM)  # the role leads its own process group
        except OSError:
            pass  # already gone

    def _pause(self, delay: float) -> bool:
        """Wait `delay` seconds before a restart. True when the role was stopped on purpose meanwhile."""
        deadline = self._monotonic() + delay
        while True:
            if self._stopping():
                return True
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._wait_for(min(TICK_SECONDS, remaining))


def _install_signal_flag(stop: threading.Event) -> None:
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, lambda _signum, _frame: stop.set())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[1] not in ROLES:
        print("usage: python -m labhq.supervisor CONFIG ROLE", file=sys.stderr)
        return 2
    config, role = args
    settings = Settings.load(config or None)
    stop = threading.Event()
    # Before the diagnostics install theirs: they call this one after noting the signal, instead of ending the process.
    _install_signal_flag(stop)
    logs = cli._launch_state(settings) / "logs"
    with ProcessDiagnostics(logs, role + SUPERVISOR_SUFFIX, interval=3600) as diagnostics:
        try:
            code = Supervisor(settings, role, diagnostics, stop=stop).run()
        except Exception as exc:  # the type only: a message can carry configuration
            diagnostics.write("supervisor_error", reason=type(exc).__name__)
            code = 1
        diagnostics.code = code
        return code


if __name__ == "__main__":
    raise SystemExit(main())
