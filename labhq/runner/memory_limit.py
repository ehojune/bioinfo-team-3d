"""Per-step process-tree memory limits using only operating-system primitives."""
from __future__ import annotations

import functools
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GIB = 1024 ** 3
MEMORY_LIMIT_ENV = "LABHQ_STEP_MEMORY_LIMIT_BYTES"
_MEMORY_FAILURE = re.compile(
    r"memoryerror|arraymemoryerror|cannot allocate memory|out of memory|std::bad_alloc|"
    r"allocation failed|not enough memory",
    re.IGNORECASE,
)


def _physical_memory_bytes() -> int:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except (AttributeError, OSError, ValueError):
            pass
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0


def _cgroup_memory_bytes() -> int:
    for path in (Path("/sys/fs/cgroup/memory.max"),
                 Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            # Do not use Path.read_text here: callers that forbid task-artifact reads
            # monkeypatch that API, while this is runner host metadata.
            with open(path, encoding="ascii") as stream:
                value = stream.read().strip()
            if value != "max" and int(value) > 0:
                return int(value)
        except (OSError, UnicodeError, ValueError):
            continue
    return 0


def usable_memory_bytes() -> int:
    """Physical memory, narrowed to a cgroup ceiling when one is visible."""
    physical = _physical_memory_bytes()
    cgroup = _cgroup_memory_bytes() if os.name == "posix" else 0
    known = [value for value in (physical, cgroup) if value > 0]
    return min(known) if known else GIB  # conservative fallback for an unusual unsupported host


def default_step_memory_limit_gb() -> float:
    return usable_memory_bytes() * 0.60 / GIB


def configured_limit_bytes(runner_settings: Any) -> int:
    value = float(runner_settings.step_memory_limit_gb)
    return 0 if value <= 0 else int(value * GIB)


@functools.lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel, ExtendedLimitInformation


class _WindowsJob:
    _EXTENDED_LIMIT_INFORMATION = 9
    _JOB_MEMORY = 0x00000200
    _KILL_ON_CLOSE = 0x00002000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self, pid: int, limit_bytes: int):
        import ctypes

        kernel, info_type = _windows_api()
        self._kernel, self._info_type, self._handle = kernel, info_type, None
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        try:
            info = info_type()
            info.BasicLimitInformation.LimitFlags = self._JOB_MEMORY | self._KILL_ON_CLOSE
            info.JobMemoryLimit = limit_bytes
            if not kernel.SetInformationJobObject(
                    handle, self._EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            rights = self._PROCESS_TERMINATE | self._PROCESS_SET_QUOTA | self._PROCESS_QUERY_LIMITED_INFORMATION
            process = kernel.OpenProcess(rights, False, pid)
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not kernel.AssignProcessToJobObject(handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(process)
        except BaseException:
            self.close()
            raise

    def peak_bytes(self) -> int:
        import ctypes

        if not self._handle:
            return 0
        info = self._info_type()
        if not self._kernel.QueryInformationJobObject(
                self._handle, self._EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None):
            return 0
        return int(info.PeakJobMemoryUsed)

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


@dataclass
class StepMemoryGuard:
    limit_bytes: int
    platform: str | None = None
    _job: _WindowsJob | None = None

    def __post_init__(self) -> None:
        self.limit_bytes = max(0, int(self.limit_bytes))
        self.platform = self.platform or os.name

    @property
    def enabled(self) -> bool:
        return self.limit_bytes > 0

    def spawn_kwargs(self) -> dict[str, Any]:
        return {}

    def wrap_command(self, command: list[str]) -> list[str]:
        """Set RLIMIT_AS in a tiny exec wrapper; avoids preexec_fn in the runner's threaded process."""
        if not self.enabled or self.platform != "posix":
            return command
        return [sys.executable, "-m", __name__, "--exec", str(self.limit_bytes), "--", *command]

    def attach(self, pid: int) -> None:
        if self.enabled and self.platform == "nt":
            self._job = _WindowsJob(pid, self.limit_bytes)

    def exceeded(self, returncode: int | None, stderr: str) -> bool:
        if not self.enabled or returncode in (0, None):
            return False
        if _MEMORY_FAILURE.search(stderr):
            return True
        return bool(self._job and self._job.peak_bytes() >= int(self.limit_bytes * 0.98))

    @staticmethod
    def setup_failed(stderr: str) -> bool:
        return "LABHQ_MEMORY_LIMIT_SETUP:" in stderr

    @staticmethod
    def executable_missing(stderr: str) -> bool:
        return "LABHQ_EXEC_NOT_FOUND:" in stderr

    def close(self) -> None:
        if self._job:
            self._job.close()
            self._job = None


def step_memory_guard(limit_bytes: int) -> StepMemoryGuard:
    return StepMemoryGuard(limit_bytes)


def memory_limit_mechanism(platform: str | None = None) -> str:
    return "Windows Job object" if (platform or os.name) == "nt" else "POSIX RLIMIT_AS (ulimit -v equivalent)"


def memory_limit_supported(platform: str | None = None) -> bool:
    platform = platform or os.name
    try:
        if platform == "nt":
            _windows_api()
        else:
            import resource
            getattr(resource, "RLIMIT_AS")
        return True
    except (AttributeError, ImportError, OSError):
        return False


def _exec_with_limit(argv: list[str]) -> int:
    if len(argv) < 4 or argv[0] != "--exec" or argv[2] != "--":
        print("LABHQ_MEMORY_LIMIT_SETUP: invalid wrapper arguments", file=sys.stderr)
        return 126
    try:
        limit = int(argv[1])
        command = argv[3:]
        if limit <= 0 or not command:
            raise ValueError("missing positive limit or command")
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        target = min(limit, hard) if hard != resource.RLIM_INFINITY else limit
        resource.setrlimit(resource.RLIMIT_AS, (target, hard))
        os.execvpe(command[0], command, os.environ)
    except FileNotFoundError as exc:
        print(f"LABHQ_EXEC_NOT_FOUND: {exc}", file=sys.stderr)
        return 127
    except (ImportError, OSError, ValueError) as exc:
        print(f"LABHQ_MEMORY_LIMIT_SETUP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 126
    return 126


if __name__ == "__main__":  # pragma: no cover - exercised as the staff subprocess wrapper
    raise SystemExit(_exec_with_limit(sys.argv[1:]))
