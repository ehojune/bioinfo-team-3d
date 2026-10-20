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

from labhq import windows_launch as win

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

DAEMON = r'''
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
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(.02)
    raise AssertionError("owned launcher produced no outcome")


@contextmanager
def owned_launch(tmp_path, *, allow_breakaway):
    dll = kernel()
    job = dll.CreateJobObjectW(None, None)  # Unnamed, non-inheritable; only this test owns its lifetime.
    assert job, C.get_last_error()
    case = SimpleNamespace(dll=dll, job=job, parent=None, child_handle=None)
    (tmp_path / "daemon.py").write_text(DAEMON, encoding="utf-8")
    script = tmp_path / "parent.py"
    script.write_text(PARENT, encoding="utf-8")
    env = dict(os.environ)  # CI installs labhq editable; preserve all guard/PYTHONPATH variables intact.
    # Do not modify/remove any guard variables. This file never runs in the local guarded session.
    try:
        limits = win.ExtendedLimits()
        limits.basic.flags = win.KILL_ON_CLOSE | (win.BREAKAWAY_OK if allow_breakaway else 0)
        assert dll.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), C.get_last_error()
        with (tmp_path / "parent.log").open("wb") as log:
            case.parent = subprocess.Popen([sys.executable, str(script), str(tmp_path)], env=env,
                                           stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, close_fds=True)
        # The parent waits for go: no descendant can start before assignment to our owned Job.
        assert dll.AssignProcessToJobObject(job, int(case.parent._handle)), C.get_last_error()
        (tmp_path / "go").touch()
        case.result = wait_result(tmp_path / "result.json")
        assert case.result["parent_job"]["in_job"] is True
        assert case.result["parent_job"]["kill_on_close"] is True
        assert case.result["parent_job"]["breakaway_ok"] is allow_breakaway
        case.pid_file = tmp_path / "state/labhq-gateway-supervisor.pid"
        case.events = [json.loads(line) for line in (tmp_path / "state/logs/detached-launch.jsonl").read_text().splitlines()]
        yield case
    finally:
        (tmp_path / "stop").touch()  # Normal exit for the fake daemon; never a real labhq role.
        if not case.child_handle and (tmp_path / "ready").exists():
            # Also reap a child if an assertion/timeout interrupted the parent before its result was read.
            try:
                case.child_handle = hold_recorded_child(dll, json.loads((tmp_path / "ready").read_text()))
            except (OSError, ValueError):
                pass
        try:
            if case.child_handle:
                if dll.WaitForSingleObject(case.child_handle, 5000) == 258:
                    assert dll.TerminateProcess(case.child_handle, 1)  # Retained owned handle, never a PID scan.
                assert dll.WaitForSingleObject(case.child_handle, 5000) == 0
        finally:
            if case.child_handle:
                assert dll.CloseHandle(case.child_handle)
            if case.job:
                assert dll.CloseHandle(case.job)
            if case.parent:
                if case.parent.poll() is None:
                    case.parent.kill()  # Only the Popen owned by this test, including failed assignment.
                case.parent.wait(timeout=10)


def close_parent_job(case):
    assert case.dll.CloseHandle(case.job)
    case.job = None  # Last owned Job handle: the assigned parent must exit, but the escaped child must not.
    case.parent.wait(timeout=10)


def test_allowed_breakaway_child_survives_parent_job_close(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=True) as case:
        # Denial/unknown is NOT evidence for survival. An incompatible outer host fails this positive test.
        assert case.result["success"], "UNVERIFIED positive survival: " + json.dumps(case.events)
        assert case.pid_file.is_file() and (tmp_path / "ready").is_file()
        case.child_handle = hold_recorded_child(case.dll, json.loads(case.pid_file.read_text()))
        assert case.child_handle, C.get_last_error()
        assert win.job_membership(case.child_handle) is False
        assert [e["status"] for e in case.events] == ["attempt", "job_verified", "recorded"]
        close_parent_job(case)
        assert case.dll.WaitForSingleObject(case.child_handle, 300) == 258
        with capsys.disabled():
            print("WIN795 POSITIVE: successful launch, parent Job closed, child survived; owned cleanup follows")


def test_denied_breakaway_records_no_pid_or_readiness(tmp_path, capsys):
    with owned_launch(tmp_path, allow_breakaway=False) as case:
        assert not case.result["success"]
        assert not case.pid_file.exists() and not (tmp_path / "ready").exists()
        assert [e["status"] for e in case.events] == ["attempt", "denied"]
        assert case.events[-1]["winerror"] == 5  # A missing/broken executable is not Job denial evidence.
        close_parent_job(case)
        with capsys.disabled():
            print("WIN795 DENIED: WinError 5, no PID/readiness; this does not establish positive survival")
