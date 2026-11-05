"""Fail-closed process boundary for explicit doctor canaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import yaml


class BoundaryError(Exception):
    pass


class WindowsJob:
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes
        from .runner.memory_limit import _windows_api

        self.ctypes = ctypes
        self.kernel, info_type = _windows_api()
        k = self.kernel
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k.IsProcessInJob.restype = wintypes.BOOL
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise BoundaryError("Job creation failed")
        try:
            info = info_type()
            info.BasicLimitInformation.LimitFlags = 0x2000  # kill on close; no breakaway
            if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise BoundaryError("Job configuration failed")
            if not k.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise BoundaryError("Job assignment failed")
            member = wintypes.BOOL()
            if not k.IsProcessInJob(int(process._handle), self.handle, ctypes.byref(member)) or not member.value:
                raise BoundaryError("Job membership not confirmed")
        except BaseException:
            self.close()
            raise

    def active(self):
        return _active_processes(self.kernel, self.handle)

    def cleanup(self, timeout=5):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise BoundaryError("Job termination failed")
        deadline = time.monotonic() + timeout
        while self.active():
            if time.monotonic() >= deadline:
                raise BoundaryError("Job cleanup timed out")
            time.sleep(0.05)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _active_processes(kernel, handle):
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                        ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

        info = Accounting()
        if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise BoundaryError("Job accounting failed")
        return info.ActiveProcesses


def _require_no_children():
    from .runner.memory_limit import _windows_api
    kernel, _ = _windows_api()
    if _active_processes(kernel, None) != 1:
        raise BoundaryError("previous canary left processes in the Job")


PER_AGENT_DEADLINE_S = 390


def _selected_count(settings, selector):
    if selector != "all":
        return 1
    from . import doctor
    try:
        return max(1, len(doctor._roster(settings)))
    except (OSError, ValueError, yaml.YAMLError):
        return 1


def run(settings, selector, timeout=None):
    if os.name != "nt":
        raise BoundaryError("canary containment unsupported on this platform")
    if timeout is None:
        timeout = PER_AGENT_DEADLINE_S * _selected_count(settings, selector)
    process = subprocess.Popen([sys.executable, "-m", "labhq.canary_process"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    job = None
    output = None
    try:
        job = WindowsJob(process)
        payload = json.dumps({"settings": settings.model_dump(mode="json"), "selector": selector}).encode("utf-8")
        try:
            output, _ = process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise BoundaryError("canary deadline exceeded") from exc
        if process.returncode != 0:
            raise BoundaryError("canary bootstrap failed")
        try:
            result = json.loads(output)
            if not isinstance(result, list):
                raise ValueError("invalid result")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BoundaryError("canary bootstrap returned invalid results") from exc
    finally:
        try:
            if job is not None:
                job.cleanup()
            elif process.poll() is None:
                process.kill()
        finally:
            if job is not None:
                job.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise BoundaryError("bootstrap cleanup timed out") from exc
    return result


def _main():
    # No provider code is imported or executed until the parent releases this barrier.
    payload = json.load(sys.stdin.buffer)
    from . import doctor
    from .canary_process import _require_no_children
    from .settings import Settings
    import asyncio

    doctor._canary_between_calls = _require_no_children
    rows = asyncio.run(doctor._run_canaries(Settings.model_validate(payload["settings"]), payload["selector"]))
    sys.stdout.buffer.write(json.dumps(rows).encode("utf-8"))


if __name__ == "__main__":
    _main()
