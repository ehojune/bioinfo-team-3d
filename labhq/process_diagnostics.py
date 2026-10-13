"""Small, process-local death breadcrumbs; never log config or exception messages."""
from __future__ import annotations

import atexit
import asyncio
import faulthandler
import json
import os
from pathlib import Path
import signal
import sys
import threading
import traceback
from datetime import datetime, timezone


def _resources() -> dict:
    result = {"rss_bytes": None, "handles": None}
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage")]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            handle = kernel.GetCurrentProcess()
            count, memory = wintypes.DWORD(), Counters()
            memory.cb = ctypes.sizeof(memory)
            if kernel.GetProcessHandleCount(handle, ctypes.byref(count)):
                result["handles"] = count.value
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                result["rss_bytes"] = memory.WorkingSetSize
        elif Path("/proc/self/statm").exists():
            result["rss_bytes"] = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass  # Unsupported counters stay explicitly null.
    return result


class ProcessDiagnostics:
    def __init__(self, logs: Path, role: str, *, interval: float = 600):
        self.logs, self.role, self.interval = logs, role, interval
        self.pid = os.getpid()
        self.reason, self.code = "returned", 0
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._exited = False
        self._signals = {}
        self._loops = []

    def write(self, event: str, **fields) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(), "pid": self.pid,
                  "role": self.role, "event": event, **fields}
        try:
            with self._lock:
                self._output.write(json.dumps(record, ensure_ascii=True) + "\n")
                self._output.flush()
        except (OSError, ValueError):
            # Diagnostic disk failure must not become the cause of daemon death.
            pass

    def heartbeat(self) -> None:
        self.write("heartbeat", **_resources())

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.heartbeat()

    def _exception(self, typ, value, tb) -> None:
        self.reason, self.code = f"exception:{typ.__name__}", 1
        self.write("uncaught_exception", reason=self.reason, code=self.code)
        traceback.print_tb(tb, file=self._fault)
        self._fault.flush()
        self._previous_hook(typ, value, tb)

    def attach_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if any(saved is loop for saved, _ in self._loops):
            return
        previous = loop.get_exception_handler()

        def handler(current, context):
            exception = context.get("exception")
            self.write("asyncio_exception", reason=type(exception).__name__ if exception else "unhandled_context",
                       code=None)
            if previous:
                previous(current, context)
            else:
                current.default_exception_handler(context)

        loop.set_exception_handler(handler)
        self._loops.append((loop, previous))

    def note_signal(self, sig: int) -> None:
        self.reason, self.code = f"signal:{signal.Signals(sig).name}", 128 + sig
        self.write("signal", reason=self.reason, code=self.code)

    def _signal(self, sig, frame) -> None:
        self.note_signal(sig)
        previous = self._signals[sig]
        if callable(previous):
            previous(sig, frame)
        elif previous == signal.SIG_DFL:
            raise SystemExit(128 + sig)

    def _exit(self) -> None:
        with self._lock:
            if not self._exited:
                self.write("exit", reason=self.reason, code=self.code)
                self._exited = True

    def __enter__(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        self._output = (self.logs / f"{self.role}-diagnostics.jsonl").open("a", encoding="utf-8")
        self._fault = (self.logs / f"{self.role}-fault.log").open("a", encoding="utf-8")
        self._fault_enabled = faulthandler.is_enabled()
        faulthandler.enable(self._fault, all_threads=True)
        self._previous_hook = sys.excepthook
        sys.excepthook = self._exception
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None and threading.current_thread() is threading.main_thread():
                previous = signal.getsignal(sig)
                if previous != signal.SIG_IGN:
                    self._signals[sig] = previous
                    signal.signal(sig, self._signal)
        atexit.register(self._exit)
        self.write("start", ppid=os.getppid())
        self.heartbeat()
        self._thread = threading.Thread(target=self._heartbeat_loop, name="labhq-diagnostics", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, typ, value, tb):
        if typ is not None:
            if issubclass(typ, SystemExit):
                self.code = value.code if isinstance(value.code, int) else (0 if value.code is None else 1)
                if not self.reason.startswith("signal:"):
                    self.reason = "SystemExit"
            elif issubclass(typ, KeyboardInterrupt):
                self.reason, self.code = "signal:SIGINT", 130
            else:
                self.reason, self.code = f"exception:{typ.__name__}", 1
                self.write("uncaught_exception", reason=self.reason, code=self.code)
                traceback.print_tb(tb, file=self._fault)
                self._fault.flush()
        self._stop.set()
        self._thread.join(timeout=1)
        self._exit()
        atexit.unregister(self._exit)
        sys.excepthook = self._previous_hook
        for sig, previous in self._signals.items():
            signal.signal(sig, previous)
        for loop, previous in self._loops:
            if not loop.is_closed():
                loop.set_exception_handler(previous)
        faulthandler.disable()
        if self._fault_enabled:
            faulthandler.enable(sys.__stderr__)  # Capture streams may not have a file descriptor.
        self._fault.close()
        self._output.close()


def latest_records(logs: Path, role: str, pid: int) -> dict:
    """Read a bounded tail and ignore earlier processes, including previous starts of a reused PID."""
    latest = {}
    try:
        with (logs / f"{role}-diagnostics.jsonl").open("rb") as source:
            size = source.seek(0, 2)
            source.seek(max(0, size - 65536))
            lines = source.read().splitlines()
        for line in lines:
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict) or row.get("pid") != pid:
                continue
            event = row.get("event")
            if event == "start":
                latest.clear()
            if event in {"start", "heartbeat", "exit"}:
                latest[event] = row
    except OSError:
        pass
    return latest
