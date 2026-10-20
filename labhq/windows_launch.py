"""Read-only Job diagnostics and verified detached launch; never retry without breakaway."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BREAKAWAY = 0x01000000
DETACHED = 0x00000208
CHECK_ENV = "LABHQ_DETACHED_JOB_CHECK"
KILL_ON_CLOSE = 0x2000
BREAKAWAY_OK = 0x0800
SILENT_BREAKAWAY_OK = 0x1000


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", C.c_uint32),
                ("min_working_set", C.c_size_t), ("max_working_set", C.c_size_t),
                ("active_processes", C.c_uint32), ("affinity", C.c_size_t),
                ("priority", C.c_uint32), ("scheduling", C.c_uint32)]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", C.c_uint64 * 6),
                ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                ("peak_process_memory", C.c_size_t), ("peak_job_memory", C.c_size_t)]


def _kernel():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = C.c_void_p
    kernel.IsProcessInJob.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]
    kernel.IsProcessInJob.restype = C.c_int
    kernel.QueryInformationJobObject.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.c_void_p]
    kernel.QueryInformationJobObject.restype = C.c_int
    return kernel


def job_membership(handle=None, *, diagnostics=None) -> bool | None:
    """NULL job tests membership in ANY job, including a remaining ancestor after partial breakaway."""
    facts = diagnostics if diagnostics is not None else {}
    try:
        kernel = _kernel()
        result = C.c_int()
        if kernel.IsProcessInJob(handle if handle is not None else kernel.GetCurrentProcess(),
                                 None, C.byref(result)):
            return bool(result.value)
        facts.update(query_error="IsProcessInJob", winerror=C.get_last_error())
    except (OSError, AttributeError, TypeError, ValueError, C.ArgumentError) as exc:
        facts.update(query_error=type(exc).__name__, winerror=getattr(exc, "winerror", None))
    return None


def current_job() -> dict:
    if sys.platform != "win32":
        return {"in_job": False, "scope": "not_windows", "limit_flags": None}
    diagnostics = {}
    member = job_membership(diagnostics=diagnostics)
    facts = {"in_job": member, "scope": "immediate_job_only", "limit_flags": None,
             "kill_on_close": None, "breakaway_ok": None, "silent_breakaway_ok": None,
             "membership_query": diagnostics}
    if member is True:
        try:
            limits = ExtendedLimits()
            kernel = _kernel()
            if kernel.QueryInformationJobObject(None, 9, C.byref(limits), C.sizeof(limits), None):
                flags = limits.basic.flags
                facts.update(limit_flags=flags, kill_on_close=bool(flags & KILL_ON_CLOSE),
                             breakaway_ok=bool(flags & BREAKAWAY_OK),
                             silent_breakaway_ok=bool(flags & SILENT_BREAKAWAY_OK))
            else:
                facts["limits_query"] = {"query_error": "QueryInformationJobObject", "winerror": C.get_last_error()}
        except (OSError, AttributeError, TypeError, ValueError, C.ArgumentError) as exc:
            facts["limits_query"] = {"query_error": type(exc).__name__, "winerror": getattr(exc, "winerror", None)}
    return facts


def child_job_membership(child, *, diagnostics=None) -> bool | None:
    handle = getattr(child, "_handle", None)
    if handle is None:
        if diagnostics is not None:
            diagnostics.update(query_error="missing_child_handle", winerror=None)
        return None
    return job_membership(handle, diagnostics=diagnostics)


class LaunchError(OSError):
    pass


def require_detached_job() -> None:
    """Child-side gate before a managed role can claim ownership or start its own children."""
    requested = os.environ.pop(CHECK_ENV, None) == "1"  # Do not leak the managed-entry gate to staff/foreground CLIs.
    if sys.platform == "win32" and requested and job_membership() is not False:
        raise LaunchError("Windows Job 탈출 확인 실패. 독립 콘솔에서 다시 실행하거나 Job breakaway 정책을 확인하세요.")


def outcome(state: Path, role: str, status: str, **facts) -> None:
    path = state / "logs" / "detached-launch.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "role": role, "status": status, **facts}) + "\n")


def spawn(command, *, state: Path, role: str, popen=None, **options):
    if sys.platform != "win32":
        return (popen or subprocess.Popen)(command, **options)
    parent = current_job()
    outcome(state, role, "attempt", parent_job=parent)
    try:
        child = (popen or subprocess.Popen)(command, **options)
    except OSError as exc:
        outcome(state, role, "denied" if isinstance(exc, PermissionError) else "spawn_failed",
                error=type(exc).__name__, winerror=getattr(exc, "winerror", None))
        raise LaunchError("Windows detached 실행 거부: Job breakaway가 허용된 독립 콘솔에서 실행하세요. "
                          f"winerror={getattr(exc, 'winerror', None)}; parent_job={json.dumps(parent)}. "
                          "분리 flag를 빼고 재시도하지 않습니다.") from exc
    diagnostics = {}
    member = child_job_membership(child, diagnostics=diagnostics)
    if member is not False:
        # The retained Popen handle owns this exact child; no PID/tree scan or unrelated process is touched.
        outcome(state, role, "unverified", child_in_job=member, child_job_query=diagnostics)
        try:
            child.kill()
            child.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            outcome(state, role, "cleanup_failed", error=type(exc).__name__)
            raise LaunchError("Windows 미확인 자식 정리 실패. detached-launch.jsonl을 확인하고 독립 콘솔에서 점검하세요.") from exc
        raise LaunchError("Windows 자식의 Job 탈출을 확인하지 못해 실행을 취소했습니다. "
                          f"child_in_job={member}; query={json.dumps(diagnostics)}; "
                          f"parent_job={json.dumps(parent)}. 독립 콘솔에서 실행하고 상위 Job 정책을 확인하세요.")
    outcome(state, role, "job_verified", child_pid=child.pid, child_in_job=False)
    return child
