"""The per-role supervisor (#589), driven with fake children and a fake clock.

No process is started or ended here: the Popen, the process table and every kill are replaced, and the PIDs are ones
no machine allocates. The real-process behaviour is in tests/test_supervisor_process.py.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading

import pytest

from labhq import cli, proc_tree, supervisor
from labhq.settings import Settings, SupervisorSettings

PID = 930_000_000


class Diag:
    def __init__(self):
        self.rows: list[dict] = []
        self.code = 0

    def write(self, event, **fields):
        self.rows.append({"event": event, **fields})

    def events(self):
        return [row["event"] for row in self.rows]

    def row(self, event):
        return next(row for row in self.rows if row["event"] == event)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.waits: list[float] = []

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class FakeChild:
    """What the supervisor needs of a Popen: a PID and a `wait` that answers with an exit code."""
    count = 0

    def __init__(self, code=0, *, timeouts=0, during_wait=None):
        FakeChild.count += 1
        self.pid = PID + FakeChild.count
        self.code, self.timeouts, self.during_wait = code, timeouts, during_wait
        self.killed = False
        self.released = False  # set by whatever stands in for the stop request

    def wait(self, timeout=None):
        if self.during_wait:
            self.during_wait()
        if self.timeouts > 0 and not self.released:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self, children):
        self.children = list(children)
        self.calls: list[dict] = []

    def __call__(self, command, **options):
        self.calls.append({"command": command, "stdout_name": options["stdout"].name, "options": options})
        child = self.children.pop(0)
        if isinstance(child, BaseException):
            raise child
        return child


class FakePins:
    pinned = [PID + 1, PID + 2]
    unpinned: dict = {}

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def safety(monkeypatch):
    """Any real process primitive that is reached by mistake fails the test instead of acting."""
    from labhq import windows_launch
    monkeypatch.setattr(windows_launch, "current_job", lambda: {"in_job": False})
    monkeypatch.setattr(windows_launch, "child_job_membership", lambda child, **kw: False)
    def forbidden(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"{name} reached a real process")
        return fail

    for name in ("_table", "_snapshot", "_open"):
        monkeypatch.setattr(proc_tree, name, forbidden(f"proc_tree.{name}"))
    monkeypatch.setattr(proc_tree, "kill_tree", forbidden("proc_tree.kill_tree"))
    monkeypatch.setattr(cli, "_terminate_tree", forbidden("cli._terminate_tree"))
    monkeypatch.setattr(os, "killpg", forbidden("os.killpg"), raising=False)
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(cli, "_process_created", lambda pid: f"created:{pid}")


def _settings(tmp_path, **supervisor_settings) -> Settings:
    settings = Settings.model_validate({
        "gateway": {"state_dir": str(tmp_path / "state")}, "runner": {"state_dir": str(tmp_path / "state")},
        "supervisor": supervisor_settings, "hpc": {"scheduler": "none"}})
    settings.config_path = str(tmp_path / "labhq.yaml")
    return settings


def _make(tmp_path, children, *, role="gateway", stop=None, pin=None, **supervisor_settings):
    settings = _settings(tmp_path, **supervisor_settings)
    clock, popen, diag = FakeClock(), FakePopen(children), Diag()
    pins = FakePins()
    run = supervisor.Supervisor(settings, role, diag, stop=stop, popen=popen, pin=pin or (lambda: pins),
                                monotonic=clock.monotonic, wait=clock.wait)
    return run, settings, clock, popen, diag, pins


# --- the restart policy ------------------------------------------------------------------------------------------

def test_the_policy_is_off_by_default_and_the_setting_defaults_say_so():
    assert SupervisorSettings().restart is False
    assert supervisor.RestartPolicy.from_settings(SupervisorSettings()).delay([], 0.0) is None


def test_the_backoff_doubles_up_to_its_cap_and_the_cap_on_restarts_ends_it():
    policy = supervisor.RestartPolicy(enabled=True, max_restarts=4, window_s=600, backoff_s=5, backoff_cap_s=12)
    assert policy.delay([], 100) == 5
    assert policy.delay([100], 101) == 10
    assert policy.delay([100, 101], 102) == 12  # 20, held at the cap
    assert policy.delay([100, 101, 102], 103) == 12
    assert policy.delay([100, 101, 102, 103], 104) is None  # four restarts within the window: the next end is final


def test_restarts_older_than_the_window_no_longer_count():
    policy = supervisor.RestartPolicy(enabled=True, max_restarts=2, window_s=600, backoff_s=5, backoff_cap_s=120)
    assert policy.delay([0, 10], 100) is None
    assert policy.delay([0, 10], 700) == 5  # both are older than 600 seconds by now
    assert policy.delay([0, 650], 700) == 10  # only the recent one counts


def test_exit_status_is_stored_as_a_code_a_windows_status_or_a_signal():
    assert supervisor.exit_fields(0) == {"code": 0, "code_hex": None, "signal": None}
    assert supervisor.exit_fields(1) == {"code": 1, "code_hex": None, "signal": None}
    assert supervisor.exit_fields(3221225786) == {"code": 3221225786, "code_hex": "0xC000013A", "signal": None}
    assert supervisor.exit_fields(-15)["signal"] == "SIGTERM"
    assert supervisor.exit_fields(-250)["signal"] == "signal 250"


# --- one start, one end -----------------------------------------------------------------------------------------

def test_a_role_that_fails_is_recorded_with_its_exit_code_and_not_restarted_by_default(tmp_path):
    child = FakeChild(7)
    run, settings, _clock, popen, diag, pins = _make(tmp_path, [child])

    assert run.run() == 1

    assert diag.events() == ["parents_pinned", "child_start", "child_exit"]
    assert diag.row("parents_pinned")["pinned"] == FakePins.pinned
    ended = diag.row("child_exit")
    assert (ended["code"], ended["child"], ended["child_pid"], ended["attempt"]) == (7, "gateway", child.pid, 1)
    assert ended["stop_requested"] is False and ended["uptime_s"] == 0.0
    assert len(popen.calls) == 1 and pins.closed


def test_the_role_is_started_the_way_up_used_to_start_it_and_recorded_for_down(tmp_path):
    child = FakeChild(0)
    run, settings, _clock, popen, _diag, _pins = _make(tmp_path, [child], role="runner")

    run.run()

    command, options = cli._child_launch(settings, "runner")
    if supervisor.sys.platform == "win32":
        command[0] = getattr(supervisor.sys, "_base_executable", supervisor.sys.executable)
    call = popen.calls[0]
    assert call["command"] == command and command[-3:] == ["-c", settings.config_path, "runner"]
    assert call["stdout_name"].endswith("runner.log")
    assert options["stderr"] == subprocess.STDOUT and call["options"]["stderr"] == subprocess.STDOUT
    if supervisor.sys.platform == "win32":
        assert call["options"]["env"]["__PYVENV_LAUNCHER__"] == supervisor.sys.executable
    assert call["options"]["env"]["PYTHONUNBUFFERED"] == "1" and call["options"]["close_fds"] is True
    assert ("creationflags" in call["options"]) != ("start_new_session" in call["options"])  # detached either way
    record = json.loads(cli._pid_file(settings, "runner").read_text(encoding="utf-8"))
    assert record == {"pid": child.pid, "role": "runner", "created": f"created:{child.pid}",
                      "config": settings.config_path}


def test_a_clean_exit_is_never_restarted_even_when_restarting_is_on(tmp_path):
    run, _settings_, clock, popen, diag, _pins = _make(tmp_path, [FakeChild(0)], restart=True)

    assert run.run() == 0

    assert len(popen.calls) == 1 and "restart_scheduled" not in diag.events() and clock.waits == []


def test_a_role_that_ended_before_it_could_be_recorded_still_gets_its_exit_code_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_process_created", lambda pid: None)  # already gone when the identity was read
    child = FakeChild(3221225786)
    run, settings, _clock, _popen, diag, _pins = _make(tmp_path, [child])

    assert run.run() == 1

    assert not cli._pid_file(settings, "gateway").exists()
    ended = diag.row("child_exit")
    assert ended["code"] == 3221225786 and ended["code_hex"] == "0xC000013A"
    assert diag.row("child_start")["created"] is None


# --- restarting -----------------------------------------------------------------------------------------------

def test_with_restarting_on_a_role_that_keeps_failing_is_started_again_with_backoff_then_left(tmp_path):
    children = [FakeChild(1), FakeChild(1), FakeChild(1)]
    run, settings, clock, popen, diag, _pins = _make(tmp_path, children, restart=True, max_restarts=2, backoff_s=5)

    assert run.run() == 1

    assert len(popen.calls) == 3  # the first start and two restarts
    assert [row["delay_s"] for row in diag.rows if row["event"] == "restart_scheduled"] == [5, 10]
    assert clock.waits == [0.5] * 10 + [0.5] * 20  # the waits are made in ticks, 5 s and then 10 s
    assert diag.events()[-1] == "restart_giving_up"
    assert json.loads(cli._pid_file(settings, "gateway").read_text(encoding="utf-8"))["pid"] == children[-1].pid


def test_a_restart_that_succeeds_ends_the_supervisor_cleanly_when_the_role_later_exits_0(tmp_path):
    run, _settings_, _clock, popen, diag, _pins = _make(tmp_path, [FakeChild(1), FakeChild(0)], restart=True)

    assert run.run() == 0

    assert len(popen.calls) == 2
    assert [row["attempt"] for row in diag.rows if row["event"] == "child_exit"] == [1, 2]


def test_a_role_that_refused_to_start_is_not_started_again(tmp_path):
    """Exit code 2 is labhq's own refusal (a rejected config, another config's live process on its state folder, #613):
    the same start would be refused the same way, so restarting only fills the log."""
    run, _settings_, clock, popen, diag, _pins = _make(tmp_path, [FakeChild(2), FakeChild(0)], restart=True)

    assert run.run() == 1

    assert len(popen.calls) == 1 and clock.waits == []
    assert diag.events()[-2:] == ["child_exit", "restart_skipped"]
    assert diag.row("restart_skipped")["reason"] == "refused_to_start"


def test_exit_code_2_is_only_special_when_restarting_is_on(tmp_path):
    run, _settings_, _clock, _popen, diag, _pins = _make(tmp_path, [FakeChild(2)])

    assert run.run() == 1

    assert "restart_skipped" not in diag.events()  # off: nothing was going to restart it anyway


def test_a_role_stopped_on_purpose_is_not_restarted(tmp_path):
    child = FakeChild(1)
    run, settings, _clock, popen, diag, _pins = _make(tmp_path, [child, FakeChild(0)], restart=True)
    child.during_wait = lambda: cli._mark_stopping(settings, "gateway")  # `down` marks it, then ends the role

    assert run.run() == 0

    assert len(popen.calls) == 1 and "restart_scheduled" not in diag.events()
    assert diag.row("child_exit")["stop_requested"] is True


def test_a_stop_during_the_backoff_cancels_the_restart(tmp_path):
    stop = threading.Event()
    run, _settings_, clock, popen, diag, _pins = _make(tmp_path, [FakeChild(1), FakeChild(0)], restart=True, stop=stop)
    run._wait_for = lambda seconds: (clock.wait(seconds), stop.set())

    assert run.run() == 0

    assert len(popen.calls) == 1 and diag.events().count("restart_scheduled") == 1


def test_a_stop_signal_is_passed_on_to_the_role_once_and_the_role_is_not_restarted(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    child = FakeChild(143, timeouts=3)
    passed_on = []

    def pass_on(pid, *_ignored):
        passed_on.append(pid)
        child.released = True

    monkeypatch.setattr(proc_tree, "kill_tree", pass_on)
    monkeypatch.setattr(os, "killpg", pass_on, raising=False)
    run, _settings_, _clock, popen, diag, _pins = _make(tmp_path, [child, FakeChild(0)], restart=True, stop=stop)

    assert run.run() == 0

    assert passed_on == [child.pid] and len(popen.calls) == 1
    assert diag.row("stop_forwarded")["child_pid"] == child.pid
    assert diag.row("child_exit")["stop_requested"] is True


# --- things that go wrong at the start --------------------------------------------------------------------------

def test_a_role_that_cannot_be_started_is_logged_and_the_supervisor_ends(tmp_path):
    run, settings, _clock, _popen, diag, pins = _make(tmp_path, [FileNotFoundError("no python")])

    assert run.run() == 1

    assert diag.row("child_start_failed")["reason"] == "FileNotFoundError"
    assert not cli._pid_file(settings, "gateway").exists() and pins.closed


@pytest.mark.parametrize("failure", ["identity", "record"])
def test_a_role_nobody_could_find_again_is_ended_instead_of_left_running(tmp_path, monkeypatch, failure):
    ended = []
    monkeypatch.setattr(proc_tree, "kill_tree", lambda pid, *a, **kw: ended.append(pid))
    monkeypatch.setattr(os, "killpg", lambda pid, sig: ended.append(pid), raising=False)
    if failure == "identity":
        def unreadable(pid):
            raise cli.ProcessIdentityError("denied")
        monkeypatch.setattr(cli, "_process_created", unreadable)
    else:
        def full(path, text):
            raise OSError("disk full")
        monkeypatch.setattr(supervisor, "atomic_write_text", full)
    child = FakeChild(0)
    run, settings, _clock, _popen, diag, _pins = _make(tmp_path, [child])

    assert run.run() == 1

    assert ended == [child.pid] and child.killed
    assert diag.row("child_start_failed")["reason"] in {"ProcessIdentityError", "OSError"}
    assert "child_start" not in diag.events()


def test_a_failed_pin_is_logged_and_the_role_still_starts(tmp_path):
    def broken():
        raise RuntimeError("private detail")

    run, _settings_, _clock, popen, diag, _pins = _make(tmp_path, [FakeChild(0)], pin=broken)

    assert run.run() == 0

    assert len(popen.calls) == 1
    assert list(diag.row("parents_pinned")["unpinned"].values()) == ["RuntimeError"]  # the type, never the message


def test_the_pins_are_closed_even_when_the_loop_raises(tmp_path):
    run, _settings_, _clock, _popen, _diag, pins = _make(tmp_path, [RuntimeError("boom")])

    with pytest.raises(RuntimeError):
        run.run()

    assert pins.closed


# --- the entry point --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [[], ["only-one"], ["", "web"], ["", "gateway", "extra"]])
def test_main_refuses_arguments_it_does_not_understand(argv, capsys):
    assert supervisor.main(argv) == 2
    assert "usage" in capsys.readouterr().err


def test_main_writes_the_supervisors_own_diagnostics_and_returns_the_loops_code(tmp_path, monkeypatch):
    config = tmp_path / "labhq.yaml"
    config.write_text(f"gateway:\n  state_dir: {tmp_path.as_posix()}/state\nrunner:\n  state_dir: {tmp_path.as_posix()}/state\n",
                      encoding="utf-8")
    seen = {}

    class Loop:
        def __init__(self, settings, role, diagnostics, *, stop):
            seen.update(role=role, stop=stop)

        def run(self):
            return 3

    monkeypatch.setattr(supervisor, "Supervisor", Loop)
    signals = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGINT", "SIGTERM")}
    try:
        assert supervisor.main([str(config), "runner"]) == 3
    finally:
        for name, handler in signals.items():
            signal.signal(getattr(signal, name), handler)

    rows = [json.loads(line) for line in
            (tmp_path / "state" / "logs" / "runner-supervisor-diagnostics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "start" and rows[0]["role"] == "runner-supervisor"
    assert rows[-1]["event"] == "exit" and rows[-1]["code"] == 3
    assert seen["role"] == "runner" and not seen["stop"].is_set()
