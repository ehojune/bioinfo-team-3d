"""Fake-only native trace regression; never load a DLL or start a process/thread."""
import ast
import itertools
import json
import time as real_time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from tests import test_windows_job_ci as owned
from tests.support.time_proxy import RuntimeProxy


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setenv("CI_SUITE_ARTIFACT_DIR", str(root))
    ticks, identifiers = itertools.count(), itertools.count(1)
    monkeypatch.setattr(owned, "time", SimpleNamespace(monotonic=lambda: next(ticks) / 1000))
    monkeypatch.setattr(owned, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=f"{next(identifiers):032x}")))
    return root


def read(trace):
    return json.loads(trace.path.read_text(encoding="utf-8"))


def test_absent_env_is_no_filesystem_clock_uuid_side_effect(monkeypatch):
    monkeypatch.delenv("CI_SUITE_ARTIFACT_DIR", raising=False)
    forbidden = Mock(side_effect=AssertionError("diagnostic work without opt-in"))
    monkeypatch.setattr(owned, "Path", forbidden)
    monkeypatch.setattr(owned, "time", SimpleNamespace(monotonic=forbidden))
    monkeypatch.setattr(owned, "uuid", SimpleNamespace(uuid4=forbidden))
    trace = owned._NativeTrace()
    trace.emit("body_positive")
    function = Mock(return_value=258)
    assert owned._trace_call(trace, "wait", function, 101, 5000, wait_ms=5000) == 258
    function.assert_called_once_with(101, 5000)
    forbidden.assert_not_called()


def test_allowlist_bounds_unique_names_and_trusted_records(diagnostic):
    directory = diagnostic / "critical"
    directory.mkdir()
    for name in ("identity.json", "collection-master.json", "metrics-master.json", "proof.json"):
        (directory / name).write_text('{"trusted":true}', encoding="utf-8")
    trace, other = owned._NativeTrace(), owned._NativeTrace()
    assert trace.path != other.path and trace.path.parent == directory
    trace.emit("PRIVATE-unknown")
    for _ in range(90):
        trace.emit("wait_enter", role="PRIVATE-path", wait_ms=123456, result="PRIVATE-token", signature_ok="PRIVATE")
    result = read(trace)
    text = trace.path.read_text(encoding="utf-8")
    assert len(result["events"]) == 64 and result["events"][-1]["phase"] == "trace_full"
    assert len(text.encode("ascii")) <= 16384 and "PRIVATE" not in text
    assert all(set(row) <= {"phase", "clock_ok", "elapsed_ms"} for row in result["events"])
    assert result["case_id"].startswith("positive-") and len(result["case_id"]) <= 48
    for name in ("identity.json", "collection-master.json", "metrics-master.json", "proof.json"):
        assert (directory / name).read_text(encoding="utf-8") == '{"trusted":true}'
    assert not list(directory.glob("*-pending.json"))
    trace.path, trace.full = directory / "identity.json", False
    trace.emit("body_positive")
    assert trace.path.read_text(encoding="utf-8") == '{"trusted":true}'


def test_collision_never_overwrites_an_existing_case(diagnostic, monkeypatch):
    monkeypatch.setattr(owned, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="a" * 32)))
    first = owned._NativeTrace()
    original = first.path.read_bytes()
    second = owned._NativeTrace()
    second.emit("body_positive")
    assert second.path is None and first.path.read_bytes() == original


def test_nonfinite_or_out_of_bound_clock_is_explicitly_unmeasured(diagnostic, monkeypatch):
    trace = owned._NativeTrace()
    for value in (float("nan"), float("inf"), -1.0, 1e10):
        monkeypatch.setattr(owned, "time", RuntimeProxy(owned.time, monotonic=lambda: value))
        trace.emit("wait_enter", call_ms=float("inf"), result=2**50)
        row = read(trace)["events"][-1]
        assert row["clock_ok"] is False
        assert not {"elapsed_ms", "call_ms", "result"} & set(row)
    text = trace.path.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text


def test_all_five_kinds_have_bounded_identifiable_case_ids(diagnostic):
    kinds = ("positive", "denied", "nested", "unknown_job", "receipt_timeout")
    traces = [owned._NativeTrace(kind) for kind in kinds]
    assert len({trace.path for trace in traces}) == 5
    for kind, trace in zip(kinds, traces):
        assert read(trace)["case_id"].startswith(kind + "-")
        assert len(read(trace)["case_id"]) <= 48
    assert owned._NativeTrace("PRIVATE-invalid-kind").path is None


def test_redirected_root_and_relative_path_decline_writes(diagnostic, monkeypatch):
    monkeypatch.setenv("CI_SUITE_ARTIFACT_DIR", "relative-artifacts")
    assert owned._NativeTrace().path is None
    monkeypatch.setenv("CI_SUITE_ARTIFACT_DIR", str(diagnostic))
    resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda p, **kw: diagnostic.parent / "outside" if p == diagnostic else resolve(p, **kw))
    assert owned._NativeTrace().path is None
    assert not (diagnostic / "critical").exists()


def test_redirected_case_target_declines_publication(diagnostic, monkeypatch):
    trace = owned._NativeTrace()
    before = trace.path.read_bytes()
    resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda p, **kw: diagnostic.parent / "outside" if p == trace.path else resolve(p, **kw))
    trace.emit("body_positive")
    assert trace.path.read_bytes() == before


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE-original"), KeyboardInterrupt(), SystemExit(2)])
def test_native_exception_and_results_survive_diagnostic_io_failure(diagnostic, monkeypatch, error):
    trace = owned._NativeTrace()
    opening = Path.open
    def fail_pending(path, *args, **kwargs):
        if path.name.endswith("-pending.json"):
            raise OSError("PRIVATE-I/O")
        return opening(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_pending)
    result = Mock(return_value=258)
    assert owned._trace_call(trace, "wait", result, 101, 5000, wait_ms=5000) == 258
    failed = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        owned._trace_call(trace, "wait", failed, 101, 5000, wait_ms=5000)
    assert caught.value is error
    result.assert_called_once_with(101, 5000); failed.assert_called_once_with(101, 5000)
    assert "PRIVATE" not in trace.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["normal", "body_error", "diagnostic_error"])
def test_actual_fixture_cleanup_is_unchanged_with_trace(diagnostic, tmp_path, monkeypatch, mode):
    calls, counts = [], {}
    def wait(handle, milliseconds):
        assert (tmp_path / "stop").exists() and milliseconds == 5000 and handle in (101, 102)
        calls.append(("wait", handle, milliseconds))
        counts[handle] = counts.get(handle, 0) + 1
        return 258 if counts[handle] == 1 else 0
    waiting = Mock(side_effect=wait)
    waiting.argtypes, waiting.restype = [owned.C.c_void_p, owned.C.c_uint32], owned.C.c_uint32
    def api(name):
        return Mock(side_effect=lambda *args: calls.append((name, *args)) or 1)
    dll = SimpleNamespace(CreateJobObjectW=Mock(return_value=1), SetInformationJobObject=Mock(return_value=1),
        AssignProcessToJobObject=Mock(return_value=1), WaitForSingleObject=waiting,
        TerminateProcess=api("terminate"), CloseHandle=api("close"))
    parent = SimpleNamespace(_handle=50, poll=Mock(return_value=0), kill=Mock(), wait=Mock(return_value=0))
    spawn = Mock(return_value=parent)
    monkeypatch.setattr(owned, "kernel", lambda: dll)
    monkeypatch.setattr(owned.subprocess, "Popen", spawn)
    monkeypatch.setattr(owned, "wait_result", lambda p: {"success":True,"parent_job":{
        "in_job":True,"kill_on_close":True,"breakaway_ok":True,"silent_breakaway_ok":False}})
    journal = tmp_path / "state/logs/detached-launch.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("", encoding="utf-8")
    error = RuntimeError("PRIVATE-body")
    try:
        with owned.owned_launch(tmp_path, allow_breakaway=True) as case:
            case.role_handle, case.child_handle = 101, 102
            owned.close_parent_job(case)
            if mode == "diagnostic_error":
                monkeypatch.setattr(case.trace, "emit", Mock(side_effect=OSError("PRIVATE-diagnostic")))
            if mode != "normal":
                raise error
            owned._trace_event(case, "body_positive")
    except RuntimeError as caught:
        assert mode != "normal" and caught is error
    else:
        assert mode == "normal"
    assert counts == {101:2,102:2}
    assert [c for c in calls if c[0] == "terminate"] == [("terminate",101,1),("terminate",102,1)]
    assert [c for c in calls if c[0] == "close"] == [("close",1),("close",101),("close",102)]
    assert parent.wait.call_args_list == [call(timeout=10), call(timeout=10)]
    parent.kill.assert_not_called(); spawn.assert_called_once()
    events = read(case.trace)["events"]
    assert "PRIVATE" not in json.dumps(events)
    if mode != "diagnostic_error":
        waits = [e for e in events if e["phase"] == "wait_enter"]
        assert len(waits) == 4 and all(e["wait_ms"] == 5000 and e["signature_ok"] for e in waits)
        assert events[-1]["phase"] == "parent_wait_return"


def test_wrong_signature_flag_does_not_change_the_native_call(diagnostic):
    trace = owned._NativeTrace()
    function = Mock(return_value=258)
    function.argtypes, function.restype = [], owned.C.c_int
    assert owned._trace_call(trace, "wait", function, 101, 5000, role="child", wait_ms=5000) == 258
    function.assert_called_once_with(101, 5000)
    assert read(trace)["events"][-1]["signature_ok"] is False


def test_startup_error_still_closes_job_and_reaps_only_owned_parent(diagnostic, tmp_path, monkeypatch):
    dll = SimpleNamespace(CreateJobObjectW=Mock(return_value=1), SetInformationJobObject=Mock(return_value=1),
        AssignProcessToJobObject=Mock(return_value=1), CloseHandle=Mock(return_value=1))
    parent = SimpleNamespace(_handle=50, poll=Mock(return_value=None), kill=Mock(), wait=Mock(return_value=0))
    monkeypatch.setattr(owned, "kernel", lambda: dll)
    monkeypatch.setattr(owned.subprocess, "Popen", Mock(return_value=parent))
    error = RuntimeError("PRIVATE-startup")
    monkeypatch.setattr(owned, "wait_result", Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        with owned.owned_launch(tmp_path, allow_breakaway=True):
            pytest.fail("startup did not fail")
    assert caught.value is error
    dll.CloseHandle.assert_called_once_with(1)
    parent.kill.assert_called_once_with(); parent.wait.assert_called_once_with(timeout=10)
    files = list((diagnostic / "critical").glob("native-case-*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    phases = [e["phase"] for e in json.loads(text)["events"]]
    assert "wait_result_error" in phases and "parent_kill_return" in phases and "body_positive" not in phases
    assert "PRIVATE" not in text


def test_callable_duration_excludes_diagnostic_write_time(monkeypatch):
    clock, events = [0.0], []
    stdlib_clock = real_time.monotonic
    monkeypatch.setattr(owned, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert real_time.monotonic is stdlib_clock and owned.time is not real_time
    def emit(phase, **facts):
        events.append((phase, facts))
        clock[0] += 10  # Synthetic diagnostic write cost; no real waiting.
    def function():
        clock[0] += .5
        return 1
    trace = SimpleNamespace(path=True, emit=emit)
    assert owned._trace_call(trace, "close", function, role="child") == 1
    assert events[1][1]["call_ms"] == 500
    assert clock[0] == 20.5


def test_native_five_case_contract_and_python310_syntax_remain():
    source = Path(owned.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3,10))
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert [t.name for t in tests] == ["test_allowed_breakaway_child_survives_parent_job_close",
        "test_denied_direct_and_mock_denied_provider_record_no_pid_or_readiness",
        "test_wmi_escapes_owned_outer_nonbreakaway_inner_silent_job",
        "test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack"]
    assert ast.literal_eval(tests[-1].decorator_list[0].args[1]) == ["unknown_job", "receipt_timeout"]
    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3,10))
