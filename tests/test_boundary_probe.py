import os
from pathlib import Path
import subprocess
import time

import pytest

from scripts.boundary_probe import probe


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


def test_synthetic_seal_adapter_contract():
    from copy import deepcopy
    import hashlib
    import hmac
    import importlib.util
    import json
    import sys
    root = Path(__file__).parent / "fixtures" / "process_boundary_seal"
    spec = importlib.util.spec_from_file_location("offline_seal_adapter", root / "validator.py")
    validator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = validator
    spec.loader.exec_module(validator)
    CORPUS = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    def record_external_ledger(evidence, trusted):
        """Test-only observer recording, never called by validate or a child adapter."""
        witness = evidence["witness"]
        previous, hashes = "0" * 64, []
        for event in witness["events"]:
            payload = [witness["run_id"], witness["launch_nonce"], previous, event]
            previous = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            hashes.append(previous)
        trusted["ledger"] = {
            "store_id": trusted["store_id"], "observer_id": witness["observer_id"],
            "run_id": witness["run_id"], "launch_nonce": witness["launch_nonce"], "hashes": hashes,
        }


    def test_authenticated_offline_adapter_and_independent_cleanup():
        trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
        receipt = dict(version=1, run_id=trusted["run_id"], snapshot_digest=None, runtime_digest=None,
                       policy_digest=None, supervisor_identity=None, child_identity=None, namespace_identities={},
                       preparation="diagnostic_ready", launch="exited_0",
                       cleanup="broker_closed_socket_absent; descendants_unverified", seal_status="unsealed",
                       unsupported_reasons=["diagnostic only; runtime and storage are unsealed"])
        producer_key, observer_key = b"p" * 32, b"o" * 32

        def tags_for(r, e):
            def sign(domain, value, key):
                data = json.dumps([domain, trusted["run_id"], trusted["launch_nonce"], value],
                                  sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                return hmac.new(key, data, hashlib.sha256).hexdigest()
            return {"producer": sign("producer", r, producer_key), "observer": sign("observer", e, observer_key)}

        def run(r=receipt, e=evidence, tags=None, seen=None, expectations=trusted):
            return validator.adapt_producer(r, evidence=e, trusted=expectations, tags=tags or tags_for(r, e),
                                            producer_key=producer_key, observer_key=observer_key,
                                            consumed=set() if seen is None else seen)

        seen = set()
        assert run(seen=seen).status == "synthetic_only"
        assert run(seen=seen).contract == "rejected"
        assert run(tags={"producer": "0" * 64, "observer": tags_for(receipt, evidence)["observer"]}).contract == "rejected"
        retry = set()
        assert run(tags={"producer": "0" * 64, "observer": tags_for(receipt, evidence)["observer"]}, seen=retry).contract == "rejected"
        assert not retry
        failed_receipt = deepcopy(receipt)
        failed_receipt["launch"] = "failed"
        assert run(r=failed_receipt, seen=retry).contract == "rejected"
        assert retry == {(trusted["run_id"], trusted["launch_nonce"])}
        assert run(seen=retry).contract == "rejected"
        malformed_seen = set()
        assert run(r={}, seen=malformed_seen).contract == "rejected"
        assert malformed_seen == {(trusted["run_id"], trusted["launch_nonce"])}
        forged = deepcopy(evidence)
        forged["witness"]["observer_id"] = "producer"
        assert run(e=forged, tags=tags_for(receipt, evidence)).contract == "rejected"
        for change in (lambda r: r.update(run_id="wrong"), lambda r: r.pop("cleanup"),
                       lambda r: r.update(launch="failed"), lambda r: r.update(seal_status="sealed")):
            bad = deepcopy(receipt)
            change(bad)
            assert run(r=bad).contract == "rejected"
        for resource in ("descendants", "sockets", "mounts", "handles", "broker_endpoints", "active_tokens"):
            bad = deepcopy(evidence)
            cleanup = next(event for event in bad["witness"]["events"] if event["type"] == "cleanup")
            cleanup[resource] = ["remaining"]
            # An independent ledger and signature record the failure, rather than
            # making the test pass merely because a stale signature was rejected.
            updated = deepcopy(trusted)
            record_external_ledger(bad, updated)
            verdict = run(e=bad, expectations=updated)
            assert "cleanup_resources_not_cleared" in verdict.reasons
        for failure in ("broker_timeout", "observer_death", "supervisor_interrupted", "deadline_expired"):
            bad = deepcopy(evidence)
            events = bad["witness"]["events"]
            fault = deepcopy(events[2])
            fault["type"] = failure
            events.insert(3, fault)
            for index, event in enumerate(events):
                event["sequence"] = index
                event["time_ns"] = index
            updated = deepcopy(trusted)
            record_external_ledger(bad, updated)
            verdict = run(e=bad, expectations=updated)
            assert failure + "_observed" in verdict.reasons
            assert "reasoned_stop_unverified" in verdict.reasons


    test_authenticated_offline_adapter_and_independent_cleanup()


def test_imported_seal_negative_corpora():
    from copy import deepcopy
    import hashlib
    import importlib.util
    import json
    import sys

    root = Path(__file__).parent / "fixtures" / "process_boundary_seal"
    spec = importlib.util.spec_from_file_location("corpus_validator", root / "validator.py")
    validator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = validator
    spec.loader.exec_module(validator)
    corpus = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    controls = json.loads((root / "controls.json").read_text(encoding="utf-8"))["cases"]

    def materialize(case):
        evidence = deepcopy(corpus["positive"])
        for change in case["changes"]:
            parent = evidence
            for part in change["path"][:-1]:
                parent = parent[part]
            key = change["path"][-1]
            if change.get("remove"):
                del parent[key]
            elif "insert" in change:
                parent.insert(key, deepcopy(change["insert"]))
                for i, event in enumerate(parent):
                    event["sequence"] = i
            else:
                parent[key] = deepcopy(change["value"])
        return evidence

    for case in corpus["cases"]:
        verdict = validator.validate(materialize(case), deepcopy(corpus["trusted"]))
        assert (verdict.contract, verdict.status) == (case["expected"]["contract"], case["expected"]["status"]), case["id"]
        assert (case["expected"]["reason"] in verdict.reasons if case["expected"]["reason"] else not verdict.reasons), case["id"]
        assert verdict.protection == "unverified"
    for case in controls:
        for rerecord in (False, True):
            evidence, trusted = materialize(case), deepcopy(corpus["trusted"])
            if rerecord:
                witness = evidence["witness"]
                previous, hashes = "0" * 64, []
                for event in witness["events"]:
                    payload = [witness["run_id"], witness["launch_nonce"], previous, event]
                    previous = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    hashes.append(previous)
                trusted["ledger"] = {"store_id": trusted["store_id"], "observer_id": witness["observer_id"],
                                     "run_id": witness["run_id"], "launch_nonce": witness["launch_nonce"], "hashes": hashes}
            verdict = validator.validate(evidence, trusted)
            assert verdict.contract == "rejected" and case["reason"] in verdict.reasons, case["id"]
            assert verdict.protection == "unverified"
