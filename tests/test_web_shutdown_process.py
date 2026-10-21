"""PR #576 P1 with real processes: a gateway started the way `up` starts it, a stand-in runner and the real watcher.

Only a free port on 127.0.0.1 and a temporary state folder are used; the PI's instance (port 8787) is never touched.
A forced kill (SIGKILL, or taskkill /F on Windows) skips the process's exit hooks, so these tests tell a graceful end
from a forced one by what the gateway wrote while it was shutting down."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from labhq import cli
from labhq.gateway.shutdown import EXIT_GRACE_SECONDS, spawn_exit_watch, watch_gateway_exit
from labhq.settings import Settings
from labhq.store import StateStore
from labhq.util import atomic_write_text

PI = {"Authorization": "Bearer test-pi"}
SLEEPER = [sys.executable, "-c", "import time; time.sleep(300)"]
DETACH = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def instance(tmp_path):
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
        f"  state_dir: {tmp_path.as_posix()}/state\n", encoding="utf-8")
    settings = Settings.load(str(config))
    children: list[subprocess.Popen] = []
    yield settings, children
    for child in children:
        if child.poll() is None:
            child.kill()


def _reaping_popen(children):
    """A child that ended stays a zombie, and a zombie still looks alive to the identity check, until its parent waits."""
    def popen(command, **kwargs):
        child = subprocess.Popen(command, **kwargs)
        children.append(child)
        threading.Thread(target=child.wait, daemon=True).start()
        return child
    return popen


def _quiet_sleeper(children) -> subprocess.Popen:
    return _reaping_popen(children)(SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **DETACH)


def _record(settings, role: str, child: subprocess.Popen) -> str:
    created = cli._process_created(child.pid)
    cli._launch_state(settings).mkdir(parents=True, exist_ok=True)
    atomic_write_text(cli._pid_file(settings, role), json.dumps(
        {"pid": child.pid, "role": role, "created": created, "config": settings.config_path}) + "\n")
    return created


def _logs(settings) -> Path:
    return cli._launch_state(settings) / "logs"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _wait_until(check, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return check()


def _launch_journal(path: Path) -> str:
    """Bounded test diagnostics: project fixed facts, never provider output or commands."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 16384)
            stream.seek(start)
            raw = stream.read(16384)
        if start:
            raw = raw.partition(b"\n")[2]  # discard a possibly truncated first frame
    except FileNotFoundError:
        return "(journal missing)"
    except OSError:
        return "(journal unreadable)"
    enums = {
        "status": ("attempt", "denied", "spawn_failed", "unverified", "cleanup_failed", "direct_reaped",
                   "job_verified", "wmi_attempt", "wmi_unverified", "wmi_verified", "wmi_failed", "wmi_control_cleanup_failed"),
        "phase": ("provider_create", "process_open", "process_identity", "receipt_wait", "identity_verify", "job_verify", "ack_write"),
        "reason": ("provider_timeout", "provider_protocol", "protocol_error", "native_error", "provider_subprocess",
                   "create_failed", "invalid_returned_pid", "receipt_timeout", "identity_mismatch", "job_unverified",
                   "system_directory_unknown", "native_or_protocol", "request_too_large"),
        "error": ("BrokerFailure", "TimeoutExpired", "CalledProcessError", "SubprocessError", "PermissionError",
                  "FileNotFoundError", "TimeoutError", "OSError", "ValueError", "TypeError", "OtherError"),
        "trace_status": ("absent", "partial", "phases_complete", "invalid_or_partial"),
    }
    def facts(row):
        result = {key: row[key] for key, allowed in enums.items() if row.get(key) in allowed}
        for key in ("code", "returncode", "elapsed_ms"):
            value = row.get(key)
            valid = (type(value) in (int, float) and 0 <= value <= 60000) if key == "elapsed_ms" else (
                type(value) is int and -2**32 <= value <= 2**32)
            if valid:
                result[key] = value
        return result
    rows = [{"journal": "tail_only"}] if start else []
    for line in raw.splitlines()[-4:]:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            rows.append({"journal": "malformed"})
            continue
        if not isinstance(row, dict):
            rows.append({"journal": "malformed"})
            continue
        if row.get("role") != "gateway-supervisor":
            continue
        safe = facts(row)
        provider = row.get("provider")
        if isinstance(provider, dict):
            safe["provider"] = facts(provider)
            phases = provider.get("phases")
            if isinstance(phases, list):
                valid = []
                for index, step in enumerate(phases[:7]):
                    if not isinstance(step, dict) or step.get("phase") != (
                            "powershell_started", "stdin_open", "header_read", "input_read", "input_accepted", "create_entered", "create_returned")[index]:
                        break
                    elapsed = facts(step).get("elapsed_ms")
                    if elapsed is None or (valid and elapsed < valid[-1]["elapsed_ms"]):
                        break
                    valid.append({"phase": step["phase"], "elapsed_ms": elapsed})
                safe["provider"]["phases"] = valid
        rows.append(safe)
    return json.dumps(rows)


def _start_gateway(settings, children) -> tuple[int, str]:
    try:
        cli._start_background(settings, "gateway", popen=_reaping_popen(children))
        assert cli._wait_for_health(settings, runner=False, timeout=90) is not None, _text(_logs(settings) / "gateway.log")
        record = json.loads(cli._pid_file(settings, "gateway").read_text(encoding="utf-8"))
        return record["pid"], record["created"]
    except (Exception, SystemExit):
        for name in ("gateway-supervisor.log", "gateway.log"):
            try:
                text = _text(_logs(settings) / name) or "(missing or empty)"
            except OSError as exc:
                text = f"(could not read log: {exc})"
            print(f"\n--- {name} ---\n{text}", file=sys.stderr)
        print("\n--- detached-launch.jsonl ---\n" + _launch_journal(_logs(settings) / "detached-launch.jsonl"),
              file=sys.stderr)
        raise  # keep the original exception and traceback, including SystemExit(2)


@pytest.mark.parametrize("failure", [SystemExit, RuntimeError])
@pytest.mark.parametrize("logs", ["present", "missing", "unreadable"])
def test_start_gateway_failure_keeps_the_exception_and_reports_logs(tmp_path, monkeypatch, capsys, failure, logs):
    # Only the start boundary is faked: no native fixture, port, process or thread.
    settings, children, popen = object(), [], object()
    error = failure(2 if failure is SystemExit else "synthetic startup failure")
    calls = []
    monkeypatch.setattr(__name__ + "._logs", lambda _: tmp_path)
    monkeypatch.setattr(__name__ + "._reaping_popen", lambda items: popen if items is children else None)

    def start(actual_settings, role, **kwargs):
        calls.append((actual_settings, role, kwargs))
        raise error

    monkeypatch.setattr(cli, "_start_background", start)
    monkeypatch.setattr(cli, "_wait_for_health", lambda *a, **kw: pytest.fail("startup failed before health"))
    if logs == "present":
        (tmp_path / "gateway-supervisor.log").write_bytes("supervisor 상태\n".encode("utf-8") + b"\xff")
        (tmp_path / "gateway.log").write_text("gateway fixture cause", encoding="utf-8")
    elif logs == "unreadable":
        def unreadable(_path):
            raise PermissionError("fixture log denied")
        monkeypatch.setattr(__name__ + "._text", unreadable)

    with pytest.raises(failure) as caught:
        _start_gateway(settings, children)
    assert caught.value is error
    assert calls == [(settings, "gateway", {"popen": popen})]  # no retry or launcher bypass
    stderr = capsys.readouterr().err
    assert "gateway-supervisor.log" in stderr and "gateway.log" in stderr
    if logs == "present":
        assert "supervisor 상태\n\ufffd" in stderr and "gateway fixture cause" in stderr
    elif logs == "missing":
        assert stderr.count("missing or empty") == 2
    else:
        assert stderr.count("fixture log denied") == 2


@pytest.mark.parametrize("journal", ["present", "missing", "unreadable", "malformed", "oversize", "bad-phase"])
def test_start_gateway_reports_only_bounded_allowlisted_launch_facts(tmp_path, monkeypatch, capsys, journal):
    error = SystemExit(2)
    monkeypatch.setattr(__name__ + "._logs", lambda _: tmp_path)
    monkeypatch.setattr(__name__ + "._reaping_popen", lambda _: object())
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(cli, "_start_background", fail)
    path = tmp_path / "detached-launch.jsonl"
    row = {"role": "gateway-supervisor", "status": "wmi_failed", "phase": "provider_create",
           "reason": "provider_timeout", "error": "TimeoutExpired", "code": None,
           "cmd": "SECRET-command", "stdout": "SECRET-stdout", "env": {"SECRET": "value"},
           "provider": {"elapsed_ms": 10000, "trace_status": "partial", "stderr": "SECRET-stderr",
                        "phases": [{"phase": name, "elapsed_ms": index}
                                   for index, name in enumerate(("powershell_started", "stdin_open", "header_read", "input_read", "input_accepted",
                                                                 "create_entered", "create_returned"))]}}
    if journal == "bad-phase":
        row["provider"]["phases"] = [{"phase": "powershell_started", "elapsed_ms": 1},
                                      {"phase": "SECRET-phase", "elapsed_ms": 2}]
    if journal in ("present", "oversize", "bad-phase"):
        # Invalid fields and foreign roles never become diagnostic text.
        unsafe = {"role": "gateway-supervisor", "status": "wmi_failed", "error": "SECRET-class",
                  "reason": "SECRET-reason", "code": "SECRET-code", "phase": "SECRET-phase",
                  "provider": {"returncode": "SECRET-returncode", "elapsed_ms": float('inf')}}
        path.write_text(("SECRET-prefix\n" * 2000 if journal == "oversize" else "")
                        + json.dumps(unsafe) + "\n" + json.dumps({**row, "role": "SECRET-role"})
                        + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    elif journal == "malformed":
        path.write_bytes(b'SECRET-malformed{\xff\n')
    elif journal == "unreadable":
        original = Path.open
        def denied(p, *a, **kw):
            if p == path:
                raise PermissionError("SECRET-file-message")
            return original(p, *a, **kw)
        monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(SystemExit) as caught:
        _start_gateway(object(), [])
    assert caught.value is error
    stderr = capsys.readouterr().err
    assert "detached-launch.jsonl" in stderr and "SECRET" not in stderr
    assert len(stderr) < 4096
    if journal in ("present", "oversize"):
        assert '"reason": "provider_timeout"' in stderr and '"elapsed_ms": 10000' in stderr
        assert '"phase": "powershell_started"' in stderr
        assert '"phase": "input_read"' in stderr and '"phase": "create_returned"' in stderr
        if journal == "oversize":
            assert '"journal": "tail_only"' in stderr
    elif journal == "bad-phase":
        assert '"phase": "powershell_started"' in stderr and '"phase": "input_read"' not in stderr
    else:
        assert journal in stderr


def _gateway_events(settings) -> set[str]:
    """The state folder is new for each test, so every record is this gateway's. (On Windows the recorded PID is the
    venv launcher's and the interpreter writing these records has another.)"""
    rows = [json.loads(line) for line in _text(_logs(settings) / "gateway-diagnostics.jsonl").splitlines() if line]
    return {row["event"] for row in rows}


@contextmanager
def _web_page_connected(settings):
    """The web office keeps one websocket open; Uvicorn has to close it to finish its shutdown."""
    from websockets.sync.client import connect

    with connect(f"ws://127.0.0.1:{settings.gateway.port}/ws/client?token=test-pi", open_timeout=15) as page:
        page.recv(timeout=15)  # the snapshot: the page is connected
        yield page


def _assert_graceful_end(settings, pid, created, runner) -> None:
    started = time.monotonic()
    assert cli._wait_for_process_end(pid, created, 60), "the gateway never ended"
    assert time.monotonic() - started < EXIT_GRACE_SECONDS, "the gateway ended only after the watcher's grace period"
    gateway_log = _text(_logs(settings) / "gateway.log")
    # Uvicorn finished its shutdown, so the app's shutdown hooks ran; a kill leaves none of this behind.
    assert "Application shutdown complete" in gateway_log, gateway_log
    assert {"uvicorn_end", "exit"} <= _gateway_events(settings)
    assert cli._process_created(runner.pid) is None, "the runner was left running"
    assert _wait_until(lambda: not cli._pid_file(settings, "gateway").exists())
    assert not cli._pid_file(settings, "runner").exists()
    watch_log = lambda: _text(_logs(settings) / "gateway-exit-watch.log")
    assert _wait_until(lambda: "정상 종료" in watch_log()), watch_log()
    assert "강제" not in watch_log()


def test_web_shutdown_ends_a_real_gateway_through_its_own_shutdown(instance):
    settings, children = instance
    pid, created = _start_gateway(settings, children)
    runner = _quiet_sleeper(children)
    _record(settings, "runner", runner)
    with _web_page_connected(settings):
        response = httpx.post(f"http://127.0.0.1:{settings.gateway.port}/api/shutdown", json={}, headers=PI, timeout=30)
        assert response.status_code == 202, response.text
        _assert_graceful_end(settings, pid, created, runner)


def test_forced_web_shutdown_with_running_work_also_ends_gracefully_and_keeps_the_record(instance):
    settings, children = instance
    pid, created = _start_gateway(settings, children)
    runner = _quiet_sleeper(children)
    _record(settings, "runner", runner)
    base = f"http://127.0.0.1:{settings.gateway.port}"
    request_id = httpx.post(f"{base}/api/requests", json={"text": "합성 요청"}, headers=PI,
                            timeout=30).json()["request_id"]
    assert httpx.post(f"{base}/api/shutdown", json={}, headers=PI, timeout=30).status_code == 409
    with _web_page_connected(settings):
        response = httpx.post(f"{base}/api/shutdown", json={"force": True, "confirmation": "interrupt-running-work"},
                              headers=PI, timeout=30)
        assert response.status_code == 202, response.text
        _assert_graceful_end(settings, pid, created, runner)
    # The shutdown hook reported the interrupted request, and its record had already been saved.
    assert f"gateway shutdown with running requests: {request_id}" in _text(_logs(settings) / "gateway.log")
    store = StateStore(Path(settings.path(settings.gateway.state_dir)) / "gateway.sqlite3")
    try:
        assert request_id in store.all("request")
    finally:
        store.close()


def test_the_watcher_stops_a_gateway_that_never_finishes_its_shutdown(instance, capsys):
    settings, children = instance
    stuck = _quiet_sleeper(children)
    created = _record(settings, "gateway", stuck)
    assert watch_gateway_exit(settings, stuck.pid, created, grace=0.5) == 1
    assert cli._process_created(stuck.pid) is None and not cli._pid_file(settings, "gateway").exists()
    assert "강제로 종료" in capsys.readouterr().out


def test_the_watcher_reports_a_gateway_that_ended_by_itself(instance, capsys):
    settings, children = instance
    done = _quiet_sleeper(children)
    created = _record(settings, "gateway", done)
    done.kill()
    assert watch_gateway_exit(settings, done.pid, created, grace=5) == 0
    assert not cli._pid_file(settings, "gateway").exists() and "정상 종료" in capsys.readouterr().out


class _ProcTree:
    """/proc where the process is exiting: its stat file answers with `error`."""

    def __init__(self, error):
        self.error = error

    def is_dir(self):
        return True

    def __truediv__(self, _name):
        return self

    def read_text(self, **_kwargs):
        raise self.error


def _linux_proc(monkeypatch, error):
    monkeypatch.setattr(cli, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(cli, "Path", lambda *_args: _ProcTree(error))


def test_a_process_that_is_exiting_reads_as_gone_not_unreadable(monkeypatch):
    # CI 2026-10-14 (ubuntu 3.12): the watcher read /proc/<pid>/stat right after SIGKILL, got ESRCH ("No such process"),
    # called the identity unreadable and returned 2 instead of 0 for a gateway that had ended.
    _linux_proc(monkeypatch, ProcessLookupError(3, "No such process"))
    assert cli._process_created(4242) is None


def test_a_proc_error_that_is_not_a_missing_process_stays_unreadable(monkeypatch):
    _linux_proc(monkeypatch, PermissionError(13, "Permission denied"))
    with pytest.raises(cli.ProcessIdentityError):
        cli._process_created(4242)


def test_the_watcher_leaves_a_process_it_cannot_identify_alone(instance, monkeypatch, capsys):
    settings, _ = instance

    def unreadable(pid):
        raise cli.ProcessIdentityError("no access")

    monkeypatch.setattr(cli, "_process_created", unreadable)
    monkeypatch.setattr(cli, "_terminate_tree", lambda *a, **kw: pytest.fail("killed a process it could not identify"))
    assert watch_gateway_exit(settings, 4242, "created", grace=0.2) == 2
    assert "건드리지 않습니다" in capsys.readouterr().out


def test_the_watcher_is_started_detached_outside_the_gateways_process_group(instance, monkeypatch):
    settings, _ = instance
    seen = {}

    def popen(command, **kwargs):
        seen.update(command=command, **kwargs)
        return object()

    monkeypatch.setattr(subprocess, "Popen", popen)
    spawn_exit_watch(settings, 777, "created-777")
    assert seen["command"][1:] == ["-m", "labhq.gateway.shutdown", settings.config_path, "777", "created-777"]
    if sys.platform == "win32":
        assert seen["creationflags"] & 0x00000008 and seen["creationflags"] & 0x00000200  # DETACHED | NEW_GROUP
    else:
        assert seen["start_new_session"] is True  # a killpg on the gateway's group must not reach the watcher
    assert seen["stdin"] is subprocess.DEVNULL and seen["close_fds"] is True
