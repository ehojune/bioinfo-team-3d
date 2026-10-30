import os
import subprocess

from scripts.boundary_probe import probe
from scripts.boundary_probe import integration_probe


def test_unisolated_process_exposes_fixtures():
    result = probe.run([])
    assert result["cases"]["allowed"]["status"] == "pass"
    for case in ("read", "write", "env", "socket"):
        assert result["cases"][case]["status"] == "fail"
    assert result["cases"]["fd"]["status"] == ("fail" if os.name == "posix" else "unsupported")
    assert result["protection_verified"] is False
    assert "fixture-only" not in str(result["cases"])
    assert result["run_id"]
    assert result["cases"]["external_writer"] == {"status": "fail", "host_witness": True}
    assert result["cases"]["parent_replace"]["status"] in ("fail", "inconclusive")
    assert result["cases"]["detached_child"] == {"status": "fail", "host_witness": True,
                                                   "early_exit_observed": False}
    for case in ("control_hook", "tool_use_hook"):
        assert result["cases"][case] == {"status": "pass", "host_witness": True,
                                          "scope": "direct_function_only"}
    assert result["cases"]["builtin_mcp"] == {"status": "pass", "host_witness": True,
                                               "scope": "stdio_list_only"}


def test_missing_launcher_is_never_protection_pass():
    result = probe.run(["nonexistent-boundary-probe-launcher"])
    assert result["cases"]["allowed"]["status"] == "launch_error"
    assert all(v["status"] != "pass" for v in result["cases"].values())


def test_failed_positive_control_invalidates_denial(monkeypatch):
    def fake_run(argv, **kwargs):
        case = argv[-2]
        return subprocess.CompletedProcess(argv, 10 if case != "write" else 0,
                                           stdout=b"boundary-probe-child\n")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.run([])
    assert result["cases"]["allowed"]["status"] == "fail"
    assert result["cases"]["read"]["status"] == "inconclusive"
    assert result["cases"]["write"]["status"] == "fail"
    assert result["cases"]["control_hook"]["status"] == "inconclusive"


def test_launcher_exit_code_without_child_is_not_denial(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 10, stdout=b""))
    result = probe.run([])
    assert result["cases"]["read"]["status"] == "inconclusive"


def test_mutation_requires_ready_barrier_and_witness(tmp_path):
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (tmp_path / "run-id").write_text("run-a", encoding="ascii")
    result = probe.mutation_probe("external_writer", [], os.sys.executable,
                                  str(probe.__file__), tmp_path, work, outside,
                                  os.environ.copy(), "wrong-run", 2)
    assert result == {"status": "inconclusive", "host_witness": False}


def test_receipt_never_establishes_seal_or_accepts_another_run():
    receipt = dict(version=1, run_id="run-a", snapshot_digest=None, runtime_digest=None,
                   policy_digest=None, supervisor_identity=None, child_identity=None,
                   namespace_identities={}, preparation="ok", launch="exited_0",
                   cleanup="unknown", seal_status="unsealed", unsupported_reasons=[])
    assert probe.receipt_diagnostic(receipt, "run-a")["status"] == "unsupported"
    assert probe.receipt_diagnostic(receipt, "run-b")["status"] == "inconclusive"
    receipt["seal_status"] = "sealed"
    assert probe.receipt_diagnostic(receipt, "run-a")["status"] == "unsupported"
    receipt.pop("cleanup")
    assert probe.receipt_diagnostic(receipt, "run-a")["status"] == "inconclusive"


def test_reverse_interop_writer_is_run_bound(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "run-id").write_text("run-a", encoding="ascii")
    proc = subprocess.run([os.sys.executable, probe.__file__, "--interop-writer", str(tmp_path)])
    assert proc.returncode == 0
    assert (work / "reverse_interop.marker").read_text(encoding="ascii") == "run-a"


def test_interop_passes_python_code_as_argv_not_shell_literal(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "interop.distro").write_text("disposable", encoding="ascii")
    (tmp_path / "run-id").write_text("run-a", encoding="ascii")
    calls = []
    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(probe.subprocess, "run", capture)
    assert probe.child("interop", tmp_path) == 0
    assert calls[0][5] == "-c"
    assert calls[0][6].startswith("import pathlib,")
    assert calls[0][-1] == "run-a"


def test_missing_process_observation_is_unknown(tmp_path):
    assert probe.synthetic_writer_alive(tmp_path) is None


def test_witnessed_detached_violation_survives_unknown_cleanup(monkeypatch):
    wait = probe.wait_synthetic_writer
    def unknown_after_reaping(work):
        wait(work)
        return False
    monkeypatch.setattr(probe, "wait_synthetic_writer", unknown_after_reaping)
    result = probe.run([])
    assert result["cases"]["detached_child"]["status"] == "fail"
    assert result["cases"]["detached_child"]["host_witness"] is True


def test_integration_missing_modules_is_inconclusive(tmp_path):
    result = integration_probe.run(tmp_path / "missing-a.py", tmp_path / "missing-b.py")
    assert result["status"] == "inconclusive"
    assert result["protection_verified"] is False


def test_integration_receipt_without_ready_witness_is_inconclusive(monkeypatch):
    class Receipt:
        def to_dict(self):
            return {"run_id": "synthetic", "preparation": "diagnostic_ready",
                    "launch": "exited_0", "cleanup": "broker_closed_socket_absent; descendants_unverified",
                    "seal_status": "unsealed"}

    class Boundary:
        @staticmethod
        def diagnostic_broker_probe(factory, on_ready):
            return Receipt()  # A receipt alone is not a socket witness.

    monkeypatch.setattr(integration_probe, "load", lambda path, name: Boundary())
    result = integration_probe.run(None, None)
    assert result["status"] == "inconclusive"
    assert result["protection_verified"] is False
