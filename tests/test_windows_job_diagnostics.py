"""Fake/AST evidence for CI-only launch diagnostics; no Job/process/thread execution."""
import ast
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests import test_windows_job_ci as owned
from tests.support.time_proxy import RuntimeProxy


@pytest.mark.parametrize("code", [None, 1, -1, OSError("PRIVATE-error"), True, 2**40])
def test_failure_projection_reuses_allowlist_and_bounds_exitcode(tmp_path, code):
    journal = tmp_path / "state/logs/detached-launch.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"role": "gateway-supervisor", "status": "wmi_failed",
        "phase": "provider_create", "reason": "provider_timeout", "command": "PRIVATE-command",
        "environment": "PRIVATE-env", "path": "PRIVATE-path", "stderr": "PRIVATE-stderr",
        "provider": {"trace_status": "partial", "phases": [
            {"phase": "powershell_started", "elapsed_ms": 194},
            {"phase": "stdin_open", "elapsed_ms": 5445},
            {"phase": "header_read", "elapsed_ms": 5546}]}}), encoding="utf-8")
    parent = SimpleNamespace(poll=Mock(side_effect=code if isinstance(code, Exception) else None,
                                     return_value=code))
    text = owned._owned_launch_diagnostics(tmp_path, parent)
    assert len(text.encode("ascii")) <= 4096 and "PRIVATE" not in text
    result = json.loads(text)
    expected = "running" if code is None else code if type(code) is int and abs(code) <= 2**32 else "unavailable"
    assert result["launcher_exitcode"] == expected
    facts, = json.loads(result["journal"])
    assert facts["phase"] == "provider_create" and facts["reason"] == "provider_timeout"
    assert facts["provider"]["phases"][-1] == {"phase": "header_read", "elapsed_ms": 5546}
    parent.poll.assert_called_once()


@pytest.mark.parametrize("projection", ["missing", "malformed", "oversized", "unreadable"])
def test_missing_or_bad_journal_cannot_hide_the_exitcode(tmp_path, monkeypatch, projection):
    journal = tmp_path / "state/logs/detached-launch.jsonl"
    if projection == "malformed":
        journal.parent.mkdir(parents=True)
        journal.write_text("PRIVATE-not-json", encoding="utf-8")
    elif projection == "oversized":
        monkeypatch.setattr(owned, "_launch_journal", lambda path: "PRIVATE" * 5000)
    elif projection == "unreadable":
        monkeypatch.setattr(owned, "_launch_journal", Mock(side_effect=OSError("PRIVATE-error")))
    text = owned._owned_launch_diagnostics(tmp_path, SimpleNamespace(poll=lambda: 1))
    assert len(text.encode("ascii")) <= 4096 and "PRIVATE" not in text
    result = json.loads(text)
    assert result["launcher_exitcode"] == 1
    assert any(marker in result["journal"] for marker in ("missing", "malformed", "bound", "unavailable"))


@pytest.mark.parametrize("error", [AssertionError("owned launcher produced no outcome"),
                                  RuntimeError("PRIVATE-readiness"), SystemExit(2)])
@pytest.mark.parametrize("diagnostic_break", [False, True])
def test_actual_fixture_exception_handler_reports_and_reraises(tmp_path, error, diagnostic_break):
    source = Path(owned.__file__).read_text(encoding="utf-8")
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "owned_launch")
    boundary = next(n for n in function.body if isinstance(n, ast.Try))
    # Execute the actual handler with one fake failing operation, excluding all native setup/cleanup.
    fragment = ast.Try(body=[ast.Expr(value=ast.Call(func=ast.Name(id="action", ctx=ast.Load()), args=[], keywords=[]))],
                       handlers=copy.deepcopy(boundary.handlers), orelse=[], finalbody=[ast.Pass()])
    stream = io.StringIO()
    action = Mock(side_effect=error)
    ns = {"action": action, "case": SimpleNamespace(parent=SimpleNamespace(poll=lambda: 1)),
          "tmp_path": tmp_path, "sys": SimpleNamespace(stderr=stream),
          "_owned_launch_diagnostics": Mock(side_effect=OSError("PRIVATE-diagnostic")) if diagnostic_break
                                       else owned._owned_launch_diagnostics}
    with pytest.raises(type(error)) as caught:
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fragment], type_ignores=[])), "<fixture-handler>", "exec"), ns)
    assert caught.value is error
    text = stream.getvalue()
    if diagnostic_break:
        assert text == ""
    else:
        assert '"launcher_exitcode": 1' in text and "journal missing" in text
    assert "PRIVATE" not in text and len(text) < 4200
    action.assert_called_once()


def test_wait_result_still_fails_at_45_seconds_without_process_access(monkeypatch):
    ticks = iter([0.0, 44.99, 45.0])
    monkeypatch.setattr(owned, "time", RuntimeProxy(owned.time, monotonic=lambda: next(ticks)))
    sleep = Mock()
    monkeypatch.setattr(owned, "time", RuntimeProxy(owned.time, monotonic=owned.time.monotonic, sleep=sleep))
    read = Mock(side_effect=FileNotFoundError())
    with pytest.raises(AssertionError, match="^owned launcher produced no outcome$"):
        owned.wait_result(SimpleNamespace(read_text=read))
    read.assert_called_once_with(encoding="utf-8")
    sleep.assert_called_once_with(.02)
