"""Windows process-tree termination that never follows a stale parent PID (#589).

Windows keeps a process's parent PID after the parent exits and later hands that PID to an unrelated process. A tree
collected by parent PID alone (`taskkill /T`, or a scan of `th32ParentProcessID`) then counts a long-lived process whose
launcher is gone, such as the detached gateway and runner that `labhq up` leaves behind, as a child of whatever
short-lived process now holds the old PID, and ends it together with that process.

Here a process is a child only when its parent PID matches and it was created no earlier than that parent. A process
older than its claimed parent is a PID-reuse orphan: it and its subtree are left alone. The validated set is ended
leaves first, one PID at a time, and each kill re-reads the creation time on the very handle it terminates, so a PID
that was reused after the snapshot is skipped.

POSIX callers keep process groups; nothing here runs off Windows (`_snapshot` returns None there).
"""
from __future__ import annotations

import functools
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

KILLED = "killed"
GONE = "gone"  # no such process, or it had already exited
REUSED = "reused"  # the PID now belongs to a process created at another time


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    created: int | None  # FILETIME ticks (100 ns since 1601-01-01); None when the process could not be opened


@dataclass
class KillResult:
    killed: list[int] = field(default_factory=list)  # in kill order: leaves first, the root last
    skipped: list[int] = field(default_factory=list)  # already gone, or the PID was reused: not touched
    failed: dict[int, str] = field(default_factory=dict)  # pid -> why TerminateProcess did not work


def parse_created(value: str | None) -> int | None:
    """The FILETIME ticks inside a PID record's `windows:<ticks>` identity; None for any other form."""
    if isinstance(value, str) and value.startswith("windows:"):
        try:
            return int(value[len("windows:"):])
        except ValueError:
            return None
    return None


def validated_descendants(root_pid: int, procs: Iterable[Proc]) -> list[Proc]:
    """Descendants of `root_pid` in discovery order, so every parent comes before its children.

    A child is kept only when its creation time is known and not earlier than its parent's. The root's own creation
    time must be known too, or nothing can be validated and the answer is empty."""
    table = list(procs)
    root = next((p for p in table if p.pid == root_pid), None)
    if root is None or root.created is None:
        return []
    children: dict[int, list[Proc]] = {}
    for proc in table:
        if proc.ppid != proc.pid:
            children.setdefault(proc.ppid, []).append(proc)
    found: list[Proc] = []
    seen = {root.pid}
    pending = deque([root])
    while pending:
        parent = pending.popleft()
        for child in children.get(parent.pid, ()):
            if child.pid in seen or child.created is None or child.created < parent.created:
                continue  # a PID-reuse orphan (older than its claimed parent) or one that cannot be checked
            seen.add(child.pid)
            found.append(child)
            pending.append(child)
    return found


def child_pids(pid: int, *, created: int | None = None) -> list[int]:
    """Direct children of `pid` that are really its children. Empty off Windows, when the table cannot be read, or
    when `created` is given and `pid` is now another process."""
    procs = _snapshot()
    if procs is None:
        return []
    root = next((p for p in procs if p.pid == pid), None)
    if root is None or (created is not None and root.created != created):
        return []
    return [p.pid for p in validated_descendants(pid, procs) if p.ppid == pid]


def kill_tree(pid: int, *, created: int | None = None, spare_root_on_failure: bool = False) -> KillResult:
    """End `pid` and its validated descendants, leaves first, without `taskkill /T`.

    `created` is the identity the caller recorded for `pid` (FILETIME ticks): when the PID now belongs to a process
    created at another time, nothing is touched. Without it the caller owns a handle to `pid` (a Popen child), so the
    PID cannot have been reused. A process table that cannot be read ends only `pid` itself.

    With `spare_root_on_failure`, `pid` is left running when a descendant could not be ended: a caller that keeps a
    record of `pid` (`down`) then still finds it alive and can retry, instead of losing the way to the survivor."""
    result = KillResult()
    procs = _snapshot()
    victims: list[tuple[int, int | None]]
    if procs is None:
        victims = [(pid, created)]
    else:
        root = next((p for p in procs if p.pid == pid), None)
        if root is None or (created is not None and root.created != created):
            result.skipped.append(pid)
            return result
        victims = [(p.pid, p.created) for p in reversed(validated_descendants(pid, procs))]
        victims.append((pid, root.created if root.created is not None else created))
    for victim, identity in victims:
        if spare_root_on_failure and victim == pid and result.failed:
            break  # the root is the last victim
        try:
            outcome = _terminate(victim, identity)
        except OSError as exc:
            result.failed[victim] = str(exc) or f"PID {victim}을 종료하지 못함"
            continue
        (result.killed if outcome == KILLED else result.skipped).append(victim)
    return result


# --- Windows primitives; the tests replace these two with a fake process table -------------------------------------

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


@functools.lru_cache(maxsize=1)
def _kernel():
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FileTime)] * 4
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    return kernel, FileTime, ProcessEntry


def _creation_ticks(kernel, file_time, handle) -> int | None:
    import ctypes

    created, exited, kernel_time, user_time = file_time(), file_time(), file_time(), file_time()
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    return (created.high << 32) | created.low


def _snapshot() -> list[Proc] | None:
    """Every process with its creation time (None for ones this user cannot open); None off Windows or on failure."""
    if os.name != "nt":
        return None
    import ctypes

    kernel, file_time, entry_type = _kernel()
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    rows: list[tuple[int, int]] = []
    entry = entry_type()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    procs = []
    for pid, ppid in rows:
        created = None
        handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                created = _creation_ticks(kernel, file_time, handle)
            finally:
                kernel.CloseHandle(handle)
        procs.append(Proc(pid, ppid, created))
    return procs


def _terminate(pid: int, created: int | None) -> str:
    """TerminateProcess on `pid`, after reading its creation time from the handle that will terminate it.

    Returns KILLED, GONE or REUSED; raises OSError when the process is there and cannot be ended (or cannot be
    identified while an identity was asked for)."""
    if os.name != "nt":
        raise OSError("Windows 전용")
    import ctypes
    from ctypes import wintypes

    kernel, file_time, _entry = _kernel()
    handle = kernel.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:  # no such PID
            return GONE
        raise ctypes.WinError(error)
    try:
        code = wintypes.DWORD()

        def active() -> bool:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == _STILL_ACTIVE

        if not active():
            return GONE
        if created is not None:
            current = _creation_ticks(kernel, file_time, handle)
            if current is None:
                raise OSError(f"PID {pid}의 생성 시각을 읽을 수 없어 종료하지 않음")
            if current != created:
                return REUSED
        if not kernel.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if not active():  # it ended on its own between the check and the kill
                return GONE
            raise ctypes.WinError(error)
        return KILLED
    finally:
        kernel.CloseHandle(handle)
