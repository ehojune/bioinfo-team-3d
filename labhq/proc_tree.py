"""Windows process-tree termination that never follows a stale parent PID (#589).

Windows keeps a process's parent PID after the parent exits and later hands that PID to an unrelated process. A tree
collected by parent PID alone (the Windows tree-kill command, or a scan of `th32ParentProcessID`) then counts a
long-lived process whose launcher is gone, such as the detached gateway and runner that `labhq up` leaves behind, as a
child of whatever short-lived process now holds the old PID, and ends it together with that process.

Windows runner images can also attach the system `conhost.exe` to a detached console process. That helper rejects the
terminate right but exits with its console client, so it is identified by its full system path and not treated as an
unreachable user process.

Here a process is a child only when its parent PID matches and it was created no earlier than that parent. A process
older than its claimed parent is a PID-reuse orphan: it and its subtree are left alone.

A PID can also change hands between the process table being read and the process being opened. So every candidate is
opened first and the handles are held until the kill decision is made: an open handle pins its process object, so
its PID cannot be reused. A second table read, taken once all handles are open, must still show the parent PID the
first read showed; a candidate whose PID now belongs to another process fails that test. The creation time used for
the validation and the kill itself both come from the held handle, and a PID is never reopened to be killed.

The validated set is ended leaves first. When one process cannot be ended, `spare_ancestors_on_failure` leaves its whole
chain of ancestors running, so the survivor stays reachable through live parents for a retry.

`pin_ancestors` is the other half. Code outside labhq (a test, a tool) may still collect a tree by parent PID alone, and
nothing here can change that. What can be changed is whether the stale PID is ever handed out: a process holds a
handle on each of its nearest ancestors, so even after they exit their PIDs stay reserved and no unrelated process
can be given one.

POSIX callers keep process groups; nothing here runs off Windows (`_table` returns None there).
"""
from __future__ import annotations

import functools
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

KILLED = "killed"
GONE = "gone"  # the process had already exited
_PROCESS_EXIT_WAIT_MS = 10_000


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    created: int | None  # FILETIME ticks (100 ns since 1601-01-01); None when the process could not be opened


@dataclass
class KillResult:
    killed: list[int] = field(default_factory=list)  # in kill order: leaves first, the root last
    skipped: list[int] = field(default_factory=list)  # already gone, or the root's PID now belongs to another process
    failed: dict[int, str] = field(default_factory=dict)  # pid -> why it could not be opened or ended
    spared: list[int] = field(default_factory=list)  # left running on purpose: ancestors of a process that failed


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
    when `created` is given and `pid` is now another process. Read-only: it never decides a kill."""
    procs = _snapshot()
    if procs is None:
        return []
    root = next((p for p in procs if p.pid == pid), None)
    if root is None or (created is not None and root.created != created):
        return []
    return [p.pid for p in validated_descendants(pid, procs) if p.ppid == pid]


def _reachable(root_pid: int, rows: list[tuple[int, int]]) -> list[int]:
    """Every PID the parent PIDs reach from `root_pid`, valid or not: the candidates to open and then validate."""
    children: dict[int, list[int]] = {}
    for pid, ppid in rows:
        if ppid != pid:
            children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    seen = {root_pid}
    pending = deque([root_pid])
    while pending:
        for child in children.get(pending.popleft(), ()):
            if child not in seen:
                seen.add(child)
                found.append(child)
                pending.append(child)
    return found


def kill_tree(pid: int, *, created: int | None = None, spare_ancestors_on_failure: bool = False) -> KillResult:
    """End `pid` and its validated descendants, leaves first, never by parent PID alone.

    `created` is the identity the caller recorded for `pid` (FILETIME ticks): when the PID now belongs to a process
    created at another time, nothing is touched. Without it the caller owns a handle to `pid` (a Popen child), so the
    PID cannot have been reused. A process table that cannot be read twice ends only `pid` itself. A candidate that
    cannot be opened (another user's, protected) cannot be checked, so it is never touched; if its parent PID still
    names `pid` or a validated descendant it may be a survivor, and it is recorded as failed like one that refuses to
    die. A candidate that is gone by then is not.

    With `spare_ancestors_on_failure`, a process that could not be ended keeps its whole ancestor chain up to `pid`
    running, so a caller that keeps a record of `pid` (`down`) still finds the survivor through live parents on a
    retry. Processes that are not ancestors of a failure are still ended."""
    result = KillResult()
    held: dict[int, _Handle] = {}
    readonly: dict[int, _Handle] = {}
    try:
        _end_tree(pid, created, spare_ancestors_on_failure, result, held, readonly)
    finally:
        for handle in [*held.values(), *readonly.values()]:
            handle.close()
    return result


def _end_tree(pid: int, created: int | None, spare: bool, result: KillResult, held: dict[int, _Handle],
              readonly: dict[int, _Handle]) -> None:
    first = _table()
    candidates = _reachable(pid, first) if first is not None else []
    try:
        root = _open(pid)
    except OSError as exc:
        result.failed[pid] = str(exc) or f"PID {pid}을 열 수 없음"
        return
    if root is None:
        result.skipped.append(pid)
        return
    held[pid] = root
    if created is not None and root.created != created:
        result.skipped.append(pid)  # the PID now belongs to a process created at another time
        return
    unopened: dict[int, str] = {}
    for candidate in candidates:  # pin every candidate before anything is decided
        try:
            handle = _open(candidate)
        except OSError as exc:
            reason = str(exc) or f"PID {candidate}을 열 수 없음"
            try:
                query_handle = _open(candidate, terminate=False)
            except OSError:
                query_handle = None
            if query_handle is not None:
                readonly[candidate] = query_handle
                if _is_system_console_host(query_handle):
                    continue  # Windows owns it; it exits when the console client below is ended.
            unopened[candidate] = reason
            continue
        if handle is not None:
            held[candidate] = handle
    second = _table() if candidates else None
    procs = [Proc(pid, 0, root.created)]
    before, after = (dict(first), dict(second)) if second is not None else ({}, {})
    if second is not None:
        for candidate, handle in held.items():
            # still the process the first read described: its PID shows the same parent PID now
            if candidate != pid and candidate in after and after[candidate] == before.get(candidate):
                procs.append(Proc(candidate, after[candidate], handle.created))
    validated = validated_descendants(pid, procs)
    parent_of = {p.pid: p.ppid for p in validated}
    spared: set[int] = set()

    def failed(victim: int, reason: str) -> None:
        result.failed[victim] = reason
        if spare:
            ancestor = parent_of.get(victim)
            while ancestor is not None:
                spared.add(ancestor)
                ancestor = parent_of.get(ancestor)  # the root has no entry: the chain ends there

    ours = {pid, *parent_of}
    created_of = {pid: root.created, **{p.pid: p.created for p in validated}}
    for candidate, reason in unopened.items():
        # Unchecked, so never touched. Still a child of the root or of a validated descendant, with the parent PID it
        # had in both reads: it may be a survivor. Gone, or hanging off something that is not ours: not a failure.
        if candidate in after and after[candidate] == before.get(candidate) and after[candidate] in ours:
            query = readonly.get(candidate)
            if query is not None and query.created < created_of[after[candidate]]:
                # Queryable, so its age is known from the held handle: older than its claimed parent, it is a PID-reuse
                # orphan like the ones `validated_descendants` drops (a boot-time system process under a reused PID,
                # PR #800 Windows CI), not a survivor of this tree.
                continue
            parent_of[candidate] = after[candidate]
            failed(candidate, reason)
    for victim in [p.pid for p in reversed(validated)] + [pid]:
        if victim in spared:
            result.spared.append(victim)
            continue
        try:
            outcome = held[victim].terminate()
        except OSError as exc:
            failed(victim, str(exc) or f"PID {victim}을 종료하지 못함")
            continue
        (result.killed if outcome == KILLED else result.skipped).append(victim)


@dataclass
class PinResult:
    """Ancestors this process holds open. Closing them (or exiting) lets their PIDs be reused again."""

    pinned: list[int] = field(default_factory=list)  # nearest ancestor first
    unpinned: dict[int, str] = field(default_factory=dict)  # PID -> why it could not be held; the walk stops there
    handles: list = field(default_factory=list, repr=False)

    def close(self) -> None:
        while self.handles:
            self.handles.pop().close()


def pin_ancestors(*, depth: int = 4, pid: int | None = None) -> PinResult:
    """Hold a handle on each of `pid`'s (default: this process's) nearest `depth` ancestors while the result stays open.

    A parent that has exited still names its PID in its children's parent field. While a handle on it is open Windows
    keeps the process object, so that PID cannot be given to another process, and a tree collected by parent PID from
    that number can never start at a stranger. The walk stops at the first ancestor that cannot be held: already gone
    (nothing left to hold), unreadable, or younger than its child, which means its PID was reused before this ran. The
    result lists what was held and why the walk stopped. An ancestor that cannot be held is reported there rather than
    raised, since losing this protection must not stop the caller. Empty off Windows."""
    result = PinResult()
    rows = _table()
    if rows is None:
        return result
    parent_of = dict(rows)
    pid = os.getpid() if pid is None else pid
    try:
        own = _open(pid, terminate=False)
    except OSError as exc:
        result.unpinned[pid] = str(exc) or f"PID {pid}을 열 수 없음"
        return result
    if own is None:
        return result
    child_pid, child_created = pid, own.created
    own.close()
    for _ in range(depth):
        ppid = parent_of.get(child_pid)
        if not ppid or ppid == child_pid:
            break
        try:
            handle = _open(ppid, terminate=False)
        except OSError as exc:
            result.unpinned[ppid] = str(exc) or f"PID {ppid}을 열 수 없음"
            break
        if handle is None:
            result.unpinned[ppid] = "이미 종료되어 붙들 수 없음"
            break
        if handle.created > child_created:
            handle.close()
            result.unpinned[ppid] = "자식보다 늦게 만들어짐: PID가 이미 다른 프로세스에 재사용됨"
            break
        result.handles.append(handle)
        result.pinned.append(ppid)
        child_pid, child_created = ppid, handle.created
    return result


# --- Windows primitives; the tests replace `_table`, `_snapshot` and `_open` with a fake process table -------------

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
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
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FileTime)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
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


def _image_path(kernel, handle) -> str | None:
    """Full executable path available with PROCESS_QUERY_LIMITED_INFORMATION, or None when Windows refuses it."""
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(32_768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    return buffer.value


def _is_system_console_host(handle: "_Handle") -> bool:
    """The Windows-owned console host, not a user child that must remain reachable after an Access denied."""
    path = (getattr(handle, "image_path", None) or "").replace("/", "\\").casefold()
    system_root = (os.environ.get("SystemRoot") or r"C:\Windows").rstrip("\\/").replace("/", "\\").casefold()
    return path == system_root + r"\system32\conhost.exe"


def _table() -> list[tuple[int, int]] | None:
    """(PID, parent PID) of every process; None off Windows or when the snapshot cannot be taken."""
    if os.name != "nt":
        return None
    import ctypes

    kernel, _file_time, entry_type = _kernel()
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
    return rows


def _snapshot() -> list[Proc] | None:
    """Every process with its creation time (None for ones this user cannot open); None off Windows or on failure.
    Read-only: nothing is pinned, so this never decides a kill."""
    rows = _table()
    if rows is None:
        return None
    kernel, file_time, _entry = _kernel()
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


class _Handle:
    """An open handle to one process. It pins the process object, so the PID cannot be reused while it is open, and
    both the creation time and the kill come from it."""

    def __init__(self, pid: int, created: int, raw, image_path: str | None = None) -> None:
        self.pid, self.created, self._raw, self.image_path = pid, created, raw, image_path

    def terminate(self) -> str:
        """KILLED, or GONE when it had already exited; OSError when it is there and cannot be ended."""
        import ctypes

        kernel, _file_time, _entry = _kernel()

        def running() -> bool:
            return kernel.WaitForSingleObject(self._raw, 0) != 0  # signaled (0) means it has exited

        if not running():
            return GONE
        if not kernel.TerminateProcess(self._raw, 1):
            error = ctypes.get_last_error()
            # A Windows venv launcher ends by itself after its interpreter child. During that transition
            # TerminateProcess can say access denied before the process handle becomes signaled. Waiting on the
            # already-pinned handle distinguishes that normal exit from a process that really refused to end.
            if kernel.WaitForSingleObject(self._raw, _PROCESS_EXIT_WAIT_MS) == 0:
                return GONE
            raise ctypes.WinError(error)
        waited = kernel.WaitForSingleObject(self._raw, _PROCESS_EXIT_WAIT_MS)
        if waited == 0:  # WAIT_OBJECT_0: the process and its loaded modules are actually gone
            return KILLED
        if waited == 0x00000102:  # WAIT_TIMEOUT
            raise OSError(f"PID {self.pid}이 {_PROCESS_EXIT_WAIT_MS // 1000}초 안에 끝나지 않음")
        raise OSError(f"PID {self.pid} 종료 확인 실패(WaitForSingleObject={waited})")

    def close(self) -> None:
        if self._raw:
            _kernel()[0].CloseHandle(self._raw)
            self._raw = None


def _open(pid: int, *, terminate: bool = True) -> _Handle | None:
    """Pin `pid`: a handle that can query and end it (query only with `terminate=False`, which is all that holding a
    PID needs and works on more processes). None when there is no such process; OSError when it cannot be opened or
    its creation time cannot be read (then it cannot be checked, so it is not touched)."""
    if os.name != "nt":
        raise OSError("Windows 전용")
    import ctypes

    kernel, file_time, _entry = _kernel()
    rights = _PROCESS_QUERY_LIMITED_INFORMATION
    if terminate:
        rights |= _PROCESS_TERMINATE | _SYNCHRONIZE
    raw = kernel.OpenProcess(rights, False, pid)
    if not raw:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:  # no such PID
            return None
        raise ctypes.WinError(error)
    created = _creation_ticks(kernel, file_time, raw)
    if created is None:
        kernel.CloseHandle(raw)
        raise OSError(f"PID {pid}의 생성 시각을 읽을 수 없어 확인하지 못함")
    return _Handle(pid, created, raw, _image_path(kernel, raw))
