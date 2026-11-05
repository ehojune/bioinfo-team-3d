"""Fixed provider progress only; no PowerShell, native API or process starts."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import windows_broker as broker
from tests.test_windows_broker import world  # Reuse the fully fake native/ownership boundary.
from tests.support.time_proxy import RuntimeProxy

CREATE = broker._create


PHASES = ["powershell_started", "stdin_open", "header_read", "input_read", "input_accepted", "create_entered", "create_returned"]


def frames(count):
    return b"".join(json.dumps({"phase": phase, "elapsed_ms": index * 10}).encode() + b"\n"
                    for index, phase in enumerate(PHASES[:count]))


@pytest.fixture
def shell(tmp_path, monkeypatch):
    path = tmp_path / "powershell.exe"
    path.touch()
    monkeypatch.setattr(broker, "_powershell", lambda: path)
    ticks = iter([50.0, 60.0])
    monkeypatch.setattr(broker, "time", RuntimeProxy(broker.time, monotonic=lambda: next(ticks)))
    return path


@pytest.mark.parametrize("count", range(len(PHASES) + 1))
def test_timeout_retains_only_received_phases_and_never_retries(shell, tmp_path, monkeypatch, count):
    failure = subprocess.TimeoutExpired("fake-powershell", broker.PROVIDER_TIMEOUT, output=frames(count))
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
    assert run.call_args.kwargs["timeout"] == broker.PROVIDER_TIMEOUT
    assert run.call_args.kwargs["stderr"] == subprocess.DEVNULL


def test_success_parses_progress_separately_from_returned_pid(shell, tmp_path, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=frames(len(PHASES)) + b'{"code":0,"pid":222}'))
    monkeypatch.setattr(broker.subprocess, "run", run)
    diagnostic = {}
    assert broker._create([str(tmp_path / "python.exe"), "script.py"], tmp_path, {}, diagnostics=diagnostic) == 222
    assert [row["phase"] for row in diagnostic["phases"]] == PHASES
    assert diagnostic["elapsed_ms"] == 10000
    assert diagnostic["returncode"] == 0
    run.assert_called_once()


@pytest.mark.parametrize("count,reply", [(count, b'{"code":0,"pid":222}' if count == len(PHASES) else b"") for count in range(1,len(PHASES)+1)])
def test_timeout_journal_keeps_phase_and_duration_without_ack_or_pid(world, shell, monkeypatch, count, reply):
    failure = subprocess.TimeoutExpired("SECRET-command", broker.PROVIDER_TIMEOUT, output=frames(count) + reply,
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
    b'{"phase":"stdin_open","elapsed_ms":"private-value"}\n',
    b'{"phase":"stdin_open","elapsed_ms":NaN}\n',
    b'{"phase":"stdin_open","elapsed_ms":-1}\n',
    b'{"phase":"stdin_open","elapsed_ms":true}\n',
    b'{"phase":"stdin_open","elapsed_ms":2,"env":"private-value"}\n',
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


FIRST_PHASE = """[Console]::Out.WriteLine('{"phase":"powershell_started","elapsed_ms":0}')"""


def test_script_flushes_fixed_phases_around_input_and_single_create():
    script = broker.WMI_SCRIPT
    offsets = [script.index(FIRST_PHASE)] + [script.index("Write-Phase '" + name + "'") for name in PHASES[1:]]
    assert offsets == sorted(offsets)
    assert offsets[0] < script.index("OpenStandardInput()") < offsets[1] < script.index("ReadUInt32()") < offsets[2]
    assert offsets[2] < script.index("ReadBytes(") < offsets[3] < script.index("ConvertFrom-Json") < offsets[4]
    assert offsets[4] < script.index("CreateInstance()") < offsets[5] < script.index(".Create($r.command") < offsets[6]
    assert script.count(".Create($r.command") == 1
    assert "[Console]::Out.Flush()" in script and "Stopwatch" in script


def test_full_phase_reply_fits_the_unchanged_byte_bound():
    raw = frames(len(PHASES)) + b'{"code":0,"pid":222}'
    diagnostic = {}
    assert broker._provider_output(raw + b' ' * (8192 - len(raw)), diagnostic, complete=True) == {"code": 0, "pid": 222}
    assert diagnostic["trace_status"] == "phases_complete"
    with pytest.raises(ValueError, match="provider protocol"):
        broker._provider_output(raw + b' ' * (8193 - len(raw)), {}, complete=True)


@pytest.mark.parametrize("raw", [
    frames(len(PHASES)),  # progress alone is never a complete result
    frames(1) + b'{"phase":"stdin_open",',
    frames(2) + json.dumps({"phase":"stdin_open", "elapsed_ms":20}).encode(),
    frames(1) + json.dumps({"phase":"input_accepted", "elapsed_ms":20}).encode(),
    frames(len(PHASES)) + b'{"code":0,"pid":222}\n{"code":0,"pid":222}',
], ids=["no-reply", "partial-frame", "duplicate-phase", "missing-read-phase", "excess-frame"])
def test_split_protocol_rejects_incomplete_mismatched_and_extra_frames(raw):
    diagnostic = {}
    with pytest.raises(ValueError, match="provider protocol"):
        broker._provider_output(raw, diagnostic, complete=True)
    assert diagnostic["trace_status"] == "invalid_or_partial"


def test_the_first_phase_is_a_flushed_literal_that_needs_no_json_conversion():
    """#844 clean-install: no phase at all reached stdout in 10 s. The first line no longer waits for ConvertTo-Json."""
    lines = [line.strip() for line in broker.WMI_SCRIPT.splitlines()]
    first = lines.index("try {") + 1
    assert lines[first] == FIRST_PHASE and lines[first + 1] == "[Console]::Out.Flush()"
    assert "Write-Phase 'powershell_started'" not in broker.WMI_SCRIPT
    literal = FIRST_PHASE.split("'")[1]  # a single-quoted PowerShell string is written exactly as is
    assert "ConvertTo-Json" not in FIRST_PHASE and json.loads(literal) == {"phase": "powershell_started", "elapsed_ms": 0}
    diagnostic = {}
    later = json.dumps({"phase": "stdin_open", "elapsed_ms": 0.4}).encode()
    assert broker._provider_output(literal.encode() + b"\n" + later + b"\n", diagnostic, complete=False) is None
    assert diagnostic["phases"] == [{"phase": "powershell_started", "elapsed_ms": 0}, {"phase": "stdin_open", "elapsed_ms": 0.4}]
    assert diagnostic["trace_status"] == "partial"


@pytest.mark.parametrize("output,stdout,size,status", [
    (None, "missing", None, "absent"),
    (b"", "bytes", 0, "absent"),
    (frames(1), "bytes", len(frames(1)), "partial"),
    (b"SECRET-not-json", "bytes", 15, "invalid_or_partial"),
    (b"x" * 9000, "bytes", 9000, "invalid_or_partial"),
    ("SECRET-text", "invalid_type", None, "invalid_or_partial"),
], ids=["none", "empty", "phase", "not-json", "oversize", "not-bytes"])
def test_stdout_is_described_by_kind_and_size_only(output, stdout, size, status):
    diagnostic = {}
    assert broker._provider_output(output, diagnostic, complete=False) is None
    assert diagnostic["stdout"] == stdout and diagnostic.get("stdout_bytes") == size
    assert diagnostic["trace_status"] == status and "SECRET" not in json.dumps(diagnostic)
    with pytest.raises(ValueError, match="provider protocol"):  # no complete reply in any of these
        broker._provider_output(output, {}, complete=True)


@pytest.mark.parametrize("output", [None, b"", frames(len(PHASES)) + b'{"code":0,"pid":222}'], ids=["none", "empty", "full"])
def test_a_timed_out_provider_journals_its_stdout_kind_but_never_its_pid(world, shell, monkeypatch, output):
    run = Mock(side_effect=subprocess.TimeoutExpired("SECRET-command", broker.PROVIDER_TIMEOUT, output=output))
    monkeypatch.setattr(broker.subprocess, "run", run)
    world.create.side_effect = CREATE
    with pytest.raises(broker.BrokerFailure, match="provider_timeout"):
        broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)
    rows = [json.loads(line) for line in (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8").splitlines()]
    provider = rows[-1]["provider"]
    assert provider["stdout"] == ("missing" if output is None else "bytes")
    assert provider.get("stdout_bytes") == (None if output is None else len(output))
    assert "pid" not in json.dumps(provider) and "SECRET" not in json.dumps(rows)  # partial output proves no PID
    assert not any("child_pid" in row for row in rows)
    assert "denied_reads" not in rows[-1]  # counted only for the receipt wait
    run.assert_called_once()
    world.native.open_process.assert_not_called()
    world.native.k.TerminateProcess.assert_not_called()
