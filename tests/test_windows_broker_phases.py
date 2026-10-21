"""Fixed provider progress only; no PowerShell, native API or process starts."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import windows_broker as broker
from tests.test_windows_broker import world  # Reuse the fully fake native/ownership boundary.

CREATE = broker._create


PHASES = ["powershell_started", "input_accepted", "create_entered", "create_returned"]


def frames(count):
    return b"".join(json.dumps({"phase": phase, "elapsed_ms": index * 10}).encode() + b"\n"
                    for index, phase in enumerate(PHASES[:count]))


@pytest.fixture
def shell(tmp_path, monkeypatch):
    path = tmp_path / "powershell.exe"
    path.touch()
    monkeypatch.setattr(broker, "_powershell", lambda: path)
    ticks = iter([50.0, 60.0])
    monkeypatch.setattr(broker.time, "monotonic", lambda: next(ticks))
    return path


@pytest.mark.parametrize("count", range(5))
def test_timeout_retains_only_received_phases_and_never_retries(shell, tmp_path, monkeypatch, count):
    failure = subprocess.TimeoutExpired("fake-powershell", 10, output=frames(count))
    run = Mock(side_effect=failure)
    monkeypatch.setattr(broker.subprocess, "run", run)
    diagnostic = {}
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        broker._create([str(tmp_path / "python.exe"), "private-script"], tmp_path,
                       {"PRIVATE": "private-value"}, diagnostics=diagnostic)
    assert caught.value is failure
    assert [row["phase"] for row in diagnostic["phases"]] == PHASES[:count]
    assert diagnostic["elapsed_ms"] == 10000
    assert "private" not in json.dumps(diagnostic)
    run.assert_called_once()
    assert run.call_args.kwargs["timeout"] == 10
    assert run.call_args.kwargs["stderr"] == subprocess.DEVNULL


def test_success_parses_progress_separately_from_returned_pid(shell, tmp_path, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=frames(4) + b'{"code":0,"pid":222}'))
    monkeypatch.setattr(broker.subprocess, "run", run)
    diagnostic = {}
    assert broker._create([str(tmp_path / "python.exe"), "script.py"], tmp_path, {}, diagnostics=diagnostic) == 222
    assert [row["phase"] for row in diagnostic["phases"]] == PHASES
    assert diagnostic["elapsed_ms"] == 10000
    assert diagnostic["returncode"] == 0
    run.assert_called_once()


@pytest.mark.parametrize("count,reply", [(3, b""), (4, b'{"code":0,"pid":222}')])
def test_timeout_journal_keeps_phase_and_duration_without_ack_or_pid(world, shell, monkeypatch, count, reply):
    failure = subprocess.TimeoutExpired("SECRET-command", 10, output=frames(count) + reply,
                                        stderr=b"SECRET-stderr")
    run = Mock(side_effect=failure)
    monkeypatch.setattr(broker.subprocess, "run", run)
    world.create.side_effect = CREATE
    with pytest.raises(broker.BrokerFailure, match="provider_timeout"):
        broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)
    rows = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = rows[-1]
    assert failed["status"] == "wmi_failed" and failed["error"] == "TimeoutExpired"
    assert failed["phase"] == "provider_create"
    assert [row["phase"] for row in failed["provider"]["phases"]] == PHASES[:count]
    assert failed["provider"]["elapsed_ms"] == 10000
    assert "fixture" not in json.dumps(rows)
    assert "SECRET" not in json.dumps(rows)
    world.create.assert_called_once()
    run.assert_called_once()
    world.native.open_process.assert_not_called()
    world.native.k.TerminateProcess.assert_not_called()
    assert not any("child_pid" in row for row in rows)
    assert not list((world.state / ".launch").iterdir())


@pytest.mark.parametrize("returncode", [0, 7])
def test_malformed_provider_reply_has_safe_class_phase_and_returncode(world, shell, monkeypatch, returncode):
    run = Mock(return_value=SimpleNamespace(returncode=returncode, stdout=frames(1) + b'SECRET-not-json',
                                            stderr=b'SECRET-stderr'))
    monkeypatch.setattr(broker.subprocess, "run", run)
    world.create.side_effect = CREATE
    with pytest.raises(broker.BrokerFailure, match="provider_protocol") as caught:
        broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)
    assert isinstance(caught.value.__cause__, ValueError)
    rows = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = rows[-1]
    assert failed["phase"] == "provider_create" and failed["error"] == "ValueError"
    assert failed["provider"]["returncode"] == returncode
    assert failed["provider"]["trace_status"] == "invalid_or_partial"
    assert "SECRET" not in json.dumps(rows)
    run.assert_called_once()
    world.create.assert_called_once()
    world.native.open_process.assert_not_called()
    world.native.k.TerminateProcess.assert_not_called()
    assert not list((world.state / ".launch").iterdir())


@pytest.mark.parametrize("where", ["provider_create", "process_open"])
def test_native_failure_keeps_errno_and_phase_without_message_or_ack(world, where):
    failure = OSError(5, "SECRET-native-message")
    target = world.create if where == "provider_create" else world.native.open_process
    target.side_effect = failure
    with pytest.raises(broker.BrokerFailure, match="native_error") as caught:
        broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)
    assert caught.value.__cause__ is failure and caught.value.code == 5
    rows = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["phase"] == where and rows[-1]["error"] == "OSError"
    assert rows[-1]["code"] == 5 and "SECRET" not in json.dumps(rows)
    world.create.assert_called_once()
    world.native.k.TerminateProcess.assert_not_called()
    assert not list((world.state / ".launch").iterdir())


@pytest.mark.parametrize("suffix", [
    b'{"phase":"private-command","elapsed_ms":2}\n',
    b'{"phase":"input_accepted","elapsed_ms":"private-value"}\n',
    b'{"phase":"input_accepted","elapsed_ms":NaN}\n',
    b'{"phase":"input_accepted","elapsed_ms":-1}\n',
    b'{"phase":"input_accepted","elapsed_ms":true}\n',
    b'{"phase":"input_accepted","elapsed_ms":2,"env":"private-value"}\n',
    b'{"phase":"create_returned","elapsed_ms":2}\n',
    b'{"phase":',
    b'x' * 9000,
], ids=["unknown-phase", "text-time", "nan", "negative", "bool", "extra-field", "out-of-order", "truncated", "oversize"])
def test_untrusted_partial_output_is_not_logged_or_accepted(suffix):
    raw = frames(1) + suffix
    diagnostic = {}
    broker._provider_output(raw, diagnostic, complete=False)
    assert "private" not in json.dumps(diagnostic) and len(json.dumps(diagnostic)) < 1000
    with pytest.raises(ValueError, match="provider protocol"):
        broker._provider_output(raw + b'{"code":0,"pid":222}', {}, complete=True)


def test_script_flushes_fixed_phases_around_input_and_single_create():
    script = broker.WMI_SCRIPT
    offsets = [script.index("Write-Phase '" + name + "'") for name in PHASES]
    assert offsets == sorted(offsets)
    assert offsets[0] < script.index("ReadToEnd()") < offsets[1]
    assert offsets[2] < script.index(".Create($r.command") < offsets[3]
    assert script.count(".Create($r.command") == 1
    assert "[Console]::Out.Flush()" in script and "Stopwatch" in script
