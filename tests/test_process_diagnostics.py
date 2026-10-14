import asyncio
import importlib
import json
import subprocess
import sys

import pytest

from labhq import cli, doctor
from labhq.settings import Settings


def _settings(tmp_path):
    return Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                    "runner": {"state_dir": str(tmp_path / "runner")},
                                    "hpc": {"scheduler": "none"}})


def test_diagnostics_records_heartbeat_exception_and_exit(tmp_path):
    module = importlib.import_module("labhq.process_diagnostics")
    script = """
import asyncio, sys
from pathlib import Path
from labhq.process_diagnostics import ProcessDiagnostics
with ProcessDiagnostics(Path(sys.argv[1]), 'gateway', interval=.02) as diag:
    async def work():
        diag.attach_loop()
        asyncio.get_running_loop().call_exception_handler({'exception': ValueError('private-value')})
        await asyncio.sleep(.08)
        raise RuntimeError('private-value')
    asyncio.run(work())
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 1
    records = [json.loads(line) for line in (tmp_path / "gateway-diagnostics.jsonl").read_text().splitlines()]
    assert records[0]["event"] == "start"
    assert sum(r["event"] == "heartbeat" for r in records) >= 2
    assert all("rss_bytes" in r and "handles" in r for r in records if r["event"] == "heartbeat")
    assert any(r["event"] == "asyncio_exception" and r["reason"] == "ValueError" for r in records)
    assert records[-1]["event"] == "exit" and records[-1]["code"] == 1
    assert records[-1]["reason"] == "exception:RuntimeError"
    assert "private-value" not in (tmp_path / "gateway-diagnostics.jsonl").read_text()
    assert (tmp_path / "gateway-fault.log").exists()
    assert module.latest_records(tmp_path, "gateway", records[0]["pid"])["exit"] == records[-1]


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_stale_pid_is_reported_with_last_records_without_deleting_it(tmp_path, monkeypatch, capsys, command):
    settings = _settings(tmp_path)
    state = settings.path(settings.gateway.state_dir)
    state.mkdir()
    pid_file = cli._pid_file(settings, "gateway")
    pid_file.write_text(json.dumps({"pid": 99999999, "created": "old", "role": "gateway", "config": None}))
    (state / "logs").mkdir()
    (state / "logs" / "gateway-diagnostics.jsonl").write_text(
        json.dumps({"event": "heartbeat", "pid": 99999999, "time": "2026-01-01T00:00:00Z",
                    "rss_bytes": 123, "handles": 5}) + "\n")
    monkeypatch.setattr(cli, "_process_created", lambda pid: None)
    monkeypatch.setattr(Settings, "load", lambda *args: settings)
    if command == "status":
        def offline(*args, **kwargs):
            raise SystemExit(2)
        monkeypatch.setattr(cli, "_api", offline)
        with pytest.raises(SystemExit):
            cli.main([command])
        output = capsys.readouterr().out
    else:
        monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
        result = doctor.collect(settings)
        output = doctor.render(result)
        assert any(r["group"] == "process" and r["status"] == "warn" for r in result["checks"])
    assert "비정상 종료" in output and "99999999" in output
    assert "heartbeat" in output and "2026-01-01T00:00:00Z" in output
    assert "exit 기록 없음" in output
    assert pid_file.exists()


def test_up_redirects_both_streams_and_unbuffers_child(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "_process_created", lambda pid: "created")
    def spawn(command, **options):
        assert options["stderr"] == subprocess.STDOUT
        assert options["stdout"].name.endswith("gateway.log")
        assert options["env"]["PYTHONUNBUFFERED"] == "1"
        options["stdout"].write("child-output\n")
        return type("Child", (), {"pid": 99999999})()
    cli._start_background(settings, "gateway", popen=spawn)
    assert "child-output" in (tmp_path / "state" / "logs" / "gateway.log").read_text()


@pytest.mark.parametrize("ending,reason,code", [
    ("pass", "returned", 0),
    ("raise RuntimeError('private')", "exception:RuntimeError", 1),
])
def test_atexit_and_excepthook_in_a_private_child(tmp_path, ending, reason, code):
    script = "\n".join([
        "from pathlib import Path", "import sys",
        "from labhq.process_diagnostics import ProcessDiagnostics",
        "diag = ProcessDiagnostics(Path(sys.argv[1]), 'runner')",
        "diag.__enter__()", ending,
    ])
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=15)
    rows = [json.loads(line) for line in (tmp_path / "runner-diagnostics.jsonl").read_text().splitlines()]
    assert child.returncode == code
    assert rows[-1]["event"] == "exit" and rows[-1]["reason"] == reason and rows[-1]["code"] == code


def test_signal_preserves_existing_handler_and_records_reason(tmp_path):
    import signal
    module = importlib.import_module("labhq.process_diagnostics")
    previous = signal.getsignal(signal.SIGTERM)
    calls = []
    try:
        signal.signal(signal.SIGTERM, lambda sig, frame: calls.append(sig))
        with module.ProcessDiagnostics(tmp_path, "runner") as diag:
            diag._signal(signal.SIGTERM, None)  # No signal sent to any live instance.
        assert calls == [signal.SIGTERM]
        assert signal.getsignal(signal.SIGTERM) is not previous
        rows = module.latest_records(tmp_path, "runner", diag.pid)
        assert rows["exit"]["reason"] == "signal:SIGTERM"
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("role", ["gateway", "runner"])
def test_cli_installs_diagnostics_for_each_role(tmp_path, monkeypatch, role):
    settings = _settings(tmp_path)
    settings.gateway.client_token = "test-token"
    monkeypatch.setattr(Settings, "load", lambda *args: settings)
    if role == "gateway":
        import uvicorn
        def serve(app, **kwargs):
            async def startup():
                for hook in app.router.on_startup:
                    if hook.__name__ == "install_loop_diagnostics":
                        await hook()
                assert asyncio.get_running_loop().get_exception_handler() is not None
            asyncio.run(startup())
        monkeypatch.setattr(uvicorn, "run", serve)
    else:
        from labhq.runner import daemon
        class Runner:
            tasks = {}
            def __init__(self, settings):
                pass
            async def run_forever(self):
                assert asyncio.get_running_loop().get_exception_handler() is not None
            def stop(self):
                pass
        monkeypatch.setattr(daemon, "Runner", Runner)
    cli.main([role])
    rows = [json.loads(line) for line in (tmp_path / "state" / "logs" / f"{role}-diagnostics.jsonl").read_text().splitlines()]
    assert rows[0]["event"] == "start" and rows[-1]["event"] == "exit"
    assert rows[-1]["reason"] == "returned" and rows[-1]["code"] == 0
    if role == "gateway":
        assert any(row["event"] == "uvicorn_end" for row in rows)


def test_status_reads_the_heartbeat_of_the_launchers_child_not_a_stale_parent_orphan(tmp_path, monkeypatch):
    """#589: `up` records the venv launcher's PID but the real interpreter, its child, writes the diagnostics, so
    `status` said `heartbeat 기록 없음` for a live gateway. Only a child created after the launcher counts."""
    from labhq import proc_tree

    launcher, child, orphan = 910_000_010, 910_000_011, 910_000_012
    settings = _settings(tmp_path)
    state = settings.path(settings.gateway.state_dir)
    (state / "logs").mkdir(parents=True)
    cli._pid_file(settings, "gateway").write_text(json.dumps(
        {"pid": launcher, "created": "windows:1000", "role": "gateway", "config": None}))

    def rows(pid, stamp):
        return [json.dumps({"event": event, "pid": pid, "time": stamp, "rss_bytes": 123, "handles": 5})
                for event in ("start", "heartbeat")]

    (state / "logs" / "gateway-diagnostics.jsonl").write_text(
        chr(10).join(rows(child, "2026-10-14T01:00:00+00:00") + rows(orphan, "2026-10-14T02:00:00+00:00")) + chr(10))
    monkeypatch.setattr(cli, "_process_created", lambda pid: "windows:1000")
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: [
        proc_tree.Proc(launcher, 1, 1000), proc_tree.Proc(child, launcher, 1100),
        proc_tree.Proc(orphan, launcher, 400)])  # the orphan only carries the launcher's PID as a stale parent

    row = next(r for r in cli._local_process_diagnostics(settings) if r["name"] == "gateway")

    assert "heartbeat 기록 없음" not in row["detail"]
    assert "heartbeat 2026-10-14T01:00:00+00:00 RSS=123" in row["detail"]
    assert "2026-10-14T02:00:00" not in row["detail"]
    assert f"PID {launcher} 실행 중" in row["detail"] and row["status"] == "ok"
