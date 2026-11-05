"""P1 regressions with fake handles, authenticated health and task-local history only."""
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest

from labhq import cli, instance_guard, runtime_readiness as runtime, windows_launch as win, windows_broker as broker
from labhq.settings import Settings
from tests.test_windows_broker import world, launch


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                    "runner": {"state_dir": str(tmp_path / "state")}})


@pytest.mark.parametrize("member", [False, True, None])
def test_post_spawn_journal_failure_reaps_owned_child(settings, monkeypatch, member):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win, "current_job", lambda: {})
    monkeypatch.setattr(win, "child_job_membership", lambda *a, **kw: member)
    def journal(state, role, status, **facts):
        if status != "attempt":
            raise OSError("fake journal full")
    monkeypatch.setattr(win, "outcome", journal)
    fallback = Mock(side_effect=AssertionError("no broker after a failed journal"))
    monkeypatch.setattr(broker, "launch", fallback)
    child = SimpleNamespace(pid=123, _handle=456, kill=Mock(), wait=Mock(return_value=0))
    with pytest.raises(OSError):
        win.spawn([], state=cli._launch_state(settings), role="gateway-supervisor", popen=Mock(return_value=child))
    child.kill.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    fallback.assert_not_called()
    assert not cli._pid_file(settings, "gateway-supervisor").exists()


@pytest.mark.parametrize("member", [False, True, None])
def test_broker_journal_failure_reaps_only_proven_owned_bootstrap(world, monkeypatch, member):
    monkeypatch.setattr(broker, "job_membership", lambda *a, **kw: member)
    def journal(state, role, status, **facts):
        if status != "wmi_attempt":
            raise OSError("fake journal full")
    monkeypatch.setattr(broker, "outcome", journal)
    with pytest.raises(OSError):
        launch(world)
    world.native.k.TerminateProcess.assert_called_once_with(2, 1)
    world.native.k.CloseHandle.assert_called_once_with(2)
    assert not (world.control.parent / "ack.json").exists()


def test_broker_journal_failure_does_not_authorize_unknown_identity_cleanup(world, monkeypatch):
    world.child["owner"] = "S-1-5-21-foreign"
    def journal(state, role, status, **facts):
        if status == "wmi_failed":
            raise OSError("fake journal full")
    monkeypatch.setattr(broker, "outcome", journal)
    with pytest.raises(OSError):
        launch(world)
    world.native.k.TerminateProcess.assert_not_called()
    world.native.k.CloseHandle.assert_called_once_with(2)
    assert not (world.control.parent / "ack.json").exists()


def authenticate(settings, monkeypatch, valid=True, connected=True):
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id] if connected else []})
    check = create_autospec(instance_guard.check_health, return_value=([], valid))
    monkeypatch.setattr(instance_guard, "check_health", check)
    return check


@pytest.mark.parametrize("stopped", [False, True])
@pytest.mark.parametrize("valid,connected", [(True, True), (False, True), (True, False)])
def test_clean_old_history_needs_current_authenticated_gateway_and_runner(settings, monkeypatch, stopped, valid, connected):
    monkeypatch.setattr(cli, "_process_record", lambda *a, **kw: ("missing", None, None))
    root = cli._launch_state(settings)
    for role in ("gateway", "runner"):
        win.outcome(root, role + "-supervisor", "recorded")
        if stopped:
            cli._stopping_file(settings, role).touch()
    local = runtime.inspect(settings)
    assert local["ready"] is False  # Local missing/down evidence alone still cannot establish readiness.
    check = authenticate(settings, monkeypatch, valid, connected)
    result = runtime.deployment(settings, local)
    assert result["ready"] is (valid and connected)
    check.assert_called_once_with(settings, {"runners": [settings.runner.id] if connected else []}, started_here=False)


@pytest.mark.parametrize("kind", ["unknown", "stale"])
def test_authentication_cannot_override_current_bad_record(settings, monkeypatch, kind):
    monkeypatch.setattr(cli, "_process_record", lambda *a, **kw: (kind, {"pid": 123}, None))
    authenticate(settings, monkeypatch)
    assert runtime.deployment(settings, runtime.inspect(settings))["ready"] is False


def test_authentication_cannot_override_unreadable_history(settings, monkeypatch):
    monkeypatch.setattr(cli, "_process_record", lambda *a, **kw: ("missing", None, None))
    journal = cli._launch_state(settings) / "logs/detached-launch.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("not json\n", encoding="utf-8")
    authenticate(settings, monkeypatch)
    result = runtime.deployment(settings, runtime.inspect(settings))
    assert not result["ready"] and any("history unknown" in reason for reason in result["reasons"])


@pytest.mark.parametrize("members", [(False, False, False, False), (True, False, False, False),
                                     (False, None, False, False), (False, False, True, False),
                                     (False, False, False, None)])
def test_windows_running_requires_current_job_proof_for_role_and_supervisor(settings, monkeypatch, members):
    monkeypatch.setattr(win.sys, "platform", "win32")
    names = ("gateway", "gateway-supervisor", "runner", "runner-supervisor")
    records = {name: {"pid": i + 10, "created": f"windows:{i + 100}"} for i, name in enumerate(names)}
    for name in names:
        win.outcome(cli._launch_state(settings), name, "job_verified", child_in_job=False, child_pid=records[name]["pid"])
    monkeypatch.setattr(cli, "_process_record", lambda s, role, **kw: ("alive", records[role], None))
    proof = Mock(side_effect=lambda record: members[record["pid"] - 10])
    monkeypatch.setattr(win, "record_job_membership", proof, raising=False)
    authenticate(settings, monkeypatch)
    result = runtime.deployment(settings, runtime.inspect(settings))
    assert result["ready"] is all(member is False for member in members)
    assert proof.call_count == 4


@pytest.mark.parametrize("mode,expected", [("clear", False), ("member", True), ("unknown", None),
    ("reused", None), ("gone", None), ("denied", None), ("exited", None)])
def test_readonly_job_proof_pins_creation_identity_before_query(monkeypatch, mode, expected):
    identity = {"created": "windows:21" if mode == "reused" else "windows:20"}
    native = SimpleNamespace(open_process=Mock(return_value=2), identity=Mock(return_value=identity),
        alive=Mock(return_value=mode != "exited"), k=SimpleNamespace(CloseHandle=Mock()))
    if mode in ("gone", "denied"):
        native.open_process.side_effect = OSError("fake unavailable")
    monkeypatch.setattr(broker, "Native", lambda: native)
    query = Mock(return_value={"clear": False, "member": True}.get(mode))
    monkeypatch.setattr(win, "job_membership", query)
    assert win.record_job_membership({"pid": 222, "created": "windows:20"}) is expected
    native.open_process.assert_called_once_with(222, terminate=False)
    if mode in ("gone", "denied"):
        native.k.CloseHandle.assert_not_called()
    else:
        native.k.CloseHandle.assert_called_once_with(2)
    if mode in ("reused", "gone", "denied", "exited"):
        query.assert_not_called()
    else:
        query.assert_called_once_with(2)
