import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from labhq.runner import process_boundary as boundary


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
        receipt = boundary.diagnostic_broker_probe(factory, on_ready=ready)
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "factory", "start", "ready", "child", "close"]
    assert events[0][1] == events[2][1] == receipt.run_id
    assert "/run/labhq/provider.sock" in events[3][1]
    assert receipt.launch == "exited_0" and receipt.seal_status == "unsealed"
    assert receipt.cleanup == "broker_closed_socket_absent; descendants_unverified"


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


def test_digest_mismatch_and_cleanup_failure_are_reported():
    stager = SimpleNamespace(stage=lambda run_id, payloads: {},
                             cleanup=lambda run_id: (_ for _ in ()).throw(OSError()))
    receipt = boundary.diagnostic_staging_probe(stager)
    assert receipt.preparation == "unsupported" and receipt.launch == "not_started"
    assert receipt.cleanup.startswith("failed") and receipt.seal_status == "unsealed"
    assert "diagnostic staging cleanup failed" in receipt.unsupported_reasons
