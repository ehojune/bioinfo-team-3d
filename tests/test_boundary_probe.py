import os
from pathlib import Path
import subprocess
import time

import pytest

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
    assert result["cases"]["detached_child"]["status"] == "unsupported"
    for case in ("control_hook", "tool_use_hook"):
        assert result["cases"][case] == {"status": "pass", "host_witness": True,
                                          "scope": "direct_function_only"}
    assert result["cases"]["builtin_mcp"] == {"status": "pass", "host_witness": True,
                                               "scope": "stdio_list_only"}


def test_missing_launcher_is_never_protection_pass():
    result = probe.run(["nonexistent-boundary-probe-launcher"])
    assert result["cases"]["allowed"]["status"] == "unsupported"
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
    assert calls[0][6].startswith("import os,pathlib,") and "os.replace(" in calls[0][6]
    assert calls[0][-1] == "run-a"


def test_missing_process_observation_is_unknown(tmp_path):
    assert probe.synthetic_writer_alive(tmp_path) is None


def test_detached_probe_never_launches_without_lifecycle(monkeypatch, tmp_path):
    run = probe.subprocess.run
    popen = probe.subprocess.Popen
    calls = []

    def guarded_run(argv, **kwargs):
        calls.append(argv)
        assert "detached_child" not in argv
        assert "--detached-writer" not in argv
        return run(argv, **kwargs)

    def guarded_popen(argv, **kwargs):
        calls.append(argv)
        assert "detached_child" not in argv
        assert "--detached-writer" not in argv
        return popen(argv, **kwargs)

    monkeypatch.setattr(probe.subprocess, "run", guarded_run)
    monkeypatch.setattr(probe.subprocess, "Popen", guarded_popen)
    launcher = tmp_path / "launcher.py"
    marker = tmp_path / "delayed-started"
    launcher.write_text(
        "import pathlib, subprocess, sys, time\n"
        "marker, *command = sys.argv[1:]\n"
        "if 'detached_child' in command:\n"
        "    pathlib.Path(marker).write_text('started')\n"
        "    time.sleep(1)\n"
        "sys.exit(subprocess.call(command))\n", encoding="utf-8")
    for prefix in ([], [os.sys.executable, str(launcher), str(marker)]):
        result = probe.run(prefix, timeout=2)
        assert result["cases"]["detached_child"] == {
            "status": "unsupported",
            "reason": "trusted descendant lifecycle ownership unavailable",
        }
        assert result["protection_verified"] is False
    assert calls and not marker.exists()


def test_launcher_environment_excludes_host_secret(monkeypatch):
    monkeypatch.setenv("BOUNDARY_TEST_SECRET", "private-fixture")
    seen = []
    def capture(argv, **kwargs):
        seen.append(kwargs.get("env", {}))
        return subprocess.CompletedProcess(argv, 20, stdout=b"")
    monkeypatch.setattr(probe.subprocess, "run", capture)
    probe.run([])
    assert seen and all("BOUNDARY_TEST_SECRET" not in env for env in seen)


def test_integration_missing_modules_is_inconclusive(tmp_path):
    result = integration_probe.run(tmp_path / "missing-a.py", tmp_path / "missing-b.py")
    assert result["status"] == "inconclusive"
    assert result["protection_verified"] is False


def test_integration_receipt_without_ready_witness_is_inconclusive(monkeypatch, tmp_path):
    (tmp_path / "boundary.py").write_text("boundary")
    (tmp_path / "broker.py").write_text("broker")
    called = []
    class Receipt:
        def to_dict(self):
            return {"run_id": "synthetic", "preparation": "diagnostic_ready",
                    "launch": "exited_0", "cleanup": "broker_closed_socket_absent; descendants_unverified",
                    "seal_status": "unsealed"}

    class Boundary:
        @staticmethod
        def diagnostic_broker_probe(factory, on_ready):
            called.append(True)
            return Receipt()  # A receipt alone is not a socket witness.

    monkeypatch.setattr(integration_probe, "load", lambda *args: Boundary())
    result = integration_probe.run(tmp_path / "boundary.py", tmp_path / "broker.py")
    assert called == [True]
    assert result["run_id_matches"] is False
    assert result["status"] == "inconclusive"
    assert result["protection_verified"] is False


def test_integration_source_mismatch_and_mutation(monkeypatch, tmp_path):
    boundary, broker = tmp_path / "boundary.py", tmp_path / "broker.py"
    boundary.write_text("before")
    broker.write_text("broker")
    assert integration_probe.run(boundary, broker, expected_boundary="0" * 64)["reason"] == "expected source hash mismatch"

    class Boundary:
        @staticmethod
        def diagnostic_broker_probe(factory, on_ready):
            boundary.write_text("after")
            raise ValueError("fixture")
    monkeypatch.setattr(integration_probe, "load", lambda *args: Boundary())
    result = integration_probe.run(boundary, broker)
    assert result["status"] == "inconclusive"
    assert result["reason"] == "source changed during diagnostic"
    assert result["sources"]["boundary"]["before"] != result["sources"]["boundary"]["after"]


def test_load_executes_supplied_bytes_not_stale_bytecode(tmp_path):
    import py_compile
    source = tmp_path / "fixture.py"
    source.write_text("value = 1\n")
    py_compile.compile(str(source), doraise=True)
    source.write_text("value = 2\n")
    module = integration_probe.load(source, "source_bytes_fixture", source.read_bytes())
    assert module.value == 2


def test_process_broker_cache_prevents_pass(tmp_path):
    import py_compile
    boundary, broker = tmp_path / "boundary.py", tmp_path / "broker.py"
    boundary.write_text("value = 1\n")
    broker.write_text("value = 1\n")
    py_compile.compile(str(broker), doraise=True)
    result = integration_probe.run(boundary, broker, "process")
    assert result["status"] == "inconclusive"
    assert result["reason"] == "broker bytecode cache present"


def test_malformed_frames_require_rejection_and_live_listener(monkeypatch, tmp_path):
    boundary, broker = tmp_path / "boundary.py", tmp_path / "broker.py"
    boundary.write_text("boundary")
    broker.write_text("broker")
    class Receipt:
        def to_dict(self):
            return {"run_id": "r", "preparation": "diagnostic_ready", "launch": "exited_0",
                    "cleanup": "broker_closed_socket_absent; descendants_unverified", "seal_status": "unsealed"}
    class Boundary:
        @staticmethod
        def diagnostic_broker_probe(factory, on_ready):
            on_ready("r", "absent-fixture.sock")
            return Receipt()
    monkeypatch.setattr(integration_probe, "load", lambda *args: Boundary())
    monkeypatch.setattr(integration_probe, "request", lambda path, obj: b'{"error":"request rejected"}\n' if obj.get("run_id") != "r" or "url" in obj or obj.get("body") == "cmVmbGVjdA==" else b'{"body":"b2s="}\n')
    monkeypatch.setattr(integration_probe, "request_raw", lambda path, data: b'{"error":"request rejected"}\n')
    class ClosedSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, path): raise OSError("closed")
    monkeypatch.setattr(integration_probe.socket, "socket", lambda *args: ClosedSocket())
    monkeypatch.setattr(integration_probe.socket, "AF_UNIX", 1, raising=False)
    result = integration_probe.run(boundary, broker)
    assert result["status"] == "pass", result
    assert len(result["witness"]["malformed"]) == 5
    monkeypatch.setattr(integration_probe, "request_raw", lambda path, data: b'{"body":"b2s="}\n')
    assert integration_probe.run(boundary, broker)["status"] == "inconclusive"


def test_unreadable_fixture_metadata_is_not_a_denial(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "port.txt").mkdir()  # reading it fails with an OS error, as a launcher denying host-made files would
    (work / "fd.txt").mkdir()
    assert probe.child("socket", tmp_path) == 20
    assert probe.child("fd", tmp_path) == 20


def test_run_reports_unreadable_fixture_as_inconclusive(monkeypatch):
    def fake_run(argv, **kwargs):
        # allowed passes; every other child could not read its fixture, so it exits 20 and never attempted anything
        case = argv[-2]
        return subprocess.CompletedProcess(argv, 0 if case == "allowed" else 20, stdout=b"boundary-probe-child\n")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.run([])
    assert result["cases"]["allowed"]["status"] == "pass"
    assert result["cases"]["socket"]["status"] == "inconclusive"
    assert result["cases"]["fd"]["status"] in ("inconclusive", "unsupported")


def test_marker_observation_waits_and_ignores_partial_content(tmp_path):
    import threading

    marker = tmp_path / "m.marker"

    def late():
        time.sleep(0.2)
        marker.write_text("run-", encoding="ascii")  # a partial run id is not this run's marker
        time.sleep(0.2)
        probe.publish_signal(marker, "run-a")

    worker = threading.Thread(target=late)
    worker.start()
    try:
        assert probe.observe_marker(marker, "run-a", 0) is False
        assert probe.observe_marker(marker, "run-a", 3) is True
    finally:
        worker.join()
    assert probe.observe_marker(tmp_path / "never", "run-a", 0.1) is False
    assert probe.observe_marker(marker, "other-run", 0) is False


def test_interop_never_launches_without_lifecycle(monkeypatch, tmp_path):
    original = probe.subprocess.run
    calls = []

    def guarded_run(argv, **kwargs):
        calls.append(argv)
        assert "interop" not in argv and "reverse_interop" not in argv
        return original(argv, **kwargs)

    monkeypatch.setattr(probe.subprocess, "run", guarded_run)
    launcher = tmp_path / "delayed.py"
    marker = tmp_path / "started"
    launcher.write_text(
        "import pathlib, subprocess, sys, time\n"
        "marker, *command = sys.argv[1:]\n"
        "if 'interop' in command or 'reverse_interop' in command:\n"
        "    time.sleep(1); pathlib.Path(marker).write_text('started')\n"
        "sys.exit(subprocess.call(command))\n", encoding="utf-8")
    for prefix in ([], [os.sys.executable, str(launcher), str(marker)]):
        result = probe.run(prefix, interop_distro="disposable", timeout=2)
        for case in ("interop", "reverse_interop"):
            assert result["cases"][case] == {
                "status": "unsupported",
                "reason": "trusted descendant lifecycle ownership unavailable",
            }
        assert result["protection_verified"] is False
    assert calls and not marker.exists()


def test_mutation_probe_rejects_unowned_launcher_before_spawn(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("unowned launcher was invoked")
    monkeypatch.setattr(probe.subprocess, "Popen", forbidden)
    for prefix, python in ((["delayed-launcher"], os.sys.executable), ([], "other-python")):
        for case in ("parent_replace", "external_writer"):
            result = probe.mutation_probe(case, prefix, python, probe.__file__, tmp_path,
                                          tmp_path, tmp_path, {}, "run-a", 0.01)
            assert result == {"status": "unsupported",
                              "reason": "trusted descendant lifecycle ownership unavailable"}
