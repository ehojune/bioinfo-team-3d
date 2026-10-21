"""The supervisor with real processes (#589). These start and end real processes, so they are meant for CI.

Never run them on a machine whose gateway and runner must stay up. They use only a temporary state folder and a free
port on 127.0.0.1, never the PI's instance (port 8787), and each ends only the processes it started itself, one PID
at a time or through the supervisor's own tree.

`labhq.cli._child_launch` is replaced inside the supervisor's process, so the supervised "gateway" is a plain python
one-liner and no port is bound, except in the one test that runs the real gateway.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from labhq import cli, proc_tree, windows_launch
from labhq.process_diagnostics import latest_child_exit
from labhq.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"
SLEEP = "import time; time.sleep(120)"

SUPERVISE = """
import sys
from labhq import cli, supervisor

config, child_code = sys.argv[1], sys.argv[2]
cli._child_launch = lambda settings, role: ([sys.executable, "-c", child_code], cli._launch_options(settings))
raise SystemExit(supervisor.main([config, "gateway"]))
"""

# Only the finite exit-code logging contract uses this foreground harness. It makes no detachment claim.
LOGGING_SUPERVISE = """
import sys, subprocess
sys.path.insert(0, sys.argv[3])
from labhq import cli, supervisor, windows_launch
from tests.test_supervisor_process import _logging_options

config, child_code = sys.argv[1:3]
fake_command = [sys.executable, "-c", child_code]
def spawn_fake_gateway(command, *, state, role, popen=None, **options):
    assert role == "gateway" and command == fake_command
    return (popen or subprocess.Popen)(command, **options)
cli._child_launch = lambda settings, role: (fake_command, _logging_options())
windows_launch.spawn = spawn_fake_gateway
raise SystemExit(supervisor.main([config, "gateway"]))
"""

# Windows: launcher -> parent -> supervisor. The parent starts the supervisor, waits until it has pinned its
# ancestors, and exits; the launcher waits for the parent and exits too, so nothing but the supervisor can still hold
# a handle on the parent's process object.
PARENT = """
import sys, time
from pathlib import Path

supervise, config, logs, root = sys.argv[1:5]
sys.path.insert(0, root)  # Fresh direct children do not inherit the WMI caller's pytest sys.path.
from labhq.windows_launch import require_detached_job
from tests.test_supervisor_process import _start_script, Settings

require_detached_job()
child = _start_script(Settings.load(config), supervise, [config, "import time; time.sleep(120)"])
target = Path(logs) / "gateway-supervisor-diagnostics.jsonl"
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        if "parents_pinned" in target.read_text(encoding="utf-8"):
            break
    except OSError:
        pass
    time.sleep(0.1)
child.close()  # Release our handle; the supervisor alone must keep its exited ancestors pinned.
"""
LAUNCHER = """
import sys

parent, supervise, config, logs, root = sys.argv[1:6]
sys.path.insert(0, root)  # Also support direct launch, not only WMI's restored caller path.
from labhq.windows_launch import require_detached_job
from tests.test_supervisor_process import _start_script, Settings

require_detached_job()
child = _start_script(Settings.load(config), parent, [supervise, config, logs, root])
print(child.pid, flush=True)
try:
    child.wait(timeout=120)
finally:
    child.close()
"""


@pytest.mark.parametrize("script", ["PARENT", "LAUNCHER"])
def test_ancestor_script_imports_checkout_helper_without_inherited_path(tmp_path, monkeypatch, script):
    """Mock/static only: cached test modules must not mask a fresh script's missing checkout path."""
    import ast
    import builtins
    from importlib.machinery import PathFinder

    args = ["parent.py", "supervise.py", "config.yaml", "logs", str(ROOT)]
    monkeypatch.setattr(sys, "argv", ["launcher.py", *args] if script == "LAUNCHER" else args)
    # A direct Python script starts with its temporary directory, not pytest's injected checkout root.
    monkeypatch.setattr(sys, "path", [str(tmp_path), *(p for p in sys.path if p and Path(p).resolve() != ROOT)])
    original_import, checked = builtins.__import__, []

    def checked_import(name, *args, **kwargs):
        if name == "tests.test_supervisor_process":
            spec = PathFinder.find_spec("tests", sys.path)  # Bypass sys.modules' already imported fixture.
            if spec is None or str(ROOT / "tests") not in list(spec.submodule_search_locations or []):
                raise ModuleNotFoundError("No module named 'tests' in the detached checkout path")
            checked.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    tree = ast.parse(globals()[script], feature_version=(3, 10))
    end = next(i for i, node in enumerate(tree.body)
               if isinstance(node, ast.ImportFrom) and node.module == "tests.test_supervisor_process")
    # Execute imports/bootstrap only. No launch, Job query, wait, thread or signal may run here.
    exec(compile(ast.Module(body=tree.body[:end + 1], type_ignores=[]), f"<{script}-imports>", "exec"), {})
    assert checked == ["tests.test_supervisor_process"] and sys.path[0] == str(ROOT)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _settings(tmp_path: Path, supervisor_section: str = "") -> Settings:
    port = _free_port()
    assert port != 8787
    config = tmp_path / "labhq.yaml"
    config.write_text(
        "gateway:\n"
        f"  port: {port}\n"
        f"  url: ws://127.0.0.1:{port}\n"
        "  client_token: test-pi\n"
        "  runner_token: test-runner\n"
        f"  state_dir: {tmp_path.as_posix()}/state\n"
        "runner:\n"
        f"  state_dir: {tmp_path.as_posix()}/state\n" + supervisor_section, encoding="utf-8")
    return Settings.load(str(config))


def _logs(settings: Settings) -> Path:
    return cli._launch_state(settings) / "logs"


START_EVIDENCE_TAIL_BYTES = 16 * 1024


def _print_start_evidence(settings: Settings, stream=None) -> None:
    """Failure output only: the bounded tail of this test's own supervisor logs, so a start that ended early is
    explained in the CI report (#843 run 39684337037 kept only the log path). Never raises; the caller re-raises the
    original failure unchanged. The fixture tokens are masked and control characters are not echoed."""
    stream = sys.stderr if stream is None else stream
    secrets = [token for token in (settings.gateway.client_token, settings.gateway.runner_token) if token]
    for name in ("gateway-supervisor.log", "gateway-supervisor-diagnostics.jsonl"):
        try:
            path = _logs(settings) / name
            if path.is_symlink():
                raise OSError("symlink")
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - START_EVIDENCE_TAIL_BYTES))
                text = handle.read(START_EVIDENCE_TAIL_BYTES).decode("utf-8", errors="replace")
            for secret in secrets:
                text = text.replace(secret, "[redacted]")
            text = "".join(c if c.isprintable() or c in "\n\t" else "?" for c in text)
            print(f"--- {name}: last {len(text)} chars of {size} bytes ---\n{text}", file=stream)
        except Exception as exc:  # noqa: BLE001 - evidence only; a missing or unreadable log hides nothing
            print(f"--- {name}: unavailable ({type(exc).__name__}) ---", file=stream)


def _rows(settings: Settings) -> list[dict]:
    path = _logs(settings) / "gateway-supervisor-diagnostics.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events(settings: Settings) -> list[str]:
    return [row["event"] for row in _rows(settings)]


def _failure_evidence(settings: Settings) -> str:
    """Include existing safe Job/launch facts instead of only child_start_failed's exception class."""
    launch = _logs(settings) / "detached-launch.jsonl"
    files = [launch, *(_logs(settings) / f"{name}-fixture.log" for name in ("launcher", "parent", "supervise"))]
    return f"supervisor={json.dumps(_rows(settings))}\n" + "\n".join(
        f"{path.name}={path.read_text(encoding='utf-8')}" for path in files if path.exists())


class _ScriptProcess:
    """File-backed output works for both a Popen and the broker's retained-handle process."""
    def __init__(self, child, output, settings):
        self.child, self.output, self.settings = child, output, settings
        self.pid = child.pid

    @property
    def returncode(self):
        return self.child.poll()

    def poll(self):
        return self.child.poll()

    def wait(self, timeout):
        return self.child.wait(timeout=timeout)

    def kill(self):
        self.child.kill()

    def communicate(self, timeout):
        self.wait(timeout)
        return self.output.read_text(encoding="utf-8") + "\n" + _failure_evidence(self.settings), None

    def close(self):
        close = getattr(self.child, "close", None)
        if close is not None:
            close()


def _start_script(settings: Settings, script, args) -> _ScriptProcess:
    """Use the production boundary, including base interpreter, BREAKAWAY and ANY-Job verification.

    Raw venv Popen can put the supervisor in the redirector's Job before it starts a fake role.
    The ancestor test starts its entire chain here; no intermediary reintroduces that redirector.
    """
    output = _logs(settings) / f"{Path(script).stem}-fixture.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        try:
            child = windows_launch.spawn([sys.executable, str(script), *args], state=cli._launch_state(settings),
                                         role="gateway-supervisor", stdout=stream, **cli._launch_options(settings))
        except windows_launch.LaunchError as exc:
            try:
                evidence = _failure_evidence(settings)[-8192:]
            except (OSError, ValueError) as error:
                evidence = f"fixture evidence unavailable: {type(error).__name__}"
            raise AssertionError(f"detached fixture launch failed before supervisor startup\n{evidence}") from exc
    return _ScriptProcess(child, output, settings)


def _logging_options():
    options = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT, "close_fds": True}
    if sys.platform != "win32":
        options["start_new_session"] = True  # Keep the existing POSIX teardown's owned role group.
    return options


def _start_logging_script(settings: Settings, script, args) -> _ScriptProcess:
    assert os.environ.get(windows_launch.CHECK_ENV) != "1"  # Never remove a requested managed-entry guard.
    output = _logs(settings) / "exit-logging-fixture.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        child = subprocess.Popen([sys.executable, str(script), *args, str(ROOT)], stdout=stream, **_logging_options())
    return _ScriptProcess(child, output, settings)


@pytest.mark.parametrize("evidence_kind", ["absent", "journal", "oversize", "unreadable"])
def test_detached_fixture_launch_failure_exposes_existing_evidence_without_retry(tmp_path, monkeypatch, evidence_kind):
    """Fake launch only: an unknown Create outcome must remain a failure, with its original cause."""
    from unittest.mock import Mock

    state = tmp_path / "state"
    logs = state / "logs"
    logs.mkdir(parents=True)
    if evidence_kind == "journal":
        (logs / "detached-launch.jsonl").write_text(
            '{"status":"wmi_attempt"}\n'
            '{"status":"wmi_failed","error":"TimeoutExpired","reason":"native_or_protocol"}\n',
            encoding="utf-8")
    failure = windows_launch.LaunchError("WMI Create outcome unknown")
    failure.__cause__ = subprocess.TimeoutExpired("fake-powershell", 10)
    spawn = Mock(side_effect=failure)
    monkeypatch.setattr(windows_launch, "spawn", spawn)
    monkeypatch.setattr(cli, "_launch_state", lambda settings: state)
    monkeypatch.setattr(cli, "_launch_options", lambda settings: {})
    if evidence_kind == "oversize":
        monkeypatch.setattr(sys.modules[__name__], "_failure_evidence", lambda settings: "x" * 20000 + "END")
    elif evidence_kind == "unreadable":
        def unreadable(settings):
            raise OSError("fake unreadable journal")
        monkeypatch.setattr(sys.modules[__name__], "_failure_evidence", unreadable)

    with pytest.raises(AssertionError, match="before supervisor startup") as caught:
        _start_script(object(), tmp_path / "supervise.py", [])

    assert caught.value.__cause__ is failure
    assert isinstance(failure.__cause__, subprocess.TimeoutExpired)
    if evidence_kind in {"absent", "journal"}:
        assert 'supervisor=[]' in str(caught.value)
    if evidence_kind == "journal":
        assert 'wmi_attempt' in str(caught.value) and 'TimeoutExpired' in str(caught.value)
    elif evidence_kind == "oversize":
        assert str(caught.value).endswith("END") and len(str(caught.value)) < 8300
    elif evidence_kind == "unreadable":
        assert "evidence unavailable: OSError" in str(caught.value)
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["stdout"].closed
    assert not list(state.glob("*.pid"))  # No invented process/readiness record or retry.


def _wait_until(check, seconds: float = 60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    return check()


def _role_record(settings: Settings) -> dict | None:
    state, record, _reason = cli._process_record(settings, "gateway", cleanup=False)
    return record if state == "alive" else None


@pytest.fixture
def run(tmp_path):
    """Start the supervisor script with a fake gateway; whatever is still running afterwards is ended."""
    started: list[tuple[_ScriptProcess, Settings]] = []

    def start(child_code: str, supervisor_section: str = "") -> tuple[_ScriptProcess, Settings]:
        settings = _settings(tmp_path, supervisor_section)
        script = tmp_path / "supervise.py"
        script.write_text(SUPERVISE, encoding="utf-8")
        process = _start_script(settings, script, [settings.config_path, child_code])
        started.append((process, settings))
        return process, settings

    yield start
    for process, settings in started:
        record = _role_record(settings)
        if process.poll() is None:
            process.kill()
        if record:  # the supervised role: its own process group on POSIX, a tree under the launcher on Windows
            if WINDOWS:
                proc_tree.kill_tree(record["pid"], created=proc_tree.parse_created(record["created"]))
            else:
                try:
                    os.killpg(record["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait(timeout=30)
        process.close()


@pytest.fixture
def logging_run(run, monkeypatch):
    """Only exit logging: ordinary owned Popen, finite fake role, and the unchanged run fixture teardown."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "SUPERVISE", LOGGING_SUPERVISE)
    monkeypatch.setattr(module, "_start_script", _start_logging_script)
    return run


def _end_role(settings: Settings, record: dict) -> None:
    """End the supervised role, one PID: the one its record names, after checking that it is still that process."""
    assert cli._process_created(record["pid"]) == record["created"]
    os.kill(record["pid"], signal.SIGTERM)


def test_the_supervisor_logs_the_exit_code_of_a_role_that_fails(logging_run):
    # It lingers for a second so its record is written before it ends: a role that ends sooner has no record to read.
    process, settings = logging_run("import sys, time; time.sleep(1); sys.exit(7)")

    output, _ = process.communicate(timeout=120)

    assert process.returncode == 1, output
    rows = _rows(settings)
    events = [row["event"] for row in rows]
    assert events[0] == "start"
    assert "child_start" in events and "child_exit" in events, output
    assert events.index("parents_pinned") < events.index("child_start") < events.index("child_exit")
    ended = next(row for row in rows if row["event"] == "child_exit")
    assert ended["code"] == 7 and ended["stop_requested"] is False and ended["child"] == "gateway"
    assert rows[-1]["event"] == "exit" and rows[-1]["code"] == 1
    record = json.loads(cli._pid_file(settings, "gateway").read_text(encoding="utf-8"))
    assert record["pid"] == ended["child_pid"] and record["role"] == "gateway"
    assert latest_child_exit(_logs(settings), "gateway")["code"] == 7


def test_with_restarting_on_a_role_that_keeps_failing_is_started_again_and_then_left(run):
    process, settings = run("import sys; sys.exit(3)",
                            "supervisor:\n  restart: true\n  max_restarts: 2\n  backoff_s: 0\n  backoff_cap_s: 0\n")

    output, _ = process.communicate(timeout=120)

    assert process.returncode == 1, output
    events = _events(settings)
    assert events.count("child_start") == 3 and events.count("child_exit") == 3, output  # start and two restarts
    assert events.count("restart_scheduled") == 2 and events[-2:] == ["restart_giving_up", "exit"]
    assert {row["code"] for row in _rows(settings) if row["event"] == "child_exit"} == {3}


def test_a_role_killed_from_outside_is_restarted_but_one_stopped_on_purpose_is_not(run):
    process, settings = run(SLEEP, "supervisor:\n  restart: true\n  backoff_s: 0\n  backoff_cap_s: 0\n")
    first = _wait_until(lambda: _role_record(settings))
    assert first, _failure_evidence(settings)

    def restarted():
        record = _role_record(settings)
        return record if record and record["pid"] != first["pid"] else None

    _end_role(settings, first)  # an outside kill: nobody asked for this stop
    second = _wait_until(restarted)
    assert second, _failure_evidence(settings)

    cli._mark_stopping(settings, "gateway")  # what `down` writes before it ends anything
    _end_role(settings, second)
    output, _ = process.communicate(timeout=120)

    assert process.returncode == 0, output
    exits = [row for row in _rows(settings) if row["event"] == "child_exit"]
    assert [row["stop_requested"] for row in exits] == [False, True]
    assert [row["child_pid"] for row in exits] == [first["pid"], second["pid"]]
    assert _events(settings).count("child_start") == 2


@pytest.mark.skipif(WINDOWS, reason="Windows has no SIGTERM to hand to a detached process; `down` ends its tree")
def test_a_stop_signal_to_the_supervisor_ends_the_role_and_the_supervisor_without_a_restart(run):
    process, settings = run(SLEEP, "supervisor:\n  restart: true\n  backoff_s: 0\n  backoff_cap_s: 0\n")
    assert _wait_until(lambda: _role_record(settings)), process.poll()

    os.kill(process.pid, signal.SIGTERM)
    output, _ = process.communicate(timeout=120)

    assert process.returncode == 0, output
    ended = [row for row in _rows(settings) if row["event"] == "child_exit"]
    assert len(ended) == 1 and ended[0]["stop_requested"] is True and ended[0]["signal"] == "SIGTERM"
    assert "stop_forwarded" in _events(settings) and "restart_scheduled" not in _events(settings)


@pytest.mark.skipif(not WINDOWS, reason="Windows keeps the parent PID of an exited parent")
def test_the_supervisor_holds_its_exited_parent_so_that_pid_is_never_handed_out(tmp_path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def exited_but_held(pid: int) -> bool:
        """The process is over, yet its object still exists: someone holds a handle, so Windows has not freed the PID."""
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False  # no such process: the PID is free to be handed out again
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)

    settings = _settings(tmp_path)
    scripts = {"supervise.py": SUPERVISE, "parent.py": PARENT, "launcher.py": LAUNCHER}
    for name, text in scripts.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    launcher = _start_script(settings, tmp_path / "launcher.py",
                             [str(tmp_path / "parent.py"), str(tmp_path / "supervise.py"),
                              settings.config_path, str(_logs(settings)), str(ROOT)])
    supervisor_pid = None
    try:
        def parent_started():
            text = launcher.output.read_text(encoding="utf-8").strip()
            return int(text) if text.isdecimal() else None
        parent_pid = _wait_until(parent_started)
        assert parent_pid is not None, _failure_evidence(settings)
        launcher.wait(timeout=120)  # the launcher ends once the parent has: nobody else holds the parent now

        assert _wait_until(lambda: exited_but_held(parent_pid)), (
            f"the exited parent {parent_pid} was not held by the supervisor: {_failure_evidence(settings)}")
        pinned = next(row for row in _rows(settings) if row["event"] == "parents_pinned")
        assert parent_pid in pinned["pinned"], pinned
        for _ in range(10):  # and it stays held for as long as the supervisor lives
            assert exited_but_held(parent_pid)
            time.sleep(0.3)
        supervisor_pid = next(row for row in _rows(settings) if row["event"] == "start")["pid"]
    finally:
        if launcher.poll() is None:
            launcher.kill()
        launcher.wait(timeout=30)
        launcher.close()
        if supervisor_pid is None:
            starts = [row for row in _rows(settings) if row["event"] == "start"]
            supervisor_pid = starts[0]["pid"] if starts else None
        if supervisor_pid is not None:  # the supervisor, and the fake gateway under it
            created = cli._process_created(supervisor_pid)
            if created:
                proc_tree.kill_tree(supervisor_pid, created=proc_tree.parse_created(created))


# --- the real gateway under a real supervisor ----------------------------------------------------------------

def _parent_pid(pid: int) -> int | None:
    if WINDOWS:
        return dict(proc_tree._table() or []).get(pid)
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        pytest.skip("needs /proc or Windows to read a parent PID")
    text = stat.read_text(encoding="ascii")
    return int(text[text.rindex(")") + 2:].split()[1])


def _reaping_popen(started):
    """Reaps what `up` would leave to init, so an ended supervisor is not a zombie that still looks alive."""
    def popen(command, **kwargs):
        child = subprocess.Popen(command, **kwargs)
        started.append(child)
        threading.Thread(target=child.wait, daemon=True).start()
        return child
    return popen


def test_a_real_gateway_runs_as_its_supervisors_child_and_down_ends_both(tmp_path):
    settings = _settings(tmp_path)
    started: list[subprocess.Popen] = []
    try:
        try:
            cli._start_background(settings, "gateway", popen=_reaping_popen(started))
        except BaseException:
            _print_start_evidence(settings)  # then the original SystemExit/exception propagates as before
            raise
        gateway_log = _logs(settings) / "gateway.log"
        assert cli._wait_for_health(settings, runner=False, timeout=90) is not None, (
            gateway_log.read_text(encoding="utf-8", errors="replace") if gateway_log.exists() else "no gateway log")
        supervisor_record = json.loads(cli._pid_file(settings, "gateway-supervisor").read_text(encoding="utf-8"))
        gateway_record = json.loads(cli._pid_file(settings, "gateway").read_text(encoding="utf-8"))
        rows = _rows(settings)
        started_row = next(row for row in rows if row["event"] == "child_start")
        supervisor_pid = next(row for row in rows if row["event"] == "start")["pid"]  # the interpreter running the loop

        assert started_row["child_pid"] == gateway_record["pid"]
        assert _parent_pid(gateway_record["pid"]) == supervisor_pid  # the role's parent is the live supervisor
        assert cli._process_created(supervisor_pid) is not None
        row = next(row for row in cli._local_process_diagnostics(settings) if row["name"] == "gateway")
        assert row["status"] == "ok" and f"감독 PID {supervisor_record['pid']} 실행 중" in row["detail"]

        assert cli._stop_background(settings, "gateway") == gateway_record["pid"]

        assert _wait_until(lambda: cli._process_created(gateway_record["pid"]) is None)
        assert _wait_until(lambda: cli._process_created(supervisor_record["pid"]) is None)
        assert not cli._pid_file(settings, "gateway").exists()
        assert not cli._pid_file(settings, "gateway-supervisor").exists()
        assert cli._stopping_file(settings, "gateway").exists()
        if not WINDOWS:  # a Windows tree kill can end the supervisor before it writes the row
            ended = latest_child_exit(_logs(settings), "gateway")
            assert ended and ended["stop_requested"] is True
    finally:
        state, record, _reason = cli._process_record(settings, "gateway", cleanup=False)
        if state == "alive":
            cli._terminate_tree(int(record["pid"]), created=str(record["created"]))
        for child in started:
            if child.poll() is None:
                child.kill()


def test_start_evidence_is_bounded_masked_and_never_replaces_the_failure(tmp_path, monkeypatch):
    """Pure: no process starts. Only this test's own temporary log files are read."""
    import io
    config = tmp_path / "labhq.yaml"
    config.write_text("gateway:\n  client_token: test-pi\n  runner_token: test-runner\n"
                      f"  state_dir: {tmp_path.as_posix()}/state\n", encoding="utf-8")
    settings = Settings.load(str(config))
    logs = _logs(settings)
    logs.mkdir(parents=True)
    body = "x" * (START_EVIDENCE_TAIL_BYTES + 100) + "\nrunner test-runner client test-pi \x1b[31mcolor\nTraceback: boom\n"
    (logs / "gateway-supervisor.log").write_text(body, encoding="utf-8")
    out = io.StringIO()
    _print_start_evidence(settings, out)
    text = out.getvalue()
    assert "Traceback: boom" in text and "[redacted]" in text and "\x1b" not in text
    assert "test-runner" not in text and "test-pi" not in text
    assert len(text) < START_EVIDENCE_TAIL_BYTES + 400  # bounded tail, not the whole file
    assert "gateway-supervisor-diagnostics.jsonl: unavailable (FileNotFoundError)" in text
    monkeypatch.setattr(cli, "_launch_state", lambda s: (_ for _ in ()).throw(RuntimeError("state")))
    broken = io.StringIO()
    _print_start_evidence(settings, broken)  # must not raise
    assert broken.getvalue().count("unavailable (RuntimeError)") == 2
