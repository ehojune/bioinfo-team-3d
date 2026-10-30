import subprocess
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
