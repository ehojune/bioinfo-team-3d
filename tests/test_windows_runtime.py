"""Mock-only launch/runtime contracts; native Job integration is a separate CI-only file."""
import ctypes as C
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest

from labhq import cli, doctor, instance_guard, runtime_readiness as runtime, windows_launch as win
from labhq.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                    "runner": {"state_dir": str(tmp_path / "state")},
                                    "hpc": {"scheduler": "none"}})


def test_detached_windows_options_require_breakaway_and_child_gate(settings, monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    options = cli._launch_options(settings)
    assert options["creationflags"] & (win.BREAKAWAY | win.DETACHED) == win.BREAKAWAY | win.DETACHED
    assert options["env"][win.CHECK_ENV] == "1"


@pytest.mark.parametrize("member", [True, None])
def test_unverified_child_is_reaped_without_success_pid(settings, monkeypatch, member):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True, "limit_flags": win.KILL_ON_CLOSE})
    monkeypatch.setattr(win, "child_job_membership", lambda child, **kw: member)
    child = SimpleNamespace(pid=123, kill=Mock(), wait=Mock())
    with pytest.raises(SystemExit) as stopped:
        cli._start_background(settings, "gateway", popen=Mock(return_value=child))
    assert stopped.value.code == 2
    child.kill.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    assert not cli._pid_file(settings, "gateway-supervisor").exists()


def test_denied_breakaway_never_retries_or_records_success(settings, monkeypatch, capsys):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": None})
    spawn = Mock(side_effect=PermissionError("fixture denied"))
    with pytest.raises(SystemExit) as stopped:
        cli._start_background(settings, "gateway", popen=spawn)
    assert stopped.value.code == 2 and spawn.call_count == 1
    assert spawn.call_args.kwargs["creationflags"] & win.BREAKAWAY
    assert not cli._pid_file(settings, "gateway-supervisor").exists()
    assert "독립 콘솔" in capsys.readouterr().err
    events = [json.loads(line) for line in (cli._launch_state(settings) / "logs/detached-launch.jsonl").read_text().splitlines()]
    assert [e["status"] for e in events] == ["attempt", "denied"]


def test_verified_launch_records_identity_only_after_job_check(settings, monkeypatch):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True})
    monkeypatch.setattr(win, "child_job_membership", lambda child, **kw: False)
    monkeypatch.setattr(cli, "_process_created", lambda pid: "windows:123")
    monkeypatch.setattr(cli, "_await_child_record", lambda *a, **kw: None)
    assert cli._start_background(settings, "gateway", popen=Mock(return_value=SimpleNamespace(pid=123))) == 123
    assert json.loads(cli._pid_file(settings, "gateway-supervisor").read_text())["created"] == "windows:123"


def test_posix_and_foreground_do_not_require_job_escape(settings, monkeypatch):
    monkeypatch.setattr(win.sys, "platform", "linux")
    monkeypatch.setattr(win, "job_membership", lambda *a, **kw: pytest.fail("native Job query"))
    assert cli._launch_options(settings)["start_new_session"] is True
    child = object()
    assert win.spawn([], state=cli._launch_state(settings), role="gateway", popen=lambda *a, **kw: child) is child
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.delenv(win.CHECK_ENV, raising=False)
    win.require_detached_job()


@pytest.mark.parametrize("member", [True, None, False])
def test_managed_entry_requires_proven_nonmembership(monkeypatch, member):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setenv(win.CHECK_ENV, "1")
    monkeypatch.setattr(win, "job_membership", lambda: member)
    if member is False:
        win.require_detached_job()
        assert win.CHECK_ENV not in os.environ
    else:
        with pytest.raises(win.LaunchError):
            win.require_detached_job()


def test_readonly_diagnostics_mark_limits_as_immediate_only(monkeypatch):
    class Kernel:
        def QueryInformationJobObject(self, job, kind, pointer, size, returned):
            C.cast(pointer, C.POINTER(win.ExtendedLimits)).contents.basic.flags = win.KILL_ON_CLOSE | win.BREAKAWAY_OK
            return 1
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "job_membership", lambda *a, **kw: True)
    monkeypatch.setattr(win, "_kernel", Kernel)
    facts = win.current_job()
    assert facts["kill_on_close"] and facts["breakaway_ok"] and not facts["silent_breakaway_ok"]
    assert facts["scope"] == "immediate_job_only"


@pytest.mark.parametrize("reply,expected", [(0, None), (1, False), (2, True)])
def test_job_membership_api_failure_stays_unknown(monkeypatch, reply, expected):
    monkeypatch.setattr(C, "get_last_error", lambda: 6, raising=False)
    class Kernel:
        def GetCurrentProcess(self):
            return 1
        def IsProcessInJob(self, handle, job, pointer):
            assert handle == 1 and job is None
            C.cast(pointer, C.POINTER(C.c_int)).contents.value = reply == 2
            return reply != 0
    monkeypatch.setattr(win, "_kernel", Kernel)
    assert win.job_membership() is expected


def test_native_bindings_use_pointer_sized_handles_and_bool_output(monkeypatch):
    kernel = SimpleNamespace(GetCurrentProcess=Mock(), IsProcessInJob=Mock(), QueryInformationJobObject=Mock())
    loader = Mock(return_value=kernel)
    monkeypatch.setattr(C, "WinDLL", loader, raising=False)
    assert win._kernel() is kernel
    loader.assert_called_once_with("kernel32", use_last_error=True)
    assert kernel.GetCurrentProcess.argtypes == []
    assert kernel.GetCurrentProcess.restype is C.c_void_p
    assert kernel.IsProcessInJob.argtypes == [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]
    assert kernel.IsProcessInJob.restype is C.c_int
    assert kernel.QueryInformationJobObject.argtypes == [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.c_void_p]
    assert kernel.QueryInformationJobObject.restype is C.c_int


def test_ctypes_preserves_subprocess_int_subclass_handle_above_32_bits(monkeypatch):
    class Handle(int):  # subprocess.Handle is an int subclass; no OS handle is opened here.
        pass
    value = 0x1234567887654321
    calls = []
    @C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.POINTER(C.c_int))
    def query(handle, job, result):
        calls.append((handle, job))
        result[0] = 0
        return 1
    monkeypatch.setattr(win, "_kernel", lambda: SimpleNamespace(IsProcessInJob=query))
    assert win.child_job_membership(SimpleNamespace(_handle=Handle(value))) is False
    assert calls == [(value, None)]


@pytest.mark.parametrize("error", [6, 5])
def test_membership_failure_retains_native_error_and_fails_closed(settings, monkeypatch, error):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True, "scope": "immediate_job_only"})
    monkeypatch.setattr(C, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(win, "_kernel", lambda: SimpleNamespace(IsProcessInJob=lambda *a: 0))
    child = SimpleNamespace(pid=123, _handle=987, kill=Mock(), wait=Mock())
    with pytest.raises(win.LaunchError, match=f'"winerror": {error}'):
        win.spawn([], state=cli._launch_state(settings), role="gateway", popen=Mock(return_value=child))
    child.kill.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    events = [json.loads(line) for line in (cli._launch_state(settings) / "logs/detached-launch.jsonl").read_text().splitlines()]
    assert events[-1]["child_in_job"] is None
    assert events[-1]["child_job_query"] == {"query_error": "IsProcessInJob", "winerror": error}
    assert not any(e["status"] in ("job_verified", "recorded") for e in events)


def test_binding_error_is_unknown_with_explicit_diagnostic(monkeypatch):
    monkeypatch.setattr(win, "_kernel", lambda: SimpleNamespace(IsProcessInJob=Mock(side_effect=C.ArgumentError("fixture"))))
    facts = {}
    assert win.job_membership(123, diagnostics=facts) is None
    assert facts == {"query_error": "ArgumentError", "winerror": None}


@pytest.mark.parametrize("outcome", ["denied", "unverified", "verified"])
def test_ci_positive_contract_cannot_pass_on_denial_or_unknown(tmp_path, monkeypatch, capsys, outcome):
    # Import only definitions; replace the entire owned Job fixture before exercising its assertions.
    path = Path(__file__).with_name("test_windows_job_ci.py")
    spec = importlib.util.spec_from_file_location("win795_ci_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pid_file = tmp_path / "pid.json"
    pid_file.write_text('{"pid": 123, "created": "windows:123"}')
    (tmp_path / "ready").touch()
    events = ([{"status": status} for status in ("attempt", "job_verified", "recorded")]
              if outcome == "verified" else [{"status": outcome}])
    wait, close = Mock(return_value=258), Mock()
    case = SimpleNamespace(result={"success": outcome == "verified"}, events=events, pid_file=pid_file,
                           dll=SimpleNamespace(WaitForSingleObject=wait), child_handle=None)
    monkeypatch.setattr(module, "owned_launch", lambda *a, **kw: nullcontext(case))
    monkeypatch.setattr(module, "hold_recorded_child", lambda *a: 123)
    monkeypatch.setattr(module, "close_parent_job", close)
    monkeypatch.setattr(win, "job_membership", lambda *a: False)
    if outcome == "verified":
        module.test_allowed_breakaway_child_survives_parent_job_close(tmp_path, SimpleNamespace(disabled=nullcontext))
        close.assert_called_once_with(case)
        wait.assert_called_once_with(123, 300)
    else:
        with pytest.raises(AssertionError, match="UNVERIFIED positive survival"):
            module.test_allowed_breakaway_child_survives_parent_job_close(tmp_path, capsys)
        close.assert_not_called()
        wait.assert_not_called()


def test_missing_child_handle_is_not_a_query_of_the_parent(monkeypatch):
    monkeypatch.setattr(win, "job_membership", lambda *a, **kw: pytest.fail("missing child handle must stay unknown"))
    assert win.child_job_membership(SimpleNamespace(_handle=None)) is None


def test_supervisor_child_denial_never_records_a_role_pid(settings, monkeypatch):
    from labhq.supervisor import Supervisor
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True})
    popen, diag = Mock(side_effect=PermissionError("fake denied")), SimpleNamespace(write=Mock())
    supervisor = Supervisor(settings, "gateway", diag, popen=popen)
    assert supervisor._start(1) is None
    assert popen.call_count == 1 and not cli._pid_file(settings, "gateway").exists()
    assert diag.write.call_args.kwargs["reason"] == "PermissionError"


@pytest.mark.parametrize("mode,expected,ready", [
    ("healthy", "running", True), ("stale", "unhealthy", False), ("missing", "unhealthy", False),
    ("unknown", "unhealthy", False), ("pristine", "pristine", None), ("down", "down", False),
    ("foreground", "unmanaged", None), ("missing_supervisor", "unhealthy", False)])
def test_local_readiness_matrix(settings, monkeypatch, mode, expected, ready):
    monkeypatch.setattr(win, "record_job_membership", lambda record: False)  # Proof is exercised with handles separately.
    root = cli._launch_state(settings)
    states = {"gateway": "missing", "runner": "missing", "gateway-supervisor": "missing", "runner-supervisor": "missing"}
    if mode in ("healthy", "stale", "unknown", "missing_supervisor"):
        states = {key: "alive" for key in states}
        states["runner"] = {"healthy": "alive", "stale": "stale", "unknown": "unknown", "missing_supervisor": "alive"}[mode]
        if mode == "missing_supervisor":
            states["runner-supervisor"] = "missing"
            (root / "logs").mkdir(parents=True)
            (root / "logs/runner-supervisor.log").touch()
    if mode in ("missing", "down"):
        win.outcome(root, "runner-supervisor", "recorded")
    if mode == "down":
        cli._stopping_file(settings, "runner").touch()
    if mode == "foreground":
        (root / "logs").mkdir(parents=True)
        (root / "logs/runner-diagnostics.jsonl").write_text('{"event":"start"}\n')
    monkeypatch.setattr(cli, "_process_record", lambda s, role, *, cleanup: (states[role], None, None))
    result = runtime.inspect(settings)
    assert result["status"] == expected and result["ready"] is ready


@pytest.mark.parametrize("role_mode", ["remote", "foreground"])
def test_runtime_uses_authenticated_gateway_for_roles_without_local_pids(settings, monkeypatch, role_mode):
    local = {"ready": None, "status": "unmanaged", "roles": {}, "reasons": []}
    settings.gateway.url = "ws://gateway.invalid:1234" if role_mode == "remote" else "ws://127.0.0.1:1234"
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id]})
    check = create_autospec(instance_guard.check_health, return_value=([], True))
    monkeypatch.setattr(instance_guard, "check_health", check)
    assert runtime.deployment(settings, local)["ready"] is True
    assert check.call_args.kwargs == {"started_here": False}
    check.return_value = ([], False)
    assert runtime.deployment(settings, local)["ready"] is False


def test_unknown_local_identity_is_not_overridden_by_healthy_gateway(settings, monkeypatch):
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id]})
    monkeypatch.setattr(instance_guard, "check_health", create_autospec(instance_guard.check_health, return_value=([], True)))
    assert runtime.deployment(settings, {"ready": False, "status": "unhealthy", "reasons": ["unknown"]})["ready"] is False


@pytest.mark.parametrize("identity_kind,expected", [("ours", True), ("foreign", False), ("missing", False)])
def test_runtime_calls_real_health_identity_guard_readonly(settings, monkeypatch, tmp_path, identity_kind, expected):
    settings.config_path = str(tmp_path / "config.yaml")
    health = {"runners": [settings.runner.id], "identity": instance_guard.public_identity(settings)}
    if identity_kind == "foreign":
        health["identity"] = {"config_id": "foreign", "fingerprint": "foreign"}
    elif identity_kind == "missing":
        health.pop("identity")
    monkeypatch.setattr(cli, "_health", lambda s: health)
    # No process/registry discovery: exercise the real check_health signature and identity decisions.
    monkeypatch.setattr(instance_guard, "_gateway_state_owners", lambda s: [])
    monkeypatch.setattr(instance_guard, "_known_config", lambda *a: None)
    result = runtime.deployment(settings, {"ready": None, "status": "pristine", "reasons": []})
    assert result["ready"] is expected
    assert bool(result["reasons"]) is (not expected)


@pytest.mark.parametrize("connected", [True, False])
def test_enabled_wsl_requires_authenticated_connection_without_executing_wsl(settings, monkeypatch, connected):
    settings.runner.wsl.enabled = True
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id] + ([s.runner.wsl.id] if connected else [])})
    monkeypatch.setattr(instance_guard, "check_health", create_autospec(instance_guard.check_health, return_value=([], True)))
    result = runtime.deployment(settings, {"ready": None, "status": "pristine", "reasons": []})
    assert result["ready"] is connected
    assert any("WSL runner" in r for r in result["reasons"]) is (not connected)


def test_runtime_inspection_does_not_remove_a_stale_pid_record(settings, monkeypatch):
    path = cli._pid_file(settings, "gateway")
    path.parent.mkdir(parents=True)
    saved = json.dumps({"pid": 123, "created": "windows:123", "role": "gateway", "config": None})
    path.write_text(saved, encoding="utf-8")
    monkeypatch.setattr(cli, "_process_created", lambda pid: None)
    assert runtime.inspect(settings)["ready"] is False
    assert path.read_text(encoding="utf-8") == saved


def test_doctor_labels_configuration_and_runtime_separately():
    result = {"checks": [], "summary": {"fail": 1},
              "configuration_readiness": {"ready": True, "reasons": []},
              "runtime_readiness": {"ready": False, "status": "unhealthy", "reasons": ["runner: stale"]},
              "readiness": {"ready": False, "reasons": ["runner: stale"]}}
    text = doctor.render(result)
    assert "설정 준비: 예" in text and "운영 준비: 아니오" in text and "runner: stale" in text


@pytest.mark.parametrize("mode,required,failed", [("pristine", False, False), ("unhealthy", False, True),
                                                ("pristine", True, True), ("verified", True, False)])
def test_collect_preserves_config_readiness_but_fails_deployment_when_needed(
        settings, monkeypatch, mode, required, failed):
    from labhq.models import AgentSpec, Engine
    monkeypatch.setattr(doctor, "_roster", lambda s: [AgentSpec(id="fake", name="Fake", role="test", engine=Engine.mock)])
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_disk_rows", lambda s: [])
    monkeypatch.setattr(doctor, "_alphagenome_row", lambda s: doctor._row("api", "fake", "skip", "mock", ""))
    monkeypatch.setattr(doctor, "_memory_limit_row", lambda s: doctor._row("config", "memory", "ok", "mock", ""))
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": False})
    reasons = ["runner: unknown"] if mode == "unhealthy" else []
    facts = {"ready": True if mode == "verified" else False if reasons else None,
             "status": mode, "roles": {}, "reasons": reasons}
    monkeypatch.setattr(runtime, "inspect", lambda s: facts)
    monkeypatch.setattr(runtime, "deployment", lambda s, local: local)
    result = doctor.collect(settings, runtime=required, dry_run=True, require_paper2agent=False)
    assert result["configuration_readiness"]["ready"] is True
    assert bool(result["summary"]["fail"]) is failed
    assert result["readiness"]["ready"] is (not failed)


def test_runtime_cli_fails_even_when_only_configuration_readiness_is_false(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("gateway: {}\n", encoding="utf-8")
    result = {"checks": [], "summary": {"fail": 0}, "readiness": {"ready": False, "reasons": ["no engine"]},
              "configuration_readiness": {"ready": False, "reasons": ["no engine"]},
              "runtime_readiness": {"ready": True, "status": "verified", "reasons": []}}
    seen = []
    def collect(*args, **kwargs):
        seen.append(kwargs["runtime"])
        return result
    monkeypatch.setattr(doctor, "collect", collect)
    with pytest.raises(SystemExit) as failed:
        cli.main(["-c", str(config), "doctor", "--runtime"])
    assert failed.value.code == 1 and seen == [True]
