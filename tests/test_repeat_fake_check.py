"""Contract failures must not become repeatability PASS through normalization."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import repeat_fake_check as check
from scripts import repeat_fake_fixture as fixture


def receipts():
    contract = {"status": "done", "cost_known": True, "cost_usd": 0,
                "task_cost_known": True, "task_cost_usd": 0, "budget_usd": 1.0,
                "timeout_s": fixture.TIMEOUT_S, "mock_marked": True, "evidence_linked": True,
                "external_connections": 0, "child_commands": 0,
                "artifact_sha256": "hash", "outputs": ["outputs/artifact.txt"], "bundle_files": ["report.md"]}
    return [{"contract": deepcopy(contract), "request_id": f"req_{n}", "task_ids": [f"task_{n}"],
             "artifact_identity": [1, n], "created_at": n, "elapsed_s": n,
             "process_exit": 0, "remaining_children": []} for n in (1, 2)]


def test_only_runtime_identity_and_timing_may_differ():
    assert check.compare(receipts())["pass"] is True


@pytest.mark.parametrize("key,value", [
    ("status", "failed"), ("cost_known", False), ("cost_usd", 0.1),
    ("cost_usd", float("nan")), ("task_cost_known", False), ("task_cost_usd", 0.1),
    ("mock_marked", False), ("evidence_linked", False), ("external_connections", 1),
    ("child_commands", 1), ("outputs", []), ("bundle_files", []), ("artifact_sha256", "changed"),
    ("budget_usd", 2.0),
])
def test_contract_regression_fails_even_when_other_run_is_valid(key, value):
    rows = receipts()
    rows[1]["contract"][key] = value
    with pytest.raises(ValueError):
        check.compare(rows)


@pytest.mark.parametrize("key,value", [("elapsed_s", 121), ("elapsed_s", float("nan")),
                                      ("process_exit", 1), ("remaining_children", ["python"])])
def test_per_run_caps_and_cleanup_are_required(key, value):
    rows = receipts()
    rows[1][key] = value
    with pytest.raises(ValueError):
        check.compare(rows)


@pytest.mark.parametrize("key", ["request_id", "task_ids", "artifact_identity"])
def test_previous_execution_reuse_fails(key):
    rows = receipts()
    rows[1][key] = rows[0][key]
    with pytest.raises(ValueError, match="reused"):
        check.compare(rows)


def test_missing_field_is_not_filled_with_success_default():
    rows = receipts()
    del rows[1]["contract"]["cost_known"]
    with pytest.raises(KeyError):
        check.compare(rows)


def test_audit_blocks_non_loopback_and_children():
    audit = fixture.OfflineAudit()
    audit("socket.connect", (None, ("127.0.0.1", 12345)))
    with pytest.raises(OSError):
        audit("socket.connect", (None, ("example.invalid", 443)))
    with pytest.raises(OSError):
        audit("subprocess.Popen", ("codex", [], None, None))
    assert audit.external_attempts == audit.subprocess_attempts == 1


def test_failed_driver_writes_comparison_and_returns_nonzero(tmp_path, monkeypatch):
    output = tmp_path / "failure"

    def fail(source, root):
        root.mkdir()
        raise ValueError("evidence missing")

    monkeypatch.setattr(check, "rehearse", fail)
    assert check.main(["--output", str(output)]) == 1
    assert '"pass": false' in (output / "comparison.json").read_text(encoding="utf-8")


def test_existing_evidence_is_never_overwritten(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_text("previous evidence", encoding="utf-8")
    assert check.main(["--output", str(tmp_path)]) == 1
    assert path.read_text(encoding="utf-8") == "previous evidence"


def test_real_gateway_runner_twice_and_missing_artifact_is_rejected(tmp_path):
    source = Path(__file__).resolve().parents[1]
    rows = []
    for n in (1, 2):
        root = tmp_path / f"run-{n}"
        root.mkdir()
        env = check.install._isolated_env(root, source)
        check.command([sys.executable, str(source / "scripts/repeat_fake_check.py"), "--child", str(root),
                       "--config", str(source / "config/labhq.example.yaml")], source, env, root, "child", 180)
        receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
        boundary = json.loads((root / "boundary.json").read_text(encoding="utf-8"))
        assert boundary == {"external_attempts": 0, "subprocess_attempts": 0, "events": []}
        # The real child has exited, and its audit denied every possible child-command launch.
        receipt.update(process_exit=0, remaining_children=[])
        rows.append(receipt)
    assert check.compare(rows)["pass"] is True
    root = tmp_path / "run-2"
    req = json.loads((root / "request.json").read_text(encoding="utf-8"))
    tasks = json.loads((root / "tasks.json").read_text(encoding="utf-8"))
    observed = {"request": req, "elapsed_s": rows[1]["elapsed_s"], "limits": rows[1]["contract"]["limits"]}
    fixture.collect_receipt(observed, root, tasks, fixture.OfflineAudit())
    artifact = root / rows[1]["workdir"] / "outputs/artifact.txt"
    info = artifact.stat()
    os.utime(artifact, ns=(info.st_atime_ns, 0))
    with pytest.raises(ValueError, match="artifact predates"):
        fixture.collect_receipt(observed, root, tasks, fixture.OfflineAudit())
    os.utime(artifact, ns=(info.st_atime_ns, info.st_mtime_ns))
    artifact.unlink()
    with pytest.raises(ValueError, match="artifact is missing"):
        fixture.collect_receipt(observed, root, tasks, fixture.OfflineAudit())


class _Draws:
    """A stand-in for socket.socket whose bind(0) is given the next scripted port."""

    def __init__(self, ports):
        self.ports = list(ports)
        self.current = None

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def bind(self, address):
        self.current = self.ports.pop(0)

    def getsockname(self):
        return ("127.0.0.1", self.current)


def _configured(ports, monkeypatch, tmp_path):
    from labhq.settings import Settings

    draws = _Draws(ports)
    monkeypatch.setattr(fixture.socket, "socket", draws)
    return fixture.configure(Settings(), tmp_path)


def test_a_reserved_or_repeated_port_draw_is_drawn_again(monkeypatch, tmp_path):
    # 55921 and 8787 are the PI's own ports; the broker must also differ from the gateway (#1076, as #1072).
    settings = _configured([55921, 8787, 40001, 40001, 40002], monkeypatch, tmp_path)
    assert (settings.gateway.port, settings.runner.broker_port) == (40001, 40002)


def test_only_a_run_of_reserved_draws_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="reserved port"):
        _configured([55921] * fixture.PORT_DRAWS + [40002], monkeypatch, tmp_path)
