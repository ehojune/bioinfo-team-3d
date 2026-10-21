"""Mock-only broker proof: no WMI, native process, thread or signal is executed."""
import ctypes as C
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import windows_broker as broker, windows_launch as win


@pytest.fixture
def world(tmp_path, monkeypatch):
    image = os.path.normcase(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))
    caller = {"created": "windows:10", "image": image, "owner": "S-1-5-21-100"}
    child = {**caller, "created": "windows:20"}
    native = SimpleNamespace(k=SimpleNamespace(GetCurrentProcess=Mock(return_value=1), CloseHandle=Mock(),
        TerminateProcess=Mock(return_value=1), WaitForSingleObject=Mock(return_value=0), GetExitCodeProcess=Mock(return_value=1)),
        identity=Mock(side_effect=lambda handle: caller if handle == 1 else child), private=Mock(),
        open_process=Mock(return_value=2), alive=Mock(return_value=True), require=lambda value: value or pytest.fail("native failure"))
    monkeypatch.setattr(broker, "Native", lambda: native)
    monkeypatch.setattr(broker, "job_membership", lambda *a, **kw: False)
    state = tmp_path / "state"
    state.mkdir()
    stream = (state / "log").open("a", encoding="utf-8")
    options = {"cwd": str(tmp_path), "env": {"FAKE": "fixture", "PYTHONPATH": os.environ.get("PYTHONPATH", "")}, "stdout": stream}
    record = SimpleNamespace(native=native, caller=caller, child=child, state=state,
                             command=[image, "-m", "labhq.supervisor", "config.yaml", "gateway"], options=options)
    def create(command, cwd, environment, *, diagnostics=None):
        record.control = Path(command[-1])
        record.request = broker._read(record.control)
        broker._write(record.control.parent / "receipt.json",
                      {"nonce": record.request["nonce"], "pid": 222, "created": child["created"]})
        return 222
    record.create = Mock(side_effect=create)
    monkeypatch.setattr(broker, "_create", record.create)
    write = broker._write
    def accept(path, value):
        write(path, value)
        if path.name == "ack.json":
            write(path.parent / "accepted.json", value)
    monkeypatch.setattr(broker, "_write", accept)
    yield record
    stream.close()


def launch(world):
    return broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)


def test_verified_nonce_identity_owner_image_job_precede_ack_and_success(world):
    process = launch(world)
    assert (process.pid, process.created, process._handle) == (222, "windows:20", 2)
    assert world.create.call_count == 1
    world.native.private.assert_called_once()
    assert world.request["env_digest"] == broker._digest(world.options["env"])
    assert "environment" not in world.request and "env" not in world.request
    assert (world.control.parent / "ack.json").is_file()
    events = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text().splitlines()]
    assert [e["status"] for e in events] == ["wmi_attempt", "wmi_verified"]
    assert "S-1-5" not in json.dumps(events) and "fixture" not in json.dumps(events)
    world.native.k.TerminateProcess.assert_not_called()
    process.close()
    assert not world.control.parent.exists()
    world.native.k.CloseHandle.assert_called_once_with(2)


@pytest.mark.parametrize("member", [True, None])
def test_wmi_success_is_not_escape_proof_owned_bootstrap_reaped_without_ack(world, monkeypatch, member):
    monkeypatch.setattr(broker, "job_membership", lambda *a, **kw: member)
    with pytest.raises(win.LaunchError):
        launch(world)
    world.native.k.TerminateProcess.assert_called_once_with(2, 1)
    world.native.k.WaitForSingleObject.assert_called_once_with(2, 5000)
    assert world.create.call_count == 1
    assert not world.control.parent.exists()
    assert not list(world.state.glob("*.pid"))


@pytest.mark.parametrize("mismatch", ["created", "owner", "image", "nonce", "pid"])
def test_identity_mismatch_never_acknowledges_or_kills_unproven_pid(world, monkeypatch, mismatch):
    if mismatch in ("owner", "image"):
        world.child[mismatch] = "different"
    else:
        original = world.create.side_effect
        def create(*args, **kwargs):
            pid = original(*args, **kwargs)
            path = world.control.parent / "receipt.json"
            receipt = broker._read(path)
            receipt[mismatch] = "different"
            broker._write(path, receipt)
            return pid
        world.create.side_effect = create
    with pytest.raises(win.LaunchError):
        launch(world)
    world.native.k.TerminateProcess.assert_not_called()
    world.native.k.CloseHandle.assert_called_once_with(2)
    assert world.create.call_count == 1


def test_no_receipt_timeout_closes_handle_without_pid_only_kill(world, monkeypatch):
    monkeypatch.setattr(broker, "TIMEOUT", 0)
    world.create.side_effect = None
    world.create.return_value = 222
    with pytest.raises(win.LaunchError):
        launch(world)
    world.native.k.TerminateProcess.assert_not_called()
    world.native.k.CloseHandle.assert_called_once_with(2)


def test_ack_write_failure_reaps_only_already_proven_bootstrap(world, monkeypatch):
    write = broker._write
    def fail_ack(path, value):
        if path.name == "ack.json":
            raise OSError("fixture full disk")
        write(path, value)
    monkeypatch.setattr(broker, "_write", fail_ack)
    with pytest.raises(win.LaunchError):
        launch(world)
    world.native.k.TerminateProcess.assert_called_once_with(2, 1)


def test_failed_open_never_terminates_wmi_returned_pid(world):
    world.native.open_process.side_effect = OSError("fixture")
    with pytest.raises(win.LaunchError):
        launch(world)
    world.native.k.TerminateProcess.assert_not_called()


def test_create_timeout_is_unknown_and_never_retried_or_treated_as_owned(world):
    import subprocess

    failure = subprocess.TimeoutExpired("fake-powershell", 10)
    world.create.side_effect = failure
    with pytest.raises(broker.BrokerFailure, match="provider_timeout") as caught:
        launch(world)
    assert caught.value.__cause__ is failure
    world.create.assert_called_once()
    world.native.open_process.assert_not_called()
    world.native.k.TerminateProcess.assert_not_called()
    assert not list((world.state / ".launch").iterdir())  # No ACK/control files survive an uncommitted launch.
    rows = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["wmi_attempt", "wmi_failed"]
    assert rows[-1]["error"] == "TimeoutExpired"
    assert not any("child_pid" in row for row in rows)


@pytest.mark.parametrize("code", [2, 3, 8, 9, 21, -1])
def test_wmi_return_code_is_not_a_success_pid(tmp_path, monkeypatch, code):
    root = tmp_path / "windows"
    shell = root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    shell.parent.mkdir(parents=True)
    shell.touch()
    monkeypatch.setattr(broker, "_powershell", lambda: shell)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"code": code, "pid": 222}).encode()))
    monkeypatch.setattr(broker.subprocess, "run", run)
    with pytest.raises(win.LaunchError, match=f"code={code}"):
        broker._create([str(tmp_path / "python.exe"), "a & $(bad);.py"], tmp_path, {"FAKE": "not-a-secret"})
    run.assert_called_once()
    args, kwargs = run.call_args
    framed = kwargs["input"]
    assert int.from_bytes(framed[:4], "little") == len(framed) - 4
    payload = json.loads(framed[4:].decode("utf-8"))
    assert payload["flags"] & (win.BREAKAWAY | win.DETACHED) == win.BREAKAWAY | win.DETACHED
    assert payload["environment"] == ["FAKE=not-a-secret"]
    assert "not-a-secret" not in str(args)  # Values are data over stdin, never PowerShell source/argv/files.
    assert "a & $(bad);.py" in payload["command"]
    assert kwargs["timeout"] == 10 and kwargs["creationflags"] == 0x08000000
    assert ".ShowWindow = 0" in broker.WMI_SCRIPT


@pytest.mark.parametrize("reason", ["residual", "denied", "unknown", "cleanup_failed"])
def test_direct_owned_cleanup_before_at_most_one_broker(world, monkeypatch, reason):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True})
    child = SimpleNamespace(pid=12, _handle=999, kill=Mock(), wait=Mock(return_value=1))
    direct = Mock(return_value=child)
    calls = []
    child.kill.side_effect = lambda: calls.append("kill")
    child.wait.side_effect = lambda **kw: calls.append("wait") or 1
    fallback = Mock(side_effect=lambda *a, **kw: calls.append("broker") or object())
    monkeypatch.setattr(broker, "launch", fallback)
    monkeypatch.setattr(win, "child_job_membership", lambda *a, **kw: None if reason == "unknown" else True)
    if reason == "denied":
        error = PermissionError("fixture")
        error.winerror = 5
        direct.side_effect = error
    if reason == "cleanup_failed":
        child.wait.side_effect = OSError("fixture")
    if reason in ("unknown", "cleanup_failed"):
        with pytest.raises(win.LaunchError):
            win.spawn(world.command, state=world.state, role="gateway-supervisor", popen=direct, **world.options)
        fallback.assert_not_called()
    else:
        win.spawn(world.command, state=world.state, role="gateway-supervisor", popen=direct, **world.options)
        fallback.assert_called_once()
        assert calls == (["broker"] if reason == "denied" else ["kill", "wait", "broker"])
    assert direct.call_count == 1


def test_venv_redirector_is_bypassed_without_losing_venv_environment(world, monkeypatch):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": False})
    monkeypatch.setattr(win, "child_job_membership", lambda *a, **kw: False)
    direct = Mock(return_value=SimpleNamespace(pid=123))
    win.spawn([sys.executable, "-m", "labhq.supervisor", "config", "gateway"], state=world.state,
              role="gateway-supervisor", popen=direct, **world.options)
    assert direct.call_args.args[0][0] == getattr(sys, "_base_executable", sys.executable)
    assert direct.call_args.kwargs["env"]["__PYVENV_LAUNCHER__"] == sys.executable
    assert world.options["env"] == {"FAKE": "fixture", "PYTHONPATH": os.environ.get("PYTHONPATH", "")}


@pytest.mark.parametrize("mode", ["healthy", "dead_caller", "expired", "caller_identity", "environment", "job", "unknown_job", "open_failed"])
def test_bootstrap_cannot_enter_role_before_live_caller_nonce_ack_and_job_check(world, monkeypatch, mode):
    control = world.state / "wmi-bootstrap" / "request.json"
    control.parent.mkdir()
    request = {"caller_pid": 111, "caller_created": world.caller["created"], "image": world.caller["image"],
               "owner": world.caller["owner"], "env_digest": broker._digest(os.environ),
               "nonce": "test-nonce", "expires": 0 if mode == "expired" else broker.time.time() + 20}
    broker._write(control, request)
    world.native.identity.side_effect = lambda handle: world.child if handle == 1 else world.caller
    world.native.alive.return_value = mode != "dead_caller"
    if mode == "caller_identity":
        world.caller["created"] = "windows:30"
    if mode == "environment":
        monkeypatch.setattr(broker, "_digest", lambda env: "mismatch")
    if mode == "open_failed":
        world.native.open_process.side_effect = OSError("fixture")
    monkeypatch.setattr(broker, "job_membership", lambda *a, **kw: None if mode == "unknown_job" else mode == "job")
    enter = Mock(return_value=42)
    monkeypatch.setattr(broker, "_enter", enter)
    write = broker._write
    def ack(path, receipt):
        write(path, receipt)
        if path.name == "receipt.json":
            write(path.parent / "ack.json", receipt)
    monkeypatch.setattr(broker, "_write", ack)
    if mode == "healthy":
        assert broker.bootstrap(control) == 42
        enter.assert_called_once_with(request)
        assert not control.parent.exists()
    else:
        with pytest.raises((win.LaunchError, OSError)):
            broker.bootstrap(control)
        enter.assert_not_called()
        assert not control.parent.exists()
    world.native.k.TerminateProcess.assert_not_called()  # Self-abort, never kill caller or scan its tree.


def test_native_bindings_retain_pointer_width_and_process_token_types(monkeypatch):
    kernel, advapi = Mock(), Mock()
    monkeypatch.setattr(C, "WinDLL", Mock(side_effect=[kernel, advapi]), raising=False)
    broker.Native()
    assert kernel.OpenProcess.restype is C.c_void_p
    assert kernel.GetCurrentProcess.restype is C.c_void_p
    assert kernel.CloseHandle.argtypes == [C.c_void_p]
    assert kernel.GetProcessTimes.argtypes == [C.c_void_p] * 5
    assert kernel.QueryFullProcessImageNameW.restype is C.c_int
    assert advapi.OpenProcessToken.argtypes == [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
    assert kernel.WaitForSingleObject.restype is C.c_uint32


@pytest.mark.parametrize("result", ["valid", "failed", "truncated"])
def test_system_powershell_path_uses_native_system_directory_not_environment(tmp_path, monkeypatch, result):
    directory = str(tmp_path / "system32")
    def locate(buffer, size):
        buffer.value = directory
        return len(directory) if result == "valid" else 0 if result == "failed" else size
    function = Mock(side_effect=locate)
    monkeypatch.setattr(C, "WinDLL", Mock(return_value=SimpleNamespace(GetSystemDirectoryW=function)), raising=False)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "untrusted"))
    if result == "valid":
        assert broker._powershell() == Path(directory) / "WindowsPowerShell/v1.0/powershell.exe"
    else:
        with pytest.raises(broker.BrokerFailure):
            broker._powershell()
    assert function.argtypes == [C.c_wchar_p, C.c_uint32] and function.restype is C.c_uint32


def test_real_identity_reader_uses_held_handle_creation_image_and_token_user(tmp_path):
    native = broker.Native.__new__(broker.Native)
    held, created = 0x1234567887654321, (1 << 40) + 42
    image, owner = str(tmp_path / "python.exe"), "S-1-5-21-100"
    sid, text = C.create_string_buffer(16), C.create_unicode_buffer(owner)
    def times(handle, creation, *rest):
        assert handle == held
        C.cast(creation, C.POINTER(C.c_uint64))[0] = created
        return 1
    def executable(handle, flags, buffer, length):
        assert handle == held and flags == 0
        buffer.value = image
        return 1
    def token(handle, access, pointer):
        assert handle == held and access == 8
        C.cast(pointer, C.POINTER(C.c_void_p))[0] = 55
        return 1
    def information(handle, kind, data, size, length):
        assert handle.value == 55 and kind == 1
        C.cast(length, C.POINTER(C.c_uint32))[0] = 32
        if data is None:
            return 0
        C.cast(data, C.POINTER(C.c_void_p))[0] = C.addressof(sid)
        return 1
    def convert(pointer, result):
        assert pointer.value == C.addressof(sid)
        C.cast(result, C.POINTER(C.c_void_p))[0] = C.addressof(text)
        return 1
    native.k = SimpleNamespace(GetProcessTimes=times, QueryFullProcessImageNameW=executable,
                               CloseHandle=Mock(), LocalFree=Mock())
    native.a = SimpleNamespace(OpenProcessToken=token, GetTokenInformation=information, ConvertSidToStringSidW=convert)
    assert native.identity(held) == {"created": f"windows:{created}", "image": os.path.normcase(os.path.realpath(image)), "owner": owner}
    assert native.k.CloseHandle.call_args.args[0].value == 55
    assert native.k.LocalFree.call_args.args[0].value == C.addressof(text)


@pytest.mark.parametrize("success", [True, False])
def test_private_control_directory_applies_protected_current_user_inheritable_acl(tmp_path, success):
    native = broker.Native.__new__(broker.Native)
    def descriptor(sddl, revision, pointer, size):
        assert sddl == "D:P(A;OICI;FA;;;S-1-5-21-100)" and revision == 1
        C.cast(pointer, C.POINTER(C.c_void_p))[0] = 123
        return 1
    native.k = SimpleNamespace(LocalFree=Mock())
    native.a = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=descriptor,
                               SetFileSecurityW=Mock(return_value=success))
    def require(ok):
        if not ok:
            raise OSError("fixture")
    native.require = require
    if success:
        native.private(tmp_path, "S-1-5-21-100")
    else:
        with pytest.raises(OSError):
            native.private(tmp_path, "S-1-5-21-100")
    assert native.a.SetFileSecurityW.call_args.args[1] == 0x80000004
    assert native.k.LocalFree.call_args.args[0].value == 123


@pytest.mark.parametrize("entry", [["-m", "labhq.supervisor", "config", "gateway"], ["fake.py", "argument"]])
def test_transition_runs_in_same_process_without_spawn(tmp_path, monkeypatch, entry):
    request = {"cwd": str(tmp_path), "sys_path": list(sys.path), "executable": sys.executable,
               "prefix": sys.prefix, "exec_prefix": sys.exec_prefix, "argv": entry, "log": str(tmp_path / "log")}
    for name in ("stdin", "stdout", "stderr", "argv", "path", "executable", "prefix", "exec_prefix"):
        value = getattr(sys, name)
        monkeypatch.setattr(sys, name, list(value) if isinstance(value, list) else value)
    monkeypatch.setattr(broker.os, "chdir", Mock())
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "fixture-consumed-carrier")
    pid = os.getpid()
    observed = []
    def run(*args, **kw):
        observed.append((os.getpid(), list(sys.argv), args, kw))
    monkeypatch.setattr(broker.runpy, "run_module", run)
    monkeypatch.setattr(broker.runpy, "run_path", run)
    broker._enter(request)
    assert observed[0][0] == pid
    assert observed[0][1] == (entry[1:] if entry[0] == "-m" else entry)
    assert observed[0][3]["run_name"] == "__main__"
    assert "__PYVENV_LAUNCHER__" not in os.environ
    sys.stdout.close()
    sys.stdin.close()
