"""Pure startup-failure projection; never install or launch a service."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts import clean_install_check as check


def _journal(root):
    path = root / "state/gateway/logs/detached-launch.jsonl"
    path.parent.mkdir(parents=True)
    return path


def _row(role="gateway-supervisor"):
    return {"role": role, "status": "wmi_failed", "phase": "provider_create",
            "reason": "provider_timeout", "error": "TimeoutExpired", "code": None,
            "command": "SECRET-command", "environment": "SECRET-environment",
            "provider": {"elapsed_ms": 15000, "trace_status": "partial",
                         "stdout": "SECRET-output", "phases": [
                             {"phase": "powershell_started", "elapsed_ms": 20},
                             {"phase": "stdin_open", "elapsed_ms": 22},
                             {"phase": "header_read", "elapsed_ms": 25}]}}


def test_failed_up_emits_both_supervisor_facts_then_preserves_exact_failure(tmp_path, monkeypatch, capsys):
    path = _journal(tmp_path)
    path.write_text("\n".join(json.dumps(_row(role)) for role in
                              ("gateway-supervisor", "runner-supervisor")), encoding="utf-8")
    failure = check.CheckFailure("gateway와 runner 시작", "original failure")
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    monkeypatch.setattr(check, "_run", run)
    checkout, env = tmp_path / "checkout", {"PRIVATE": "SECRET-env"}
    with pytest.raises(check.CheckFailure) as caught:
        check._start_services(tmp_path, checkout, env)
    assert caught.value is failure
    assert calls == [(("gateway와 runner 시작", check._labhq_command(checkout, "-c", "config/labhq.yaml", "up")),
                      {"cwd": checkout, "env": env, "timeout": 120})]
    text = capsys.readouterr().err
    assert text.startswith("STARTUP_DIAGNOSTIC ") and len(text.splitlines()) == 1
    assert "SECRET" not in text and str(tmp_path) not in text
    facts = json.loads(text.removeprefix("STARTUP_DIAGNOSTIC "))
    assert [row["role"] for row in facts["events"]] == ["gateway-supervisor", "runner-supervisor"]
    assert all(row["reason"] == "provider_timeout" and row["provider"]["elapsed_ms"] == 15000
               and row["provider"]["phases"][-1]["phase"] == "header_read" for row in facts["events"])


@pytest.mark.parametrize("mode", ["missing", "unreadable", "malformed", "partial", "invalid", "oversize"])
def test_journal_is_bounded_and_reports_evidence_gaps(tmp_path, monkeypatch, mode):
    path = _journal(tmp_path)
    if mode == "unreadable":
        original_open = Path.open
        def denied(self, *args, **kwargs):
            if self == path:
                raise OSError("SECRET-path")
            return original_open(self, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    elif mode == "malformed":
        path.write_bytes(b"{SECRET-bad\n" + b"[" * 1500 + b"\n")
    elif mode == "partial":
        path.write_text(json.dumps(_row()) + "\n{SECRET-partial", encoding="utf-8")
    elif mode == "invalid":
        row = _row()
        row.update(reason="SECRET-reason", error="SECRET-error", code=True)
        row["provider"].update(elapsed_ms=float("nan"), returncode="SECRET-code")
        row["provider"]["phases"][1]["elapsed_ms"] = -1
        path.write_text(json.dumps(row) + "\n" + json.dumps(_row("SECRET-role")), encoding="utf-8")
    elif mode == "oversize":
        path.write_text("SECRET-prefix" * 2000 + "\n" + "\n".join(
            json.dumps(_row()) for _ in range(30)), encoding="utf-8")
    result = check._startup_journal(path)
    text = json.dumps(result, ensure_ascii=True)
    assert len(text.encode("ascii")) <= 4096 and "SECRET" not in text
    assert len(result["events"]) <= 8
    if mode in ("missing", "unreadable"):
        assert result == {"journal": mode, "events": []}
    elif mode in ("malformed", "partial"):
        assert any(row == {"journal": "malformed"} for row in result["events"])
        if mode == "partial":
            assert result["events"][0]["reason"] == "provider_timeout"
    elif mode == "oversize":
        assert result["journal"] == "tail_only" and result["events"]
    else:
        row = result["events"][0]
        assert not {"reason", "error", "code"} & row.keys()
        assert row["provider"] == {"trace_status": "partial", "phases": [
            {"phase": "powershell_started", "elapsed_ms": 20}]}


def test_success_does_not_read_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "_run", lambda *a, **kw: "started")
    def forbidden(*a, **kw):
        pytest.fail("successful startup must not read journal")
    monkeypatch.setattr(check, "_startup_journal", forbidden)
    check._start_services(tmp_path, tmp_path / "checkout", {})


def test_full_valid_tail_is_projected_under_byte_budget(tmp_path, monkeypatch):
    path = _journal(tmp_path)
    row = _row()
    row.update(returncode=-2**32, elapsed_ms=60000, trace_status="invalid_or_partial")
    row["provider"].update({key: value for key, value in row.items() if key != "provider"})
    row["provider"]["phases"] = [
        {"phase": phase, "elapsed_ms": 60000} for phase in (
            "powershell_started", "stdin_open", "header_read", "input_read", "input_accepted",
            "create_entered", "create_returned")]
    path.write_text("\n".join(json.dumps(row) for _ in range(8)), encoding="utf-8")
    original_open, reads = Path.open, []

    class ReadBudget:
        def __enter__(self):
            self.stream = original_open(path, "rb")
            return self
        def __exit__(self, *args):
            self.stream.close()
        def seek(self, *args):
            return self.stream.seek(*args)
        def tell(self):
            return self.stream.tell()
        def read(self, size):
            reads.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(Path, "open", lambda self, *a, **kw:
                        ReadBudget() if self == path else original_open(self, *a, **kw))
    result = check._startup_journal(path)
    assert reads == [16384]
    assert result["journal"] == "tail_only" and 0 < len(result["events"]) < 8
    assert len(json.dumps(result).encode("ascii")) <= 4096
    assert result["events"][-1]["provider"]["phases"][-1]["phase"] == "create_returned"


def test_diagnostic_failure_cannot_replace_startup_failure(tmp_path, monkeypatch):
    failure = check.CheckFailure("startup", "original")
    def fail(*a, **kw):
        raise failure
    def broken(*a, **kw):
        raise RuntimeError("SECRET-diagnostic")
    monkeypatch.setattr(check, "_run", fail)
    monkeypatch.setattr(check, "_startup_journal", broken)
    with pytest.raises(check.CheckFailure) as caught:
        check._start_services(tmp_path, tmp_path / "checkout", {})
    assert caught.value is failure


def test_actual_rehearsal_uses_failure_projection_before_existing_cleanup():
    tree = ast.parse(Path(check.__file__).read_text(encoding="utf-8"), feature_version=(3, 10))
    rehearse = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "rehearse")
    calls = [node for node in ast.walk(rehearse) if isinstance(node, ast.Call)]
    startup = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_start_services"]
    assert len(startup) == 1
    assert [ast.unparse(arg) for arg in startup[0].args] == ["root", "checkout", "env"]
    cleanup = next(node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "rmtree")
    assert startup[0].lineno < cleanup.lineno
    assert not any(isinstance(node.func, ast.Name) and node.func.id == "_run" and node.args
                   and isinstance(node.args[0], ast.Constant) and node.args[0].value == "gateway와 runner 시작"
                   for node in calls)
