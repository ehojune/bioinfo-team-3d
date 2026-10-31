import json
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from labhq.runner import process_boundary as boundary


@pytest.mark.parametrize("case", ["missing", "timeout", "child_failure", "cleanup_failure", "success"])
def test_cli_diagnostic_json_and_exit(case, capsys):
    result = subprocess.CompletedProcess([], 1 if case == "child_failure" else 0)
    if case == "timeout":
        result = subprocess.TimeoutExpired("bwrap", 1)
    with patch.object(boundary.os, "name", "posix"), \
         patch.object(boundary.os.path, "isdir", return_value=True), \
         patch.object(boundary.shutil, "which", return_value=None if case == "missing" else "bwrap"), \
         patch.object(boundary.subprocess, "run", side_effect=result if case == "timeout" else None,
                      return_value=result):
        if case == "cleanup_failure":
            actual_probe = boundary.diagnostic_probe
            def failed_cleanup():
                receipt = actual_probe()
                receipt.cleanup = "failed; descendants_unverified"
                return receipt
            with patch.object(boundary, "diagnostic_probe", side_effect=failed_cleanup):
                code = boundary.main(["--unsealed-diagnostic"])
        else:
            code = boundary.main(["--unsealed-diagnostic"])
    receipt = json.loads(capsys.readouterr().out)
    assert code == (0 if case == "success" else 1)
    assert receipt["seal_status"] == "unsealed"
    assert receipt["launch"] == ("not_started" if case == "missing" else
                                 "failed" if case in ("timeout", "child_failure") else "exited_0")
    if case == "cleanup_failure":
        assert receipt["cleanup"].startswith("failed")


def test_cli_default_refusal_exits_nonzero_and_prints_json():
    result = subprocess.run([sys.executable, "-m", "labhq.runner.process_boundary"],
                            capture_output=True, text=True, check=False)
    receipt = json.loads(result.stdout)
    assert result.returncode != 0
    assert receipt["preparation"] == "unsupported"
    assert receipt["launch"] == "not_started"


def test_protected_launch_rejects_caller_attestations():
    receipt = boundary.protected_launch(supervisor=True, snapshot={"sealed": True, "digest": "abc"})
    assert receipt.seal_status == "unsupported"
    assert receipt.launch == "not_started"
    assert receipt.snapshot_digest is None
    assert receipt.unsupported_reasons


def test_protected_launch_never_spawns():
    with patch.object(boundary.subprocess, "run", side_effect=AssertionError("spawned")):
        boundary.protected_launch()


def test_diagnostic_missing_bwrap_is_unsealed():
    with patch.object(boundary.shutil, "which", return_value=None):
        receipt = boundary.diagnostic_probe()
    assert receipt.launch == "not_started"
    assert receipt.seal_status == "unsealed"
    assert receipt.unsupported_reasons


def test_diagnostic_fixed_command_and_receipt():
    with patch.object(boundary.os, "name", "posix"), \
         patch.object(boundary.os.path, "isdir", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="/usr/bin/bwrap"), \
         patch.object(boundary.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
        receipt = boundary.diagnostic_probe()
    args, kwargs = run.call_args
    assert args[0][-1] == "/usr/bin/true"
    assert "--unshare-all" in args[0] and "--clearenv" in args[0]
    assert kwargs["close_fds"] is True
    assert receipt.launch == "exited_0" and receipt.seal_status == "unsealed"
    assert receipt.cleanup.endswith("descendants_unverified")
    assert receipt.to_dict()["run_id"] == receipt.run_id


def test_diagnostic_timeout_never_claims_success():
    with patch.object(boundary.os, "name", "posix"), \
         patch.object(boundary.os.path, "isdir", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="/usr/bin/bwrap"), \
         patch.object(boundary.subprocess, "run", side_effect=subprocess.TimeoutExpired("bwrap", 1)):
        receipt = boundary.diagnostic_probe(timeout=1)
    assert receipt.launch == "failed"
    assert receipt.seal_status == "unsealed"


def test_broker_ready_before_child_and_closed_after():
    events = []

    class Broker:
        def start(self):
            events.append("start")

        def close(self):
            events.append("close")

    def factory(directory, run_id):
        events.append(("factory", run_id))
        return Broker()

    def ready(run_id, path):
        events.append(("ready", run_id, path.name))

    def run(command, **kwargs):
        events.append(("child", command))
        return subprocess.CompletedProcess(command, 0)

    with patch.object(boundary, "_broker_prerequisites", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="bwrap"), \
         patch.object(boundary.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140000)), \
         patch.object(boundary.subprocess, "run", side_effect=run):
        receipt = boundary.diagnostic_broker_probe(factory, on_ready=ready,
            run_id="12345678-1234-4234-8234-123456789abc")
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "factory", "start", "ready", "child", "close"]
    assert events[0][1] == events[2][1] == receipt.run_id
    assert receipt.run_id == "12345678-1234-4234-8234-123456789abc"
    assert events[3][1][-1] == receipt.run_id
    assert "/run/labhq/provider.sock" in events[3][1]
    assert receipt.launch == "exited_0" and receipt.seal_status == "unsealed"
    assert receipt.cleanup == "broker_closed_socket_absent; descendants_unverified"


@pytest.mark.parametrize("run_id", ["", "not-a-uuid", "12345678123442348234123456789abc",
                                    "12345678-1234-4234-8234-123456789ABC", 42, True])
def test_broker_rejects_invalid_run_id_before_start(run_id):
    with patch.object(boundary.shutil, "which", side_effect=AssertionError("started")):
        with pytest.raises(ValueError, match="canonical UUID"):
            boundary.diagnostic_broker_probe(lambda *_: None, run_id=run_id)


def test_broker_generates_uuid_when_run_id_omitted():
    with patch.object(boundary.shutil, "which", return_value=None):
        first = boundary.diagnostic_broker_probe(lambda *_: None)
        second = boundary.diagnostic_broker_probe(lambda *_: None)
    assert str(boundary.uuid.UUID(first.run_id)) == first.run_id
    assert first.run_id != second.run_id


def test_broker_ready_failure_prevents_child_and_closes():
    broker = SimpleNamespace(start=lambda: None, close=lambda: closed.append(True))
    closed = []
    with patch.object(boundary, "_broker_prerequisites", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="bwrap"), \
         patch.object(boundary.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140000)), \
         patch.object(boundary.subprocess, "run", side_effect=AssertionError("spawned")):
        receipt = boundary.diagnostic_broker_probe(
            lambda directory, run_id: broker,
            on_ready=lambda run_id, path: (_ for _ in ()).throw(ValueError()))
    assert closed == [True]
    assert receipt.launch == "not_started"
    assert receipt.unsupported_reasons


def test_broker_close_failure_is_reported():
    def fail_close():
        raise RuntimeError("close failed")

    broker = SimpleNamespace(start=lambda: None, close=fail_close)
    with patch.object(boundary, "_broker_prerequisites", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="bwrap"), \
         patch.object(boundary.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140000)), \
         patch.object(boundary.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
        receipt = boundary.diagnostic_broker_probe(lambda directory, run_id: broker)
    assert receipt.cleanup.startswith("failed")
    assert "broker cleanup failed" in receipt.unsupported_reasons


def test_synthetic_staging_cleans_up_and_never_launches():
    stager = boundary.DiagnosticStager()
    seen = []
    def observe(run_id):
        directory = stager._runs[run_id]
        assert (directory / "snapshot").read_bytes() == b"synthetic input"
        seen.append(directory)
    with patch.object(boundary.subprocess, "run", side_effect=AssertionError("spawned")):
        receipt = boundary.diagnostic_staging_probe(stager, on_staged=observe)
    assert receipt.preparation == "diagnostic_staged"
    assert receipt.launch == "not_started" and receipt.seal_status == "unsealed"
    assert seen and not seen[0].exists() and not stager._runs


def test_partial_stage_and_callback_failure_cleanup():
    for fail_at in ("stage", "callback"):
        stager = boundary.DiagnosticStager()
        original = stager.stage
        def stage(run_id, payloads):
            result = original(run_id, payloads)
            if fail_at == "stage":
                raise OSError("partial failure")
            return result
        def callback(run_id):
            if fail_at == "callback":
                raise ValueError("observer failed")
        stager.stage = stage
        receipt = boundary.diagnostic_staging_probe(stager, on_staged=callback)
        assert receipt.preparation == "unsupported" and not stager._runs
        assert "diagnostic staging failed" in receipt.unsupported_reasons


@pytest.mark.parametrize("name", ["snapshot", "runtime", "policy"])
@pytest.mark.parametrize("action", ["mutate", "remove"])
def test_payload_change_after_callback_fails_and_cleans_up(name, action):
    stager = boundary.DiagnosticStager()
    directories = []
    def change(run_id):
        directory = stager._runs[run_id]
        directories.append(directory)
        payload = directory / name
        if action == "mutate":
            payload.write_bytes(b"changed")
        else:
            payload.unlink()
    with patch.object(boundary.subprocess, "run", side_effect=AssertionError("spawned")):
        receipt = boundary.diagnostic_staging_probe(stager, on_staged=change)
    assert receipt.preparation == "unsupported"
    assert receipt.launch == "not_started" and receipt.seal_status == "unsealed"
    assert "diagnostic staging failed" in receipt.unsupported_reasons
    assert receipt.cleanup.startswith("stager_cleanup_returned")
    assert not directories[0].exists() and not stager._runs


def test_digest_mismatch_and_cleanup_failure_are_reported():
    stager = SimpleNamespace(stage=lambda run_id, payloads: {},
                             cleanup=lambda run_id: (_ for _ in ()).throw(OSError()))
    receipt = boundary.diagnostic_staging_probe(stager)
    assert receipt.preparation == "unsupported" and receipt.launch == "not_started"
    assert receipt.cleanup.startswith("failed") and receipt.seal_status == "unsealed"
    assert "diagnostic staging cleanup failed" in receipt.unsupported_reasons


def test_diagnostic_ancestry_samples_are_explicitly_untrusted():
    receipt = boundary.diagnostic_staging_probe(boundary.DiagnosticStager())
    observations = receipt.ancestry_observations
    assert [o["phase"] for o in observations] == ["staged", "after_callback"]
    assert all(o["provenance"] == "stager_untrusted" for o in observations)
    pins = observations[0]["objects"]
    assert boundary.validate_synthetic_ancestry(pins, [o["objects"] for o in observations])
    assert receipt.seal_status == "unsealed"
    assert receipt.to_dict()["version"] == 2
    assert receipt.to_dict()["ancestry_observations"] == observations
    assert "ancestry_observations" not in boundary.protected_launch().to_dict()
    assert "ancestry_observations" not in boundary.diagnostic_probe().to_dict()


def test_ancestry_rejects_changed_edges_identities_and_missing_phases():
    observations = boundary.diagnostic_staging_probe(boundary.DiagnosticStager()).ancestry_observations
    pins = observations[0]["objects"]
    assert not boundary.validate_synthetic_ancestry(pins, [])
    for role in pins:
        changed = deepcopy(pins)
        changed[role]["object_id"] += ":replacement"
        assert not boundary.validate_synthetic_ancestry(pins, [pins, changed, pins])
        changed = deepcopy(pins)
        changed[role]["parent_ref"] = role
        assert not boundary.validate_synthetic_ancestry(pins, [changed])
        changed = deepcopy(pins)
        del changed[role]
        assert not boundary.validate_synthetic_ancestry(pins, [changed])
    changed = deepcopy(pins)
    changed["source:tool.py"]["parent_identity"]["object_id"] += ":replacement"
    assert not boundary.validate_synthetic_ancestry(pins, [changed])
    changed = deepcopy(pins)
    changed["parent:source"]["parent_ref"] = "source:tool.py"
    assert not boundary.validate_synthetic_ancestry(changed, [changed])
    changed = deepcopy(pins)
    changed["parent:source"]["kind"] = "file"
    assert not boundary.validate_synthetic_ancestry(changed, [changed])
    changed = deepcopy(pins)
    changed["extra"] = deepcopy(pins["parent:root"])
    assert not boundary.validate_synthetic_ancestry(pins, [changed])


def test_deleted_leaf_is_not_recreated_by_observer():
    stager = boundary.DiagnosticStager()
    def remove_leaf(run_id):
        (stager._runs[run_id] / "source" / "tool.py").unlink()
    receipt = boundary.diagnostic_staging_probe(stager, on_staged=remove_leaf)
    assert receipt.preparation == "unsupported"
    assert len(receipt.ancestry_observations) == 1
    assert receipt.seal_status == "unsealed"


def test_missing_ancestry_observer_fails_and_cleans_up():
    actual = boundary.DiagnosticStager()
    stager = SimpleNamespace(stage=actual.stage, cleanup=actual.cleanup,
                             observe_payload_digests=actual.observe_payload_digests)
    receipt = boundary.diagnostic_staging_probe(stager)
    assert receipt.preparation == "unsupported"
    assert receipt.ancestry_observations == []
    assert "diagnostic staging failed" in receipt.unsupported_reasons
    assert not actual._runs


@pytest.mark.parametrize("supervisor,snapshot", [
    (None, None),
    (SimpleNamespace(identity="trusted", verified=True), {"sealed": True, "digest": "forged"}),
])
def test_missing_or_forged_authority_refused_without_spawn(supervisor, snapshot):
    with patch.object(boundary.subprocess, "run", side_effect=AssertionError("spawned")) as run:
        receipt = boundary.protected_launch(supervisor=supervisor, snapshot=snapshot)
    run.assert_not_called()
    assert (receipt.preparation, receipt.launch, receipt.seal_status) == (
        "unsupported", "not_started", "unsupported")
    assert receipt.unsupported_reasons == ["trusted supervisor and sealed storage verification unavailable"]


@pytest.mark.parametrize("failure", ["start", "timeout", "child_failure", "close"])
def test_broker_failure_paths_report_and_do_not_claim_cleanup(failure):
    events = []
    def start():
        events.append("start")
        if failure == "start":
            raise OSError("synthetic start failure")
    def close():
        events.append("close")
        if failure == "close":
            raise OSError("synthetic close failure")
    def run(command, **kwargs):
        events.append("run")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 1 if failure == "child_failure" else 0)
    with patch.object(boundary, "_broker_prerequisites", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="bwrap"), \
         patch.object(boundary.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140000)), \
         patch.object(boundary.subprocess, "run", side_effect=run):
        receipt = boundary.diagnostic_broker_probe(
            lambda directory, run_id: SimpleNamespace(start=start, close=close), timeout=1)
    assert events[-1] == "close"
    assert receipt.seal_status == "unsealed"
    assert "descendants_unverified" in receipt.cleanup
    if failure == "start":
        assert "run" not in events and receipt.launch == "not_started"
        assert "broker diagnostic preparation or launch failed" in receipt.unsupported_reasons
    elif failure == "timeout":
        assert receipt.launch == "failed"
        assert "BROKER_TIMEOUT" in receipt.unsupported_reasons
    elif failure == "child_failure":
        assert receipt.launch == "failed"
        assert "broker namespace diagnostic failed" in receipt.unsupported_reasons
    else:
        assert receipt.cleanup.startswith("failed")
        assert "broker cleanup failed" in receipt.unsupported_reasons


@pytest.mark.xfail(strict=True, reason="no authenticated ancestry witness or reason-code interface")
def test_forged_ancestry_witness_requires_reason_coded_refusal():
    actual = boundary.DiagnosticStager()
    stager = SimpleNamespace(stage=actual.stage, cleanup=actual.cleanup,
                             observe_payload_digests=actual.observe_payload_digests,
                             observe_ancestry=lambda run_id: {
                                 **actual.observe_ancestry(run_id),
                             })
    receipt = boundary.diagnostic_staging_probe(stager)
    assert receipt.preparation == "unsupported"
    assert "ANCESTRY_WITNESS_UNAUTHENTICATED" in receipt.unsupported_reasons


def test_broker_timeout_requires_distinct_reason_code():
    broker = SimpleNamespace(start=lambda: None, close=lambda: None)
    with patch.object(boundary, "_broker_prerequisites", return_value=True), \
         patch.object(boundary.shutil, "which", return_value="bwrap"), \
         patch.object(boundary.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140000)), \
         patch.object(boundary.subprocess, "run", side_effect=subprocess.TimeoutExpired("bwrap", 1)):
        receipt = boundary.diagnostic_broker_probe(lambda directory, run_id: broker, timeout=1)
    assert receipt.launch == "failed"
    assert "BROKER_TIMEOUT" in receipt.unsupported_reasons
    assert "broker diagnostic preparation or launch failed" not in receipt.unsupported_reasons
    assert receipt.cleanup == "broker_closed_socket_absent; descendants_unverified"
