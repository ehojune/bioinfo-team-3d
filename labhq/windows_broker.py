"""One-shot local WMI bootstrap. No service, persistent environment, or PID-only cleanup."""
from __future__ import annotations

import base64
import ctypes as C
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import secrets
import subprocess
import sys
import tempfile
import time

# Executed as a file by the base interpreter, before site/PYTHONPATH imports or descendants.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from labhq.windows_launch import BREAKAWAY, DETACHED, LaunchError, job_membership, outcome

TIMEOUT = 20
# The request expires TIMEOUT s after it is written (the bootstrap checks it), and the PowerShell provider that starts the
# bootstrap runs inside that window. The provider therefore needs a budget of its own that leaves the bootstrap
# BOOTSTRAP_MARGIN s to start. It was a fixed 10 s: a cold GitHub runner spent 4 s before the request header was read and
# then did not finish create within the rest (#1029, provider_timeout at 10 234 ms). 15 s is +50% over that observation,
# not a measured bound; the CI positive test prints the real phase times so the next change has data.
BOOTSTRAP_MARGIN = 5
PROVIDER_TIMEOUT = TIMEOUT - BOOTSTRAP_MARGIN
# Independent request budget, not the 8192-byte stdout bound or a Windows OS limit.
# Necessary raw-character preflight also bounds temporary JSON escaping allocations.
MAX_REQUEST_BYTES = 1024 * 1024


class BrokerFailure(LaunchError):
    def __init__(self, reason, code=None):
        self.reason, self.code = reason, code
        super().__init__(f"Windows WMI bootstrap: {reason}; code={code}")


WMI_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$clock = [Diagnostics.Stopwatch]::StartNew()
function Write-Phase([string]$phase) {
  [Console]::Out.WriteLine((@{phase=$phase; elapsed_ms=$clock.Elapsed.TotalMilliseconds} | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
}
try {
  [Console]::Out.WriteLine('{"phase":"powershell_started","elapsed_ms":0}')
  [Console]::Out.Flush()
  $stream = [Console]::OpenStandardInput()
  Write-Phase 'stdin_open'
  $reader = [IO.BinaryReader]::new($stream)
  $length = $reader.ReadUInt32()
  if ($length -lt 1 -or $length -gt __MAX_REQUEST_BYTES__) { throw 'request length rejected' }
  Write-Phase 'header_read'
  [byte[]]$payload = $reader.ReadBytes([int]$length)
  if ($payload.Length -ne $length) { throw 'request truncated' }
  Write-Phase 'input_read'
  $requestJson = [Text.UTF8Encoding]::new($false, $true).GetString($payload)
  $r = $requestJson | ConvertFrom-Json
  if ($null -eq $r -or $r -isnot [pscustomobject]) { throw 'request shape rejected' }
  if (@($r.PSObject.Properties).Count -ne 4 -or
      $r.command -isnot [string] -or [string]::IsNullOrEmpty($r.command) -or $r.command.IndexOf([char]0) -ge 0 -or
      $r.cwd -isnot [string] -or [string]::IsNullOrEmpty($r.cwd) -or $r.cwd.IndexOf([char]0) -ge 0 -or
      ($r.flags -isnot [int] -and $r.flags -isnot [long]) -or $r.flags -ne __REQUEST_FLAGS__ -or
      $r.environment -isnot [array]) { throw 'request shape rejected' }
  foreach ($entry in $r.environment) {
    if ($entry -isnot [string] -or $entry.IndexOf('=') -lt 1 -or $entry.IndexOf([char]0) -ge 0) {
      throw 'request environment rejected'
    }
  }
  Write-Phase 'input_accepted'
  $startup = ([wmiclass]'\\.\root\cimv2:Win32_ProcessStartup').CreateInstance()
  $startup.CreateFlags = [uint32]$r.flags
  $startup.ShowWindow = 0
  $startup.EnvironmentVariables = [string[]]$r.environment
  $process = [wmiclass]'\\.\root\cimv2:Win32_Process'
  Write-Phase 'create_entered'
  $p = $process.Create($r.command, $r.cwd, $startup)
  Write-Phase 'create_returned'
  [Console]::Out.Write((@{code=[uint32]$p.ReturnValue; pid=[uint32]$p.ProcessId} | ConvertTo-Json -Compress))
} catch { [Console]::Out.Write('{"code":-1,"pid":0}'); exit 1 }
'''.replace("__MAX_REQUEST_BYTES__", str(MAX_REQUEST_BYTES)).replace("__REQUEST_FLAGS__", str(BREAKAWAY | DETACHED | 0x400))


def _bind(dll, name, args, result=C.c_int):
    function = getattr(dll, name)
    function.argtypes, function.restype = args, result


class Native:
    """All identity and cleanup operations use the retained, non-inheritable process handle."""
    def __init__(self):
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        for name, args, result in (
            ("GetCurrentProcess", [], C.c_void_p),
            ("OpenProcess", [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p),
            ("CloseHandle", [C.c_void_p], C.c_int),
            ("GetProcessTimes", [C.c_void_p] * 5, C.c_int),
            ("GetExitCodeProcess", [C.c_void_p, C.POINTER(C.c_uint32)], C.c_int),
            ("QueryFullProcessImageNameW", [C.c_void_p, C.c_uint32, C.c_wchar_p, C.POINTER(C.c_uint32)], C.c_int),
            ("WaitForSingleObject", [C.c_void_p, C.c_uint32], C.c_uint32),
            ("TerminateProcess", [C.c_void_p, C.c_uint32], C.c_int),
            ("LocalFree", [C.c_void_p], C.c_void_p)):
            _bind(self.k, name, args, result)
        for name, args in (
            ("OpenProcessToken", [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]),
            ("GetTokenInformation", [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)]),
            ("ConvertSidToStringSidW", [C.c_void_p, C.POINTER(C.c_void_p)]),
            ("ConvertStringSecurityDescriptorToSecurityDescriptorW", [C.c_wchar_p, C.c_uint32, C.POINTER(C.c_void_p), C.c_void_p]),
            ("SetFileSecurityW", [C.c_wchar_p, C.c_uint32, C.c_void_p])):
            _bind(self.a, name, args)

    @staticmethod
    def require(ok):
        if not ok:
            raise OSError(C.get_last_error(), "Windows broker native operation failed")

    def identity(self, handle):
        times = [C.c_uint64() for _ in range(4)]
        self.require(self.k.GetProcessTimes(handle, *(C.byref(t) for t in times)))
        image, size = C.create_unicode_buffer(32768), C.c_uint32(32768)
        self.require(self.k.QueryFullProcessImageNameW(handle, 0, image, C.byref(size)))
        token, length = C.c_void_p(), C.c_uint32()
        self.require(self.a.OpenProcessToken(handle, 8, C.byref(token)))
        try:
            self.a.GetTokenInformation(token, 1, None, 0, C.byref(length))
            self.require(0 < length.value <= 65536)
            data = C.create_string_buffer(length.value)
            self.require(self.a.GetTokenInformation(token, 1, data, length.value, C.byref(length)))
            sid = C.c_void_p()
            self.require(self.a.ConvertSidToStringSidW(C.c_void_p.from_buffer(data), C.byref(sid)))
            try:
                owner = C.wstring_at(sid)
            finally:
                self.k.LocalFree(sid)
        finally:
            self.k.CloseHandle(token)
        return {"created": f"windows:{times[0].value}", "image": os.path.normcase(os.path.realpath(image.value)), "owner": owner}

    def private(self, folder, owner):
        descriptor = C.c_void_p()
        self.require(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;OICI;FA;;;{owner})", 1, C.byref(descriptor), None))
        try:
            self.require(self.a.SetFileSecurityW(str(folder), 0x80000004, descriptor))
        finally:
            self.k.LocalFree(descriptor)

    def open_process(self, pid, *, terminate=True):
        handle = self.k.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
        self.require(handle)
        return handle

    def alive(self, handle):
        result = self.k.WaitForSingleObject(handle, 0)
        if result not in (0, 258):
            self.require(False)
        return result == 258


class Process:
    def __init__(self, native, pid, handle, created):
        self.native, self.pid, self._handle, self.created = native, pid, handle, created

    def kill(self):
        if self.native.alive(self._handle):
            self.native.require(self.native.k.TerminateProcess(self._handle, 1))

    terminate = kill

    def wait(self, timeout=None):
        result = self.native.k.WaitForSingleObject(self._handle, int((timeout or TIMEOUT) * 1000))
        if result == 258:
            raise subprocess.TimeoutExpired("owned WMI bootstrap", timeout)
        self.native.require(result == 0)
        code = C.c_uint32()
        self.native.require(self.native.k.GetExitCodeProcess(self._handle, C.byref(code)))
        return code.value

    def poll(self):
        return None if self.native.alive(self._handle) else self.wait(timeout=0)

    def close(self):
        if self._handle:
            self.native.k.CloseHandle(self._handle)
            self._handle = None
        if getattr(self, "folder", None):
            try:
                _cleanup(self.folder)
            except OSError:
                pass  # No environment/credentials on disk; the bootstrap also removes its bounded control files.

    def __del__(self):
        self.close()  # Release only; never kill on finalization.


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _read(path):
    try:
        if path.stat().st_size > 131072:
            raise LaunchError("WMI bootstrap record exceeds its bound")
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


# ERROR_SHARING_VIOLATION. Python opens files without FILE_SHARE_DELETE, so reading a record while another handle with
# DELETE access is open on it (the writer's atomic replace, a file scanner) can fail briefly (#844). The code is not
# always visible: open() goes through the C runtime (CPython 3.12 Modules/_io/fileio.c: _wopen, then
# PyErr_SetFromErrnoWithFilenameObject), which keeps only errno EACCES and no winerror, the same shape as an ACL denial.
_SHARING_VIOLATION = 32


def _poll(path, counts):
    """`_read` inside a deadline-bounded wait: a record held elsewhere right now is not there yet. Those reads are
    counted in the caller's `counts["denied_reads"]`, so a denial that never clears still shows when the wait ends.
    Only a sharing violation or a code-less EACCES is read again; an explicit other winerror stops at once."""
    try:
        return _read(path)
    except PermissionError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror == _SHARING_VIOLATION or (winerror is None and exc.errno == errno.EACCES):
            counts["denied_reads"] += 1
            return None
        raise


def _digest(environment):
    # CPython consumes this interpreter-selection carrier during startup.
    pairs = sorted((key.upper(), value) for key, value in environment.items() if key.upper() != "__PYVENV_LAUNCHER__")
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=True).encode()).hexdigest()


def _cleanup(folder):
    for name in ("request", "receipt", "ack", "accepted"):
        for suffix in (".json", ".tmp"):
            (folder / (name + suffix)).unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def _powershell():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    _bind(kernel, "GetSystemDirectoryW", [C.c_wchar_p, C.c_uint32], C.c_uint32)
    buffer = C.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise BrokerFailure("system_directory_unknown")
    return Path(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe"


_PROVIDER_PHASES = ("powershell_started", "stdin_open", "header_read", "input_read", "input_accepted", "create_entered", "create_returned")


def _provider_output(output, diagnostics, *, complete):
    """Keep only bounded, ordered fixed phases; partial stdout never establishes a returned PID. The stdout itself is
    described only by kind and byte count, which tells no output at all from output without a valid phase."""
    phases = diagnostics["phases"] = []
    diagnostics["trace_status"] = "absent"
    diagnostics["stdout"] = "bytes" if isinstance(output, bytes) else "missing" if output is None else "invalid_type"
    if isinstance(output, bytes):
        diagnostics["stdout_bytes"] = len(output)
    try:
        if output is None and not complete:
            return None
        if not isinstance(output, bytes) or len(output) > 8192:
            raise ValueError
        lines = output.decode("utf-8-sig").splitlines()
        if len(lines) > len(_PROVIDER_PHASES) + 1:
            raise ValueError
        reply = None
        for index, line in enumerate(lines):
            row = json.loads(line)
            if isinstance(row, dict) and "phase" in row:
                elapsed = row.get("elapsed_ms")
                if (len(phases) >= len(_PROVIDER_PHASES) or row["phase"] != _PROVIDER_PHASES[len(phases)]
                        or set(row) != {"phase", "elapsed_ms"} or type(elapsed) not in (int, float)
                        or not 0 <= elapsed <= 60000 or not math.isfinite(elapsed)
                        or (phases and elapsed < phases[-1]["elapsed_ms"])):
                    raise ValueError
                phases.append({"phase": row["phase"], "elapsed_ms": elapsed})
            elif index == len(lines) - 1 and isinstance(row, dict):
                reply = row
            else:
                raise ValueError
        if complete and reply is None:
            raise ValueError
        diagnostics["trace_status"] = "phases_complete" if len(phases) == len(_PROVIDER_PHASES) else "partial" if phases else "absent"
        return reply if complete else None
    except (ValueError, UnicodeError):
        diagnostics["trace_status"] = "invalid_or_partial"
        if complete:
            raise ValueError("WMI provider protocol invalid") from None
        return None


def _request_frame(data):
    # Every input string is internal construction; preflight before JSON's escaping copies.
    raw_chars = len(data["command"]) + len(data["cwd"]) + sum(map(len, data["environment"]))
    if raw_chars > MAX_REQUEST_BYTES:
        raise BrokerFailure("request_too_large")
    payload = json.dumps(data, ensure_ascii=True).encode("utf-8")
    if not 0 < len(payload) <= MAX_REQUEST_BYTES:
        raise BrokerFailure("request_too_large")
    return len(payload).to_bytes(4, "little") + payload


def _create(command, cwd, environment, *, diagnostics=None):
    shell = _powershell()
    if not shell.is_absolute() or not shell.is_file():
        raise LaunchError("WMI broker requires the system Windows PowerShell executable")
    data = {"command": subprocess.list2cmdline(command), "cwd": str(cwd),
            "flags": BREAKAWAY | DETACHED | 0x400,
            "environment": [f"{key}={value}" for key, value in environment.items()]}
    encoded = base64.b64encode(WMI_SCRIPT.encode("utf-16-le")).decode("ascii")
    diagnostics = {} if diagnostics is None else diagnostics
    started = time.monotonic()
    try:
        try:
            result = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                    input=_request_frame(data), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=PROVIDER_TIMEOUT, creationflags=0x08000000, check=False)
        except subprocess.TimeoutExpired as exc:
            _provider_output(exc.output, diagnostics, complete=False)
            raise  # Create may have succeeded. Never retry or use a partial returned PID.
        diagnostics["returncode"] = result.returncode if type(result.returncode) is int else None
        reply = _provider_output(result.stdout, diagnostics, complete=True)
    finally:
        diagnostics["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    code = reply.get("code")
    if result.returncode or type(code) is not int or code != 0:
        raise BrokerFailure("create_failed", code if type(code) is int else None)
    pid = reply.get("pid")
    if type(pid) is not int or pid <= 0:
        raise BrokerFailure("invalid_returned_pid")
    return pid


def launch(command, *, state, role, **options):
    """At most one provider request. Unknown processes are never terminated by their returned PID."""
    native = Native()
    caller = native.identity(native.k.GetCurrentProcess())
    cwd = Path(options["cwd"])
    if not cwd.is_absolute() or not Path(command[0]).is_absolute():
        raise LaunchError("WMI requires absolute interpreter and cwd")
    if (len(command) < 2 or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or (command[1] == "-m" and len(command) < 3)
            or (command[1] != "-m" and not Path(command[1]).is_absolute())):
        raise LaunchError("WMI requires a structured Python module or absolute script entry")
    # Bypass the CPython venv redirector: it creates another process and its own Job.
    if os.path.normcase(os.path.realpath(command[0])) != caller["image"]:
        raise LaunchError("WMI interpreter does not match the caller's actual process image")
    log = Path(options["stdout"].name).resolve()
    if not log.is_relative_to(Path(state).resolve()):
        raise LaunchError("WMI output must belong to the instance state")
    environment = dict(options["env"])
    if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
           or not isinstance(value, str) or "\0" in value for key, value in environment.items()):
        raise LaunchError("WMI environment cannot be represented safely")
    nonce = secrets.token_hex(32)
    root = Path(state) / ".launch"
    root.mkdir(parents=True, exist_ok=True)
    process, owned, committed = None, False, False
    provider = {}
    outcome(state, role, "wmi_attempt")
    folder = Path(tempfile.mkdtemp(prefix="wmi-", dir=root))
    try:
        native.private(folder, caller["owner"])
        request = {"nonce": nonce, "expires": time.time() + TIMEOUT,
                   "caller_pid": os.getpid(), "caller_created": caller["created"],
                   "image": caller["image"], "owner": caller["owner"], "argv": list(command[1:]),
                   "env_digest": _digest(environment), "cwd": str(cwd), "log": str(log),
                   "executable": sys.executable, "prefix": sys.prefix, "exec_prefix": sys.exec_prefix,
                   "sys_path": [str(Path(p or os.getcwd()).resolve()) for p in sys.path]}
        control = folder / "request.json"
        _write(control, request)  # No environment values or credentials on disk.
        phase = "provider_create"
        try:
            pid = _create([command[0], "-I", "-S", str(Path(__file__).resolve()), str(control)], cwd, environment,
                          diagnostics=provider)
            phase = "process_open"
            handle = native.open_process(pid)
            process = Process(native, pid, handle, None)
            phase = "process_identity"
            identity = native.identity(handle)
            phase, receipt_reads = "receipt_wait", {"denied_reads": 0}  # this launch's own receipt wait
            deadline = time.monotonic() + TIMEOUT
            while True:
                receipt = _poll(folder / "receipt.json", receipt_reads)
                if receipt is not None:
                    break
                if not native.alive(handle) or time.monotonic() >= deadline:
                    raise BrokerFailure("receipt_timeout")
                time.sleep(.02)
            phase = "identity_verify"
            if (receipt != {"nonce": nonce, "pid": pid, "created": identity["created"]}
                    or identity["image"] != caller["image"] or identity["owner"] != caller["owner"]):
                raise BrokerFailure("identity_mismatch")
            owned = True  # Nonce + retained-handle creation/image/owner identity; safe to clean up from here.
            process.created = identity["created"]
            diagnostics = {}
            phase = "job_verify"
            member = job_membership(handle, diagnostics=diagnostics)
            if member is not False or not native.alive(handle):
                outcome(state, role, "wmi_unverified", child_in_job=member, child_job_query=diagnostics)
                raise BrokerFailure("job_unverified")
            outcome(state, role, "wmi_verified", child_pid=pid, child_in_job=False, provider=provider)
            process.folder = folder
            phase = "ack_write"
            _write(folder / "ack.json", receipt)  # Commit point: no wait/failable work and no rollback kill after ACK.
            committed = True
            return process
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            if isinstance(exc, BrokerFailure):
                reason = exc.reason
            elif isinstance(exc, subprocess.TimeoutExpired):
                reason = "provider_timeout"
            elif isinstance(exc, (ValueError, TypeError)):
                reason = "provider_protocol" if phase == "provider_create" else "protocol_error"
            else:
                reason = "native_error" if isinstance(exc, OSError) else "provider_subprocess"
            code = getattr(exc, "code", getattr(exc, "errno", None))
            if type(code) is not int:
                code = None
            # Exception messages/commands/output are deliberately never journaled.
            error = next((kind.__name__ for kind in (BrokerFailure, subprocess.TimeoutExpired,
                subprocess.CalledProcessError, subprocess.SubprocessError, PermissionError,
                FileNotFoundError, TimeoutError, OSError, ValueError, TypeError) if isinstance(exc, kind)), "OtherError")
            # The parent's own receipt reads only; the bootstrap's ACK reads are counted in its role log stop line.
            waited = {"denied_reads": receipt_reads["denied_reads"]} if phase == "receipt_wait" else {}
            try:
                outcome(state, role, "wmi_failed", error=error, reason=reason, code=code, phase=phase, provider=provider,
                        **waited)
            finally:  # A full/unwritable journal must never prevent owned-handle cleanup before ACK.
                if process:
                    try:
                        if owned:
                            process.kill()
                            process.wait(timeout=5)
                    finally:
                        process.close()
            raise BrokerFailure(reason, code) from exc
    finally:
        if not committed:
            try:
                _cleanup(folder)
            except OSError as exc:
                outcome(state, role, "wmi_control_cleanup_failed", error=type(exc).__name__)


def bootstrap(control):
    try:
        return _bootstrap(control)
    except BaseException:
        _cleanup(control.parent)
        raise


# Where the bootstrap is once its receipt exists (#844). Before "role" a failure is the bootstrap's own and is
# reported by stage and exception type only; from "role" on it is the role's, with its owned log as stderr.
# denied_reads counts this bootstrap's own ACK reads that were held elsewhere; it starts at 0 with ack_wait.
_STAGE = {"name": None, "log": None, "denied_reads": None}


def _note_stop(error: BaseException) -> None:
    """One line in the requested role log, which the caller verified lies inside the instance state."""
    if _STAGE["name"] is None or not isinstance(_STAGE["log"], str):
        return  # No receipt yet: the caller journals that failure itself (receipt_timeout).
    try:
        with open(_STAGE["log"], "a", encoding="utf-8") as stream:
            stream.write(f"labhq WMI bootstrap stopped before the role started: "
                         f"stage={_STAGE['name']} error={type(error).__name__}"
                         + (f" winerror={error.winerror}" if type(getattr(error, "winerror", None)) is int else "")
                         + (f" denied_reads={_STAGE.get('denied_reads')}"
                            if _STAGE["name"] == "ack_wait" and type(_STAGE.get("denied_reads")) is int else "")
                         + "\n")
    except OSError:
        pass


def main(argv) -> None:
    try:
        bootstrap(Path(argv[1]))
    except Exception as error:
        if _STAGE["name"] == "role":
            raise  # The role's own traceback goes to its owned log, as when it is started directly.
        _note_stop(error)
        raise SystemExit(2) from None  # No argv, environment, credentials, or paths in bootstrap diagnostics.


def _bootstrap(control):
    _STAGE.update(name=None, log=None, denied_reads=None)
    request = _read(control)  # Read once: a request that cannot be read is never waited for.
    native = Native()
    mine = native.identity(native.k.GetCurrentProcess())
    parent = native.open_process(request["caller_pid"], terminate=False)
    try:
        caller = native.identity(parent)
        if (caller["created"] != request["caller_created"] or caller["image"] != request["image"]
                or caller["owner"] != mine["owner"] or mine["owner"] != request["owner"]
                or mine["image"] != request["image"] or _digest(os.environ) != request["env_digest"]):
            raise LaunchError("WMI bootstrap caller/environment mismatch")
        receipt = {"nonce": request["nonce"], "pid": os.getpid(), "created": mine["created"]}
        _write(control.parent / "receipt.json", receipt)
        _STAGE.update(name="ack_wait", log=request.get("log"), denied_reads=0)
        deadline = time.monotonic() + TIMEOUT
        while True:
            if not native.alive(parent) or time.time() >= request["expires"] or time.monotonic() >= deadline:
                raise LaunchError("WMI bootstrap caller exited or ACK expired")
            if _poll(control.parent / "ack.json", _STAGE) == receipt:
                break
            time.sleep(.02)
        _STAGE["name"] = "final_check"
        if job_membership() is not False or not native.alive(parent):
            raise LaunchError("WMI bootstrap final Job/caller check failed")
        _STAGE["name"] = "cleanup"
        _cleanup(control.parent)  # ACK consumed; parent no longer reads control files. No persistent nonce.
    finally:
        native.k.CloseHandle(parent)
    return _enter(request)


def _enter(request):
    # Same interpreter/PID/creation identity. No subprocess and no role ownership before ACK.
    _STAGE["name"] = "enter"
    os.chdir(request["cwd"])
    sys.path[:] = request["sys_path"]
    sys.executable, sys.prefix, sys.exec_prefix = request["executable"], request["prefix"], request["exec_prefix"]
    os.environ.pop("__PYVENV_LAUNCHER__", None)  # Do not leak the consumed CPython carrier to arbitrary staff CLIs.
    log = open(request["log"], "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log  # Keep open through interpreter shutdown, including target SystemExit.
    sys.stdin = open(os.devnull, encoding="utf-8")
    _STAGE["name"] = "role"
    argv = request["argv"]
    if argv[0] == "-m":
        sys.argv = argv[1:]
        return runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
    sys.argv = argv
    return runpy.run_path(argv[0], run_name="__main__")


if __name__ == "__main__":
    main(sys.argv)
