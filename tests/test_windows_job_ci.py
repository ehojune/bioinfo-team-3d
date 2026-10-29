"""Real owned Job/process integration: GitHub Windows CI ONLY. Never execute locally."""
import ctypes as C
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from labhq import windows_launch as win, windows_broker as broker
from tests.test_web_shutdown_process import _launch_journal

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true",
    reason="WIN795 real subprocess verification is authorized only in isolated GitHub Windows CI")

PARENT = r'''
import json, sys, time
from pathlib import Path
from labhq import cli, windows_launch as win
from labhq.settings import Settings
root = Path(sys.argv[1])
deadline = time.monotonic() + 30
while not (root / "go").exists():
    if time.monotonic() > deadline:
        raise SystemExit(3)
    time.sleep(.01)
settings = Settings.model_validate({"gateway": {"state_dir": str(root / "state")},
                                    "runner": {"state_dir": str(root / "state")}})
cli._supervisor_launch = lambda s, role: ([sys.executable, str(root / "daemon.py"), str(root)], cli._launch_options(s))
def await_ready(*a, **kw):
    deadline = time.monotonic() + 10
    while not (root / "ready").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("fake daemon did not become ready")
        time.sleep(.01)
cli._await_child_record = await_ready
if (root / "deny-broker").exists():
    from labhq import windows_broker
    def deny_provider(*a, **kw):
        raise win.LaunchError("fixture: WMI provider denied")
    windows_broker._create = deny_provider
facts = {"parent_job": win.current_job()}
try:
    facts["pid"] = cli._start_background(settings, "gateway")
    facts["success"] = True
except (SystemExit, OSError):
    facts["success"] = False
(root / "result.tmp").write_text(json.dumps(facts), encoding="utf-8")
(root / "result.tmp").replace(root / "result.json")
while True:
    time.sleep(.1)
'''

ROLE = r'''
import sys, time
from pathlib import Path
from labhq.windows_launch import require_detached_job
from labhq.cli import _process_created
import json, os
require_detached_job()
root = Path(sys.argv[1])
(root / "ready.tmp").write_text(json.dumps({"pid": os.getpid(), "created": _process_created(os.getpid())}), encoding="utf-8")
(root / "ready.tmp").replace(root / "ready")
deadline = time.monotonic() + 60
while not (root / "stop").exists() and time.monotonic() < deadline:
    time.sleep(.02)
'''

DAEMON = r'''
import json, sys, os
from pathlib import Path
from labhq import cli, windows_launch as win
from labhq.settings import Settings
win.require_detached_job()
root = Path(sys.argv[1])
settings = Settings.model_validate({"gateway": {"state_dir": str(root / "state")},
                                    "runner": {"state_dir": str(root / "state")}})
(root / "fake-supervisor.tmp").write_text(json.dumps({"pid": os.getpid(),
    "created": cli._process_created(os.getpid())}), encoding="utf-8")
(root / "fake-supervisor.tmp").replace(root / "fake-supervisor.json")
with (root / "state/fake-role.log").open("a", encoding="utf-8") as output:
    child = win.spawn([sys.executable, str(root / "role.py"), str(root)], state=root / "state",
                      role="gateway", stdout=output, **cli._launch_options(settings))
    try:
        record = {"pid": child.pid, "created": cli._process_created(child.pid)}
        (root / "fake-role.tmp").write_text(json.dumps(record), encoding="utf-8")
        (root / "fake-role.tmp").replace(root / "fake-role.json")
        child.wait(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
'''


def kernel():
    dll = win._kernel()
    dll.CreateJobObjectW.argtypes = [C.c_void_p, C.c_wchar_p]
    dll.CreateJobObjectW.restype = C.c_void_p
    dll.SetInformationJobObject.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32]
    dll.SetInformationJobObject.restype = C.c_int
    dll.AssignProcessToJobObject.argtypes = [C.c_void_p, C.c_void_p]
    dll.AssignProcessToJobObject.restype = C.c_int
    dll.CloseHandle.argtypes = [C.c_void_p]
    dll.CloseHandle.restype = C.c_int
    dll.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    dll.OpenProcess.restype = C.c_void_p
    dll.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    dll.WaitForSingleObject.restype = C.c_uint32
    dll.TerminateProcess.argtypes = [C.c_void_p, C.c_uint32]
    dll.TerminateProcess.restype = C.c_int
    dll.GetProcessTimes.argtypes = [C.c_void_p] * 5
    dll.GetProcessTimes.restype = C.c_int
    return dll


def hold_recorded_child(dll, record):
    handle = dll.OpenProcess(0x1000 | 0x100000 | 1, False, record["pid"])
    if not handle:
        return None  # The owned child may already have exited; never act on an unverified PID.
    times = [C.c_uint64() for _ in range(4)]
    if not dll.GetProcessTimes(handle, *(C.byref(t) for t in times)):
        dll.CloseHandle(handle)
        raise OSError("owned child identity unavailable")
    if record["created"] != f"windows:{times[0].value}":
        dll.CloseHandle(handle)
        return None  # Reused PID: the recorded child is gone, and this handle must never be killed.
    return handle


def wait_result(path):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(.02)
    raise AssertionError("owned launcher produced no outcome")


def _owned_launch_diagnostics(root, parent):
    """Never dump parent.log: only the existing journal projection and a bounded exitcode."""
    try:
        code = parent.poll() if parent is not None else "unavailable"
        if code is None:
            code = "running"
        elif type(code) is not int or not -2**32 <= code <= 2**32:
            code = "unavailable"
    except Exception:
        code = "unavailable"
    try:
        journal = _launch_journal(root / "state/logs/detached-launch.jsonl")
    except Exception:
        journal = "(journal unavailable)"
    diagnostic = json.dumps({"launcher_exitcode": code, "journal": journal})
    if len(diagnostic) > 4096:
        return json.dumps({"launcher_exitcode": code, "journal": "(journal projection exceeds bound)"})
    return diagnostic


class _NativeTrace:
    """Optional CI diagnostics only; never a success certificate or a cleanup gate."""
    phases = {"case_enter", "body_enter", "body_return", "body_positive", "trace_full"} | {
        name + suffix for name in ("wait_result", "stop", "wait", "terminate", "close", "poll", "parent_wait", "parent_kill", "process_wait")
        for suffix in ("_enter", "_return", "_error")}

    def __init__(self, kind="positive"):
        self.path, self.rows, self.full = None, [], False
        try:
            value = os.environ.get("CI_SUITE_ARTIFACT_DIR")
            if not value:
                return  # No filesystem, clock or UUID work outside the explicit diagnostic context.
            if kind not in ("positive", "denied", "nested", "unknown_job", "receipt_timeout"):
                return
            root = Path(value)
            if not root.is_absolute() or os.path.normcase(str(root.resolve(strict=True))) != os.path.normcase(str(root.absolute())):
                return
            directory = root / "critical"
            if os.path.normcase(str(directory.resolve())) != os.path.normcase(str(directory.absolute())):
                return
            directory.mkdir(exist_ok=True)
            identifier = uuid.uuid4().hex
            if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
                return
            self.case_id = kind + "-" + identifier
            path = directory / ("native-case-" + self.case_id + ".json")
            with path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"case_id": self.case_id, "events": []}))
            self.path, self.directory, self.started = path, directory, time.monotonic()
            self.emit("case_enter")
        except BaseException:
            self.path = None  # Diagnostic failures must never interrupt owned-resource cleanup.

    def emit(self, phase, *, role=None, wait_ms=None, result=None, signature_ok=None, call_ms=None):
        pending, created = None, False
        try:
            if self.path is None or self.full or phase not in self.phases:
                return
            if self.path.parent != self.directory or self.path.name != "native-case-" + self.case_id + ".json":
                return
            elapsed = (time.monotonic() - self.started) * 1000
            row = {"phase": phase, "clock_ok": math.isfinite(elapsed) and 0 <= elapsed <= 600000}
            if row["clock_ok"]:
                row["elapsed_ms"] = round(elapsed, 3)
            if role in ("role", "child", "parent", "job", "outer_job"):
                row["role"] = role
            if type(wait_ms) is int and wait_ms in (300, 5000, 10000, 45000):
                row["wait_ms"] = wait_ms
            if result in ("none", "other") or type(result) is int and result in (0, 1, 258, 0xFFFFFFFF):
                row["result"] = result
            if type(signature_ok) is bool:
                row["signature_ok"] = signature_ok
            if type(call_ms) in (int, float) and math.isfinite(call_ms) and 0 <= call_ms <= 600000:
                row["call_ms"] = round(call_ms, 3)
            if len(self.rows) == 63:
                row["phase"], self.full = "trace_full", True
            self.rows.append(row)
            text = json.dumps({"case_id": self.case_id, "events": self.rows}, allow_nan=False)
            if len(text) > 16384:
                self.rows.pop()
                self.full = True
                return
            # Decline redirected parents/targets. Names cannot collide with trusted CI records.
            if os.path.normcase(str(self.path.resolve())) != os.path.normcase(str(self.path.absolute())):
                return
            pending = self.path.with_name(self.path.stem + "-pending.json")
            with pending.open("x", encoding="utf-8") as stream:
                created = True
                stream.write(text)
            pending.replace(self.path)  # Close before publishing; keep the last complete JSON on interruption.
        except BaseException:
            pass
        finally:
            if created:
                try:
                    pending.unlink(missing_ok=True)
                except BaseException:
                    pass


def _trace_event(case, phase):
    try:
        trace = getattr(case, "trace", None)
        if trace is not None:
            trace.emit(phase)
    except BaseException:
        pass


def _trace_call(trace, phase, function, *args, role=None, wait_ms=None, **kwargs):
    try:
        enabled = trace is not None and trace.path is not None
    except BaseException:
        enabled = False
    if not enabled:
        return function(*args, **kwargs)
    def record(suffix, result=None, started=None):
        try:
            duration = (time.monotonic() - started) * 1000 if started is not None else None
            signature = (getattr(function, "argtypes", None) == [C.c_void_p, C.c_uint32]
                         and getattr(function, "restype", None) is C.c_uint32) if phase == "wait" else None
            trace.emit(phase + suffix, role=role, wait_ms=wait_ms, result=result, signature_ok=signature, call_ms=duration)
        except BaseException:
            pass
    record("_enter")
    try:
        started = time.monotonic()  # Exclude the preceding diagnostic write from the callable duration.
    except BaseException:
        started = None
    try:
        result = function(*args, **kwargs)
    except BaseException:
        record("_error", started=started)
        raise
    record("_return", result if type(result) is int and result in (0, 1, 258, 0xFFFFFFFF) else "none" if result is None else "other", started)
    return result


def _provider_summary(events):
    """Launch stages for a verdict: the direct attempt, then the WMI provider phases (ms since its start) and total."""
    parts = []
    for event in events:
        provider = event.get("provider") or {}
        phases = ",".join(f"{p['phase']}@{p['elapsed_ms']:.0f}" for p in provider.get("phases", []))
        detail = f"{event.get('phase')}/{event.get('reason')}: " if event["status"] == "wmi_failed" else ""
        parts.append(event["status"] + (f"[{detail}{phases}; total_ms={provider.get('elapsed_ms')}]" if provider else ""))
    return " -> ".join(parts)


@contextmanager
def owned_launch(tmp_path, *, allow_breakaway, nested=False):
    dll = kernel()
    job = dll.CreateJobObjectW(None, None)  # Unnamed, non-inheritable; only this test owns its lifetime.
    assert job, C.get_last_error()
    case = SimpleNamespace(dll=dll, job=job, outer_job=None, parent=None, child_handle=None, role_handle=None)
    case.trace = _NativeTrace("nested" if nested else "positive" if allow_breakaway else "denied")
    (tmp_path / "daemon.py").write_text(DAEMON, encoding="utf-8")
    (tmp_path / "role.py").write_text(ROLE, encoding="utf-8")
    script = tmp_path / "parent.py"
    script.write_text(PARENT, encoding="utf-8")
    env = dict(os.environ)  # CI installs labhq editable; preserve all guard/PYTHONPATH variables intact.
    # Do not modify/remove any guard variables. This file never runs in the local guarded session.
    try:
        limits = win.ExtendedLimits()
        limits.basic.flags = win.KILL_ON_CLOSE | (win.SILENT_BREAKAWAY_OK if nested else win.BREAKAWAY_OK if allow_breakaway else 0)
        assert dll.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), C.get_last_error()
        if nested:
            case.outer_job = dll.CreateJobObjectW(None, None)
            assert case.outer_job, C.get_last_error()
            outer_limits = win.ExtendedLimits()
            outer_limits.basic.flags = win.KILL_ON_CLOSE
            assert dll.SetInformationJobObject(case.outer_job, 9, C.byref(outer_limits), C.sizeof(outer_limits)), C.get_last_error()
        elif not allow_breakaway:
            (tmp_path / "deny-broker").touch()
        with (tmp_path / "parent.log").open("wb") as log:
            case.parent = subprocess.Popen([sys.executable, str(script), str(tmp_path)], env=env,
                                           stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, close_fds=True)
        # The parent waits for go: no descendant can start before assignment to our owned Job.
        if case.outer_job:
            assert dll.AssignProcessToJobObject(case.outer_job, int(case.parent._handle)), C.get_last_error()
        assert dll.AssignProcessToJobObject(job, int(case.parent._handle)), C.get_last_error()
        (tmp_path / "go").touch()
        case.result = _trace_call(case.trace, "wait_result", wait_result, tmp_path / "result.json", wait_ms=45000)
        assert case.result["parent_job"]["in_job"] is True
        assert case.result["parent_job"]["kill_on_close"] is True
        assert case.result["parent_job"]["breakaway_ok"] is (allow_breakaway and not nested)
        assert case.result["parent_job"]["silent_breakaway_ok"] is nested
        case.pid_file = tmp_path / "state/labhq-gateway-supervisor.pid"
        case.events = [json.loads(line) for line in (tmp_path / "state/logs/detached-launch.jsonl").read_text().splitlines()]
        _trace_event(case, "body_enter")
        yield case
        _trace_event(case, "body_return")
    except (Exception, SystemExit):
        try:
            print("\n--- owned launch ---\n" + _owned_launch_diagnostics(tmp_path, case.parent), file=sys.stderr)
        except Exception:
            pass  # Diagnostic failure must not replace the original assertion/exception.
        raise
    finally:
        _trace_call(case.trace, "stop", (tmp_path / "stop").touch)  # Normal exit for the fake daemon.
        for attribute, names in (("role_handle", ("fake-role.json", "ready")),
                                 ("child_handle", ("state/labhq-gateway-supervisor.pid", "fake-supervisor.json"))):
            if not getattr(case, attribute):
                for name in names:
                    if (tmp_path / name).exists():
                        try:
                            setattr(case, attribute, hold_recorded_child(dll, json.loads((tmp_path / name).read_text())))
                        except (OSError, ValueError):
                            pass
                        if getattr(case, attribute):
                            break
        try:
            for handle in (case.role_handle, case.child_handle):
                if handle:
                    role = "role" if handle is case.role_handle else "child"
                    if _trace_call(case.trace, "wait", dll.WaitForSingleObject, handle, 5000, role=role, wait_ms=5000) == 258:
                        assert _trace_call(case.trace, "terminate", dll.TerminateProcess, handle, 1, role=role)
                    assert _trace_call(case.trace, "wait", dll.WaitForSingleObject, handle, 5000, role=role, wait_ms=5000) == 0
        finally:
            if case.role_handle:
                assert _trace_call(case.trace, "close", dll.CloseHandle, case.role_handle, role="role")
            if case.child_handle:
                assert _trace_call(case.trace, "close", dll.CloseHandle, case.child_handle, role="child")
            if case.job:
                assert _trace_call(case.trace, "close", dll.CloseHandle, case.job, role="job")
            if case.outer_job:
                assert _trace_call(case.trace, "close", dll.CloseHandle, case.outer_job, role="outer_job")
            if case.parent:
                if _trace_call(case.trace, "poll", case.parent.poll, role="parent") is None:
                    _trace_call(case.trace, "parent_kill", case.parent.kill, role="parent")
                _trace_call(case.trace, "parent_wait", case.parent.wait, timeout=10, role="parent", wait_ms=10000)


def close_parent_job(case):
    trace = getattr(case, "trace", None)
    assert _trace_call(trace, "close", case.dll.CloseHandle, case.job, role="job")
    case.job = None  # Last owned Job handle: the assigned parent must exit, but the escaped child must not.
    if case.outer_job:
        assert _trace_call(trace, "close", case.dll.CloseHandle, case.outer_job, role="outer_job")
        case.outer_job = None
    _trace_call(trace, "parent_wait", case.parent.wait, timeout=10, role="parent", wait_ms=10000)


def test_allowed_breakaway_child_survives_parent_job_close(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=True) as case:
        # Denial/unknown is NOT evidence for survival. An incompatible outer host fails this positive test.
        assert case.result["success"], ("UNVERIFIED positive survival: " + _provider_summary(case.events) + " :: "
                                        + json.dumps(case.events))
        assert case.pid_file.is_file() and (tmp_path / "ready").is_file()
        case.child_handle = hold_recorded_child(case.dll, json.loads(case.pid_file.read_text()))
        assert case.child_handle, C.get_last_error()
        assert win.job_membership(case.child_handle) is False
        statuses = [e["status"] for e in case.events]
        assert statuses[-1] == "recorded" and ("job_verified" in statuses or "wmi_verified" in statuses)
        assert statuses.count("wmi_attempt") <= 1
        close_parent_job(case)
        assert _trace_call(getattr(case, "trace", None), "wait", case.dll.WaitForSingleObject, case.child_handle, 300, role="child", wait_ms=300) == 258
        _trace_event(case, "body_positive")
        with capsys.disabled():
            print("WIN795 POSITIVE: successful launch, parent Job closed, child survived; owned cleanup follows")
            print("WIN1029 launch stages: " + _provider_summary(case.events))


def test_denied_direct_and_mock_denied_provider_record_no_pid_or_readiness(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=False) as case:
        assert not case.result["success"]
        assert not case.pid_file.exists() and not (tmp_path / "ready").exists()
        assert [e["status"] for e in case.events] == ["attempt", "denied", "wmi_attempt", "wmi_failed"]
        assert case.events[1]["winerror"] == 5  # A missing/broken executable is not Job denial evidence.
        close_parent_job(case)
        with capsys.disabled():
            print("WIN795 DENIED: real direct WinError 5 + mocked provider denial, no PID/readiness; not survival proof")


def test_wmi_escapes_owned_outer_nonbreakaway_inner_silent_job(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=False, nested=True) as case:
        assert case.result["success"], "UNVERIFIED nested WMI survival: " + json.dumps(case.events)
        statuses = [event["status"] for event in case.events]
        assert statuses.count("wmi_attempt") == 1 and "wmi_verified" in statuses
        assert statuses[-1] == "recorded"
        if "unverified" in statuses:
            assert statuses.index("direct_reaped") < statuses.index("wmi_attempt")
        case.child_handle = hold_recorded_child(case.dll, json.loads(case.pid_file.read_text()))
        assert case.child_handle and win.job_membership(case.child_handle) is False
        case.role_handle = hold_recorded_child(case.dll, json.loads((tmp_path / "fake-role.json").read_text()))
        assert case.role_handle and win.job_membership(case.role_handle) is False
        assert (tmp_path / "ready").is_file()
        close_parent_job(case)
        assert _trace_call(case.trace, "wait", case.dll.WaitForSingleObject, case.child_handle, 300, role="child", wait_ms=300) == 258
        assert _trace_call(case.trace, "wait", case.dll.WaitForSingleObject, case.role_handle, 300, role="role", wait_ms=300) == 258
        _trace_event(case, "body_positive")
        with capsys.disabled():
            print("WIN795 NESTED POSITIVE: WMI bootstrap/fake role survived both owned Job closes; cleanup follows")


@pytest.mark.parametrize("failure", ["unknown_job", "receipt_timeout"])
def test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack(tmp_path, monkeypatch, failure):
    # Actual WMI/bootstrap, with only the caller-side failure injected. The child never receives ACK.
    trace = _NativeTrace(failure)
    native = broker.Native()
    caller = native.identity(native.k.GetCurrentProcess())
    create, read = broker._create, broker._read
    observed = []
    def observe(command, cwd, environment, *, diagnostics=None):
        pid = create(command, cwd, environment, diagnostics=diagnostics)
        handle = native.open_process(pid)
        identity = native.identity(handle)
        control = Path(command[-1])
        request = read(control)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            receipt = read(control.parent / "receipt.json")
            if receipt:
                break
            time.sleep(.01)
        else:
            native.k.CloseHandle(handle)  # Unknown: never terminate a bare returned PID.
            raise AssertionError("UNVERIFIED WMI bootstrap receipt; no ACK sent")
        assert receipt == {"nonce": request["nonce"], "pid": pid, "created": identity["created"]}
        assert identity["image"] == caller["image"] and identity["owner"] == caller["owner"]
        observed.append(broker.Process(native, pid, handle, identity["created"]))
        return pid
    monkeypatch.setattr(broker, "_create", observe)
    monkeypatch.setattr(broker, "TIMEOUT", 10)
    if failure == "unknown_job":
        monkeypatch.setattr(broker, "job_membership", lambda *a, **kw: None)
    else:
        monkeypatch.setattr(broker, "_read", lambda path: None if path.name == "receipt.json" else read(path))
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "daemon.py").write_text(DAEMON, encoding="utf-8")
    (tmp_path / "role.py").write_text(ROLE, encoding="utf-8")
    try:
        trace.emit("body_enter")
        with (state / "log").open("a", encoding="utf-8") as output:
            with pytest.raises(win.LaunchError):
                broker.launch([caller["image"], str(tmp_path / "daemon.py"), str(tmp_path)], state=state,
                              role="gateway-supervisor", cwd=str(tmp_path), env=dict(os.environ), stdout=output)
        assert len(observed) == 1
        _trace_call(trace, "process_wait", observed[0].wait, timeout=5, role="child", wait_ms=5000)
        assert not (tmp_path / "ready").exists() and not (tmp_path / "fake-role.json").exists()
        assert not list(state.glob("*.pid"))
        trace.emit("body_positive")
    finally:
        for process in observed:
            try:
                _trace_call(trace, "terminate", process.kill, role="child")  # Proven retained identity only.
                _trace_call(trace, "process_wait", process.wait, timeout=5, role="child", wait_ms=5000)
            finally:
                _trace_call(trace, "close", process.close, role="child")
