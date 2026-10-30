"""Read-only contained-runtime contract; all OS handles and IPC are fakes."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import cli, instance_guard, runtime_readiness as runtime, windows_broker, windows_launch
from labhq.settings import Settings
from tests.support.time_proxy import RuntimeProxy


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf8")
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                        "runner": {"state_dir": str(tmp_path / "state")}})
    settings.config_path = str(config)
    launcher, launch_contract, image = (tmp_path / name for name in ("reviewed-owner.py", "prepare.json", "owner.exe"))
    for artifact in (launcher, launch_contract, image):
        artifact.write_bytes(b"synthetic reviewed artifact")
    names = ("gateway", "gateway-supervisor", "runner", "runner-supervisor")
    records = {name: {"pid": i + 10, "created": f"windows:{100 + i}"} for i, name in enumerate(names)}
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "time", RuntimeProxy(runtime.time, time=lambda: 1000))
    monkeypatch.setattr(cli, "_process_record", lambda s, role, **kw: ("alive", records[role], None))
    monkeypatch.setattr(windows_launch, "record_job_membership", lambda r: True)
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id]})
    monkeypatch.setattr(instance_guard, "check_health", lambda *a, **kw: ([], True))
    receipt = {"schema": "labhq-contained-runtime/1", "issuer": "labhq-contained-owner/1",
               "config_path": str(config.resolve()), "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
               "nonce": "a" * 32, "owner": {"pid": 999, "created": "windows:900"},
               "roles": records, "issued_at": 900, "deadline_at": 1200,
               "provenance": {"source_commit": "c" * 40, "contract_sha256": hashlib.sha256(launch_contract.read_bytes()).hexdigest(),
                              "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                              "runtime_readiness_path": str(Path(runtime.__file__).resolve()),
                              "runtime_readiness_sha256": hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest()},
               "pipe": "\\\\.\\pipe\\labhq-contained-" + "b" * 32}
    root = cli._launch_state(settings)
    root.mkdir(parents=True)
    path = root / "contained-runtime.json"
    def save(): path.write_text(json.dumps(receipt), encoding="utf8")
    save()
    def artifact_row(path): return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    trust = {"schema": "labhq-contained-trust/1", "issuer": "operator-reviewed", "issued_at": 990, "expires_at": 1100,
             **{key: copy.deepcopy(receipt[key]) for key in
                ("owner", "nonce", "roles", "config_sha256", "deadline_at", "provenance")},
             "launcher": artifact_row(launcher), "launch_contract": artifact_row(launch_contract), "owner_image": artifact_row(image)}
    trust_path = tmp_path / "operator-trust.json"
    trust_path.write_text(json.dumps(trust), encoding="utf8")
    monkeypatch.setenv("LABHQ_CONTAINED_TRUST_PATH", str(trust_path))
    monkeypatch.setenv("LABHQ_CONTAINED_TRUST_SHA256", hashlib.sha256(trust_path.read_bytes()).hexdigest())
    native = SimpleNamespace(open_process=Mock(return_value=77),
                             identity=Mock(return_value={"created": "windows:900", "image": str(image)}),
                             alive=Mock(return_value=True), k=SimpleNamespace(CloseHandle=Mock()))
    monkeypatch.setattr(windows_broker, "Native", lambda: native)
    calls = []
    def query(pipe, owner_pid, request, timeout):
        calls.append(copy.deepcopy(request))
        return {"schema": "labhq-contained-snapshot/1", "issuer": receipt["issuer"],
                "owner": receipt["owner"], "nonce": receipt["nonce"], "challenge": request["challenge"],
                "config_sha256": receipt["config_sha256"], "deadline_at": receipt["deadline_at"],
                "provenance": copy.deepcopy(receipt["provenance"]),
                "observed_at": 1000, "deadline_active": True, "job_limit_flags": 0x2000,
                "roles": {name: {**row, "in_job": True, "alive": True} for name, row in records.items()}}
    monkeypatch.setattr(runtime, "_pipe_request", query)
    return SimpleNamespace(settings=settings, receipt=receipt, records=records, save=save,
                           path=path, native=native, calls=calls, query=query, trust=trust, trust_path=trust_path,
                           image=image, launcher=launcher, launch_contract=launch_contract)


def test_contained_is_explicit_and_preserves_actual_membership_and_authenticated_health(cohort):
    local = runtime.inspect(cohort.settings)
    assert local["ready"] is True and local["status"] == "contained_verified"
    assert all(value is True for row in local["roles"].values() for value in row["job_membership"].values())
    final = runtime.deployment(cohort.settings, local)
    assert final["ready"] is True and final["status"] == "contained_verified"
    assert cohort.calls[0]["roles"] == cohort.records
    assert len(cohort.calls[0]["challenge"]) == 32
    cohort.native.open_process.assert_called_once_with(999, terminate=False)
    cohort.native.k.CloseHandle.assert_called_once_with(77)


@pytest.mark.parametrize("fault", ["issuer", "nonce", "owner", "role_generation", "missing_role", "extra_role",
                                  "deadline", "lifetime", "config", "pipe", "owner_reused", "owner_dead",
                                  "response_nonce", "response_challenge", "response_issuer", "response_owner",
                                  "response_deadline", "response_stale", "response_membership", "response_role",
                                  "response_inactive", "response_breakaway", "response_no_kill", "ipc",
                                  "source", "verifier", "response_provenance", "owner_died_during_query",
                                  "duplicate_role_pid", "trust_expired_during_query"])
def test_bad_containment_never_overrides_detached_failure(cohort, monkeypatch, fault):
    r = cohort.receipt
    if fault == "issuer": r["issuer"] = "untrusted"
    if fault == "nonce": r["nonce"] = ""
    if fault == "owner": r["owner"]["created"] = "unknown"
    if fault == "role_generation": r["roles"] = {**r["roles"], "runner": {"pid": 12, "created": "windows:999"}}
    if fault == "missing_role": r["roles"] = {k: v for k, v in r["roles"].items() if k != "runner"}
    if fault == "extra_role": r["roles"] = {**r["roles"], "foreign": {"pid": 20, "created": "windows:200"}}
    if fault == "deadline": r["deadline_at"] = 999
    if fault == "lifetime": r["deadline_at"] = 9000
    if fault == "config": r["config_sha256"] = "f" * 64
    if fault == "pipe": r["pipe"] = "\\\\remote\\pipe\\foreign"
    if fault == "owner_reused": cohort.native.identity.return_value = {"created": "windows:901"}
    if fault == "owner_dead": cohort.native.alive.return_value = False
    if fault == "owner_died_during_query": cohort.native.alive.side_effect = [True, False]
    if fault == "duplicate_role_pid": cohort.records["runner"]["pid"] = cohort.records["gateway"]["pid"]
    if fault == "source": r["provenance"]["source_commit"] = "unknown"
    if fault == "verifier": r["provenance"]["runtime_readiness_sha256"] = "f" * 64
    cohort.save()
    def query(*args):
        if fault == "ipc": raise OSError("fake timeout")
        value = cohort.query(*args)
        changes = {"response_nonce": ("nonce", "c" * 32), "response_challenge": ("challenge", "d" * 32),
                   "response_issuer": ("issuer", "foreign"), "response_owner": ("owner", {"pid": 1}),
                   "response_deadline": ("deadline_at", 1300), "response_stale": ("observed_at", 990),
                   "response_inactive": ("deadline_active", False), "response_breakaway": ("job_limit_flags", 0x2800),
                   "response_no_kill": ("job_limit_flags", 0)}
        if fault in changes: value[changes[fault][0]] = changes[fault][1]
        if fault == "response_membership": value["roles"]["runner"]["in_job"] = False
        if fault == "response_role": value["roles"]["runner"]["created"] = "windows:999"
        if fault == "response_provenance": value["provenance"]["launcher_sha256"] = "f" * 64
        if fault == "trust_expired_during_query":
            monkeypatch.setattr(runtime, "time", RuntimeProxy(runtime.time, time=lambda: 1101))
            value["observed_at"] = 1101
        return value
    monkeypatch.setattr(runtime, "_pipe_request", query)
    result = runtime.deployment(cohort.settings, runtime.inspect(cohort.settings))
    assert result["ready"] is False
    assert any("Job escape unverified" in reason for reason in result["reasons"])


def _receipt_lifetime(cohort, monkeypatch, lifetime):
    """Re-issue the receipt (and its operator pin) with deadline_at = issued_at (900) + lifetime."""
    deadline = 900 + lifetime
    cohort.receipt["deadline_at"] = deadline
    cohort.trust["deadline_at"] = deadline
    cohort.save()
    cohort.trust_path.write_text(json.dumps(cohort.trust), encoding="utf8")
    monkeypatch.setenv("LABHQ_CONTAINED_TRUST_SHA256", hashlib.sha256(cohort.trust_path.read_bytes()).hexdigest())
    return runtime.deployment(cohort.settings, runtime.inspect(cohort.settings))


@pytest.mark.parametrize(("limit", "lifetime", "ready"), [
    (None, 5400, True), (None, 5401, False),  # default: the 90-minute behaviour is unchanged
    (54000, 54000, True), (54000, 54001, False),  # a 15-hour run needs the setting, and it is still a bound
])
def test_receipt_lifetime_follows_the_configured_bound(cohort, monkeypatch, limit, lifetime, ready):
    if limit is not None:
        cohort.settings.runner.contained_max_s = limit
    result = _receipt_lifetime(cohort, monkeypatch, lifetime)
    assert result["ready"] is ready
    assert (result["status"] == "contained_verified") is ready


def test_the_bound_is_configurable_but_has_a_ceiling():
    assert Settings().runner.contained_max_s == 5400
    assert Settings.model_validate({"runner": {"contained_max_s": 54000}}).runner.contained_max_s == 54000
    for bad in (86401, 59, 0, -1):
        with pytest.raises(ValueError):
            Settings.model_validate({"runner": {"contained_max_s": bad}})


def test_detached_policy_ignores_optional_receipt(cohort, monkeypatch):
    cohort.path.write_text("broken", encoding="utf8")
    monkeypatch.setattr(windows_launch, "record_job_membership", lambda r: False)
    result = runtime.deployment(cohort.settings, runtime.inspect(cohort.settings))
    assert result["ready"] is True and result["status"] == "verified"
    assert not cohort.calls


def test_unknown_membership_or_unreadable_history_stays_failed(cohort, monkeypatch):
    monkeypatch.setattr(windows_launch, "record_job_membership", lambda r: None)
    assert runtime.inspect(cohort.settings)["ready"] is False
    assert not cohort.calls
    monkeypatch.setattr(windows_launch, "record_job_membership", lambda r: True)
    logs = cohort.path.parent / "logs"
    logs.mkdir()
    (logs / "detached-launch.jsonl").write_text("broken", encoding="utf8")
    assert runtime.inspect(cohort.settings)["ready"] is False


def test_authentication_is_still_required(cohort, monkeypatch):
    monkeypatch.setattr(cli, "_health", lambda s: None)
    assert runtime.deployment(cohort.settings, runtime.inspect(cohort.settings))["ready"] is False


@pytest.mark.parametrize("fault", ["receipt_only", "wrong_pin", "inside_state", "expired_trust", "trust_owner",
                                  "trust_nonce", "launcher_changed", "contract_changed", "image_changed",
                                  "foreign_image", "verifier_path", "pid_overflow"])
def test_independent_operator_pin_and_deployed_artifacts_are_required(cohort, monkeypatch, fault):
    trust = cohort.trust
    if fault == "receipt_only": monkeypatch.delenv("LABHQ_CONTAINED_TRUST_PATH")
    if fault == "wrong_pin": monkeypatch.setenv("LABHQ_CONTAINED_TRUST_SHA256", "0" * 64)
    if fault == "expired_trust": trust["expires_at"] = 999
    if fault == "trust_owner": trust["owner"]["created"] = "windows:901"
    if fault == "trust_nonce": trust["nonce"] = "f" * 32
    if fault == "launcher_changed": cohort.launcher.write_text("changed", encoding="utf8")
    if fault == "contract_changed": cohort.launch_contract.write_text("changed", encoding="utf8")
    if fault == "image_changed": cohort.image.write_bytes(b"changed")
    if fault == "foreign_image": cohort.native.identity.return_value = {"created": "windows:900", "image": str(cohort.launcher)}
    if fault == "verifier_path":
        cohort.receipt["provenance"]["runtime_readiness_path"] = str(cohort.launcher)
        trust["provenance"] = copy.deepcopy(cohort.receipt["provenance"])
        cohort.save()
    if fault == "pid_overflow":
        cohort.receipt["owner"]["pid"] += 2 ** 32
        trust["owner"] = copy.deepcopy(cohort.receipt["owner"])
        cohort.save()
    selected = cohort.path.parent / "selected-trust.json" if fault == "inside_state" else cohort.trust_path
    selected.write_text(json.dumps(trust), encoding="utf8")
    if fault not in ("receipt_only", "wrong_pin"):
        monkeypatch.setenv("LABHQ_CONTAINED_TRUST_PATH", str(selected))
        monkeypatch.setenv("LABHQ_CONTAINED_TRUST_SHA256", hashlib.sha256(selected.read_bytes()).hexdigest())
    result = runtime.deployment(cohort.settings, runtime.inspect(cohort.settings))
    assert result["ready"] is False
    assert not cohort.calls  # cannot ask an owner to endorse unpinned evidence


@pytest.mark.parametrize("fault", [None, "foreign_server", "partial_write", "closed", "timeout"])
def test_pipe_transport_uses_kernel_server_identity_and_bounded_nonblocking_io(monkeypatch, fault):
    import ctypes as C
    from ctypes import wintypes as W

    expected = {"fake": "read-only response"}
    payload = json.dumps(expected).encode()
    def peer(handle, pointer):
        C.cast(pointer, C.POINTER(W.ULONG)).contents.value = 99 if fault == "foreign_server" else 77
        return True
    def write(handle, raw, size, pointer, overlapped):
        C.cast(pointer, C.POINTER(W.DWORD)).contents.value = size - (fault == "partial_write")
        return True
    def read(handle, buffer, size, pointer, overlapped):
        if fault in ("closed", "timeout"):
            return False
        C.memmove(buffer, payload, len(payload))
        C.cast(pointer, C.POINTER(W.DWORD)).contents.value = len(payload)
        return True
    native = SimpleNamespace(CreateFileW=Mock(return_value=123), WaitNamedPipeW=Mock(),
        SetNamedPipeHandleState=Mock(return_value=True), GetNamedPipeServerProcessId=Mock(side_effect=peer),
        ReadFile=Mock(side_effect=read), WriteFile=Mock(side_effect=write), CloseHandle=Mock())
    monkeypatch.setattr(C, "WinDLL", lambda *a, **kw: native, raising=False)
    monkeypatch.setattr(C, "get_last_error", lambda: 232 if fault == "timeout" else 109, raising=False)
    ticks = iter(i / 20 for i in range(50))
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    if fault:
        with pytest.raises(ValueError):
            runtime._pipe_request("fake local pipe", 77, {"schema": "fake query"}, .3)
    else:
        assert runtime._pipe_request("fake local pipe", 77, {"schema": "fake query"}, .3) == expected
    native.CloseHandle.assert_called_once_with(123)
    mode = native.SetNamedPipeHandleState.call_args.args[1]
    assert C.cast(mode, C.POINTER(W.DWORD)).contents.value == 3  # message + NOWAIT
    if fault == "foreign_server":
        native.WriteFile.assert_not_called()
