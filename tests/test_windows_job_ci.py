"""Real owned Job/process integration: GitHub Windows CI ONLY. Never execute locally."""
import ctypes as C
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from labhq import windows_launch as win, windows_broker as broker

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


@contextmanager
def owned_launch(tmp_path, *, allow_breakaway, nested=False):
    dll = kernel()
    job = dll.CreateJobObjectW(None, None)  # Unnamed, non-inheritable; only this test owns its lifetime.
    assert job, C.get_last_error()
    case = SimpleNamespace(dll=dll, job=job, outer_job=None, parent=None, child_handle=None, role_handle=None)
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
        case.result = wait_result(tmp_path / "result.json")
        assert case.result["parent_job"]["in_job"] is True
        assert case.result["parent_job"]["kill_on_close"] is True
        assert case.result["parent_job"]["breakaway_ok"] is (allow_breakaway and not nested)
        assert case.result["parent_job"]["silent_breakaway_ok"] is nested
        case.pid_file = tmp_path / "state/labhq-gateway-supervisor.pid"
        case.events = [json.loads(line) for line in (tmp_path / "state/logs/detached-launch.jsonl").read_text().splitlines()]
        yield case
    finally:
        (tmp_path / "stop").touch()  # Normal exit for the fake daemon; never a real labhq role.
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
                    if dll.WaitForSingleObject(handle, 5000) == 258:
                        assert dll.TerminateProcess(handle, 1)  # Retained owned handle, never a PID scan.
                    assert dll.WaitForSingleObject(handle, 5000) == 0
        finally:
            if case.role_handle:
                assert dll.CloseHandle(case.role_handle)
            if case.child_handle:
                assert dll.CloseHandle(case.child_handle)
            if case.job:
                assert dll.CloseHandle(case.job)
            if case.outer_job:
                assert dll.CloseHandle(case.outer_job)
            if case.parent:
                if case.parent.poll() is None:
                    case.parent.kill()  # Only the Popen owned by this test, including failed assignment.
                case.parent.wait(timeout=10)


def close_parent_job(case):
    assert case.dll.CloseHandle(case.job)
    case.job = None  # Last owned Job handle: the assigned parent must exit, but the escaped child must not.
    if case.outer_job:
        assert case.dll.CloseHandle(case.outer_job)
        case.outer_job = None
    case.parent.wait(timeout=10)


def test_allowed_breakaway_child_survives_parent_job_close(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=True) as case:
        # Denial/unknown is NOT evidence for survival. An incompatible outer host fails this positive test.
        assert case.result["success"], "UNVERIFIED positive survival: " + json.dumps(case.events)
        assert case.pid_file.is_file() and (tmp_path / "ready").is_file()
        case.child_handle = hold_recorded_child(case.dll, json.loads(case.pid_file.read_text()))
        assert case.child_handle, C.get_last_error()
        assert win.job_membership(case.child_handle) is False
        statuses = [e["status"] for e in case.events]
        assert statuses[-1] == "recorded" and ("job_verified" in statuses or "wmi_verified" in statuses)
        assert statuses.count("wmi_attempt") <= 1
        close_parent_job(case)
        assert case.dll.WaitForSingleObject(case.child_handle, 300) == 258
        with capsys.disabled():
            print("WIN795 POSITIVE: successful launch, parent Job closed, child survived; owned cleanup follows")


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
        assert case.dll.WaitForSingleObject(case.child_handle, 300) == 258
        assert case.dll.WaitForSingleObject(case.role_handle, 300) == 258
        with capsys.disabled():
            print("WIN795 NESTED POSITIVE: WMI bootstrap/fake role survived both owned Job closes; cleanup follows")


@pytest.mark.parametrize("failure", ["unknown_job", "receipt_timeout"])
def test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack(tmp_path, monkeypatch, failure):
    # Actual WMI/bootstrap, with only the caller-side failure injected. The child never receives ACK.
    native = broker.Native()
    caller = native.identity(native.k.GetCurrentProcess())
    create, read = broker._create, broker._read
    observed = []
    def observe(command, cwd, environment):
        pid = create(command, cwd, environment)
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
        with (state / "log").open("a", encoding="utf-8") as output:
            with pytest.raises(win.LaunchError):
                broker.launch([caller["image"], str(tmp_path / "daemon.py"), str(tmp_path)], state=state,
                              role="gateway-supervisor", cwd=str(tmp_path), env=dict(os.environ), stdout=output)
        assert len(observed) == 1
        observed[0].wait(timeout=5)  # Owned cleanup or bounded bootstrap self-abort; no descendants before ACK.
        assert not (tmp_path / "ready").exists() and not (tmp_path / "fake-role.json").exists()
        assert not list(state.glob("*.pid"))
    finally:
        for process in observed:
            try:
                process.kill()  # Proven nonce + held creation/image/current-user identity; never an unknown PID.
                process.wait(timeout=5)
            finally:
                process.close()
