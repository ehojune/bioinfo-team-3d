"""Pure/fake contracts only. Never execute PowerShell, WMI, threads or processes."""
import ast
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from scripts import windows_transport_probe825 as probe


def trace(count=None):
    return b"".join(json.dumps({"phase": p, "elapsed_ms": i}).encode() + b"\n"
                    for i, p in enumerate(probe.PHASES[:count]))


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform="win32", stderr=io.StringIO()))
    monkeypatch.setattr(probe, "os", SimpleNamespace(environ={"GITHUB_ACTIONS": "true"}))
    shell = tmp_path / "powershell.exe"
    shell.write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(probe.broker, "_powershell", Mock(return_value=shell))
    monkeypatch.setattr(probe, "ARTIFACT", tmp_path / "receipt.json")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=trace()))
    monkeypatch.setattr(probe.subprocess, "run", run)
    return run


@pytest.mark.parametrize("platform,actions", [("linux", "true"), ("win32", "false"), ("win32", "True"), ("win32", None)])
def test_remote_guard_rejects_before_discovery_spawn_or_receipt(tmp_path, monkeypatch, platform, actions):
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform=platform, stderr=io.StringIO()))
    monkeypatch.setattr(probe, "os", SimpleNamespace(environ={"GITHUB_ACTIONS": actions}))
    discovery = Mock(side_effect=AssertionError("native discovery forbidden"))
    spawn = Mock(side_effect=AssertionError("spawn forbidden"))
    monkeypatch.setattr(probe.broker, "_powershell", discovery)
    monkeypatch.setattr(probe.subprocess, "run", spawn)
    monkeypatch.setattr(probe, "ARTIFACT", tmp_path / "receipt.json")
    assert probe.main() == 2
    with pytest.raises(RuntimeError, match="remote_only"):
        probe.run_case("original", 1024)
    discovery.assert_not_called(); spawn.assert_not_called()
    assert not probe.ARTIFACT.exists()


@pytest.mark.parametrize("mode", probe.MODES)
@pytest.mark.parametrize("size", probe.SIZES)
def test_exact_wire_and_production_options_with_only_fake_spawn(remote, mode, size):
    result = probe.run_case(mode, size)
    assert result["outcome"] == "completed" and result["reason"] == "transport_only_completed"
    remote.assert_called_once()
    args, kwargs = remote.call_args
    assert args[0][1:5] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert base64.b64decode(args[0][5]).decode("utf-16-le") == probe.receiver(mode)
    assert set(kwargs) == {"input", "stdout", "stderr", "timeout", "creationflags", "check"}
    assert kwargs["timeout"] == 10 and kwargs["creationflags"] == 0x08000000 and kwargs["check"] is False
    assert kwargs["stdout"] == probe.subprocess.PIPE and kwargs["stderr"] == probe.subprocess.DEVNULL
    frame = kwargs["input"]
    assert len(frame) == size + 4 and int.from_bytes(frame[:4], "little") == size and frame[4:].isascii()
    data = json.loads(frame[4:])
    assert set(data) == {"command", "cwd", "flags", "environment"}
    assert data["flags"] == probe.broker.BREAKAWAY | probe.broker.DETACHED | 0x400
    assert data["environment"][0].startswith("PROBE_PAD=")
    assert frame == probe.broker._request_frame(data)
    assert data["command"] not in json.dumps(result) and "PROBE_PAD" not in json.dumps(result)


def test_receiver_contrast_is_only_formatter_and_wmi_tail_is_absent():
    a, b = (probe.receiver(mode) for mode in probe.MODES)
    original = probe.broker.WMI_SCRIPT.split("function Write-Phase([string]$phase) {\n")[1].split("}\ntry {\n")[0]
    assert a.replace(original, probe.LITERAL_BODY, 1) == b
    for script in (a, b):
        assert all(token not in script for token in ("wmiclass", "CreateInstance", ".Create(", "Start-Process", "ProcessId"))
        assert script.count(".ReadBytes(") == script.count(".ReadUInt32()") == 1
        assert "ReadToEnd" not in script and "Peek(" not in script and ".ReadByte(" not in script
        assert "$length -lt 1 -or $length -gt 1048576" in script
        assert "[Text.UTF8Encoding]::new($false, $true)" in script
        validation = probe.broker.WMI_SCRIPT.split("  if ($null -eq $r", 1)[1].split("  Write-Phase 'input_accepted'", 1)[0]
        assert "  if ($null -eq $r" + validation in script
        points = ["payload_read_enter", "$payloadResult = $reader.ReadBytes", "payload_read_return",
                  "[byte[]]$payload = $payloadResult", "payload_assigned", "$payload.Length -ne",
                  "ConvertFrom-Json", "json_complete", "Write-Phase 'input_accepted'", "probe_complete"]
        assert [script.index(p) for p in points] == sorted(script.index(p) for p in points)
    assert a.count("ConvertTo-Json") == 1 and "ConvertTo-Json" not in b


def test_run_call_matches_current_production_flags_and_options():
    def call(path, name):
        fn = next(n for n in ast.parse(Path(path).read_text(encoding="utf-8")).body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        return next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess" and n.func.attr == "run")
    production = call(probe.broker.__file__, "_create")
    candidate = call(probe.__file__, "run_case")
    assert [ast.dump(n) for n in production.args] == [ast.dump(n) for n in candidate.args]
    assert {k.arg: ast.dump(k.value) for k in production.keywords if k.arg != "input"} == {
        k.arg: ast.dump(k.value) for k in candidate.keywords if k.arg != "input"}


@pytest.mark.parametrize("needle", ["  $startup = ([wmiclass]", "  [byte[]]$payload = $reader.ReadBytes([int]$length)"])
def test_receiver_source_drift_fails_closed(monkeypatch, needle):
    monkeypatch.setattr(probe.broker, "WMI_SCRIPT", probe.broker.WMI_SCRIPT.replace(needle, "changed", 1))
    with pytest.raises(ValueError, match="receiver_source_drift"):
        probe.receiver("original")


@pytest.mark.parametrize("raw", [b"PRIVATE-raw", b"\xff", b"x" * 8193,
    b'{"phase":"PRIVATE","elapsed_ms":1}\n',
    b'{"phase":"formatter_enter_powershell_started","elapsed_ms":true}\n',
    b'{"phase":"formatter_enter_powershell_started","elapsed_ms":NaN}\n',
    b'{"phase":"formatter_enter_powershell_started","elapsed_ms":1,"PRIVATE":1}\n',
    trace(2) + trace(1), trace() + b'{}\n'])
def test_projection_rejects_untrusted_frames_without_values(raw):
    phases, status = probe.project(raw)
    assert status == "invalid" and "PRIVATE" not in json.dumps(phases)


@pytest.mark.parametrize("failure", ["timeout", "timeout_complete", "nonzero", "partial", "native"])
def test_failures_stay_failures_and_never_retry(remote, failure):
    if failure.startswith("timeout"):
        remote.side_effect = probe.subprocess.TimeoutExpired("PRIVATE-command", 10,
            output=trace() if failure == "timeout_complete" else trace(3), stderr=b"PRIVATE-stderr")
    elif failure == "native":
        remote.side_effect = OSError("PRIVATE-error")
    else:
        remote.return_value = SimpleNamespace(returncode=1 if failure == "nonzero" else 0,
                                             stdout=trace() if failure == "nonzero" else trace(2))
    result = probe.run_case("literal", 65536)
    assert result["outcome"] == ("timeout" if failure.startswith("timeout") else "error")
    assert "PRIVATE" not in json.dumps(result)
    remote.assert_called_once()


def test_main_records_exact_four_cases_once_and_returns_failure(remote):
    remote.side_effect = [SimpleNamespace(returncode=0, stdout=trace()),
        probe.subprocess.TimeoutExpired("PRIVATE-command", 10, output=trace(1)),
        SimpleNamespace(returncode=0, stdout=trace()), SimpleNamespace(returncode=1, stdout=trace(2))]
    assert probe.main() == 1 and remote.call_count == 4
    rows = json.loads(probe.ARTIFACT.read_text(encoding="utf-8"))
    assert [(r["mode"], r["size"]) for r in rows] == [(m, n) for m in probe.MODES for n in probe.SIZES]
    assert [r["outcome"] for r in rows] == ["completed", "timeout", "completed", "error"]
    assert probe.ARTIFACT.stat().st_size <= 32768 and "PRIVATE" not in json.dumps(rows)


def test_all_complete_is_only_transport_completion(remote):
    assert probe.main() == 0 and remote.call_count == 4
    rows = json.loads(probe.ARTIFACT.read_text(encoding="utf-8"))
    assert len(rows) == 4 and all(r["outcome"] == "completed" for r in rows)
    assert all(r["reason"] == "transport_only_completed" for r in rows)


def test_interruption_leaves_completed_started_and_not_run_receipts(remote):
    remote.side_effect = [SimpleNamespace(returncode=0, stdout=trace()), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        probe.main()
    rows = json.loads(probe.ARTIFACT.read_text(encoding="utf-8"))
    assert len(rows) == 4 and [r["outcome"] for r in rows] == ["completed", "started", "not_run", "not_run"]
    assert remote.call_count == 2


def test_receipt_io_failure_emits_only_a_fixed_category(remote, monkeypatch):
    monkeypatch.setattr(probe, "write_receipt", Mock(side_effect=OSError("PRIVATE-path")))
    assert probe.main() == 1
    assert probe.sys.stderr.getvalue().strip() == "probe_receipt_error"
    remote.assert_not_called()


def test_dispatch_only_workflow_and_python310_syntax():
    workflow = yaml.load(Path(".github/workflows/test.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["name"] == "test" and workflow["on"] == {"workflow_dispatch": ""}
    assert workflow["permissions"] == {"contents": "read"} and len(workflow["jobs"]) == 1
    job, = workflow["jobs"].values()
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "5"
    steps = job["steps"]
    assert steps[0]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    assert steps[1]["with"]["python-version"] == "3.12"
    assert [s["run"] for s in steps if "run" in s] == ["python scripts/windows_transport_probe825.py"]
    assert steps[2]["timeout-minutes"] == "3" and steps[3]["if"] == "always()"
    assert steps[3]["with"]["path"] == "transport-probe825.json"
    for name in (probe.__file__, __file__):
        ast.parse(Path(name).read_text(encoding="utf-8"), feature_version=(3, 10))
