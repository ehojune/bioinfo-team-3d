"""PI launch and recovery paths stay usable from a fresh Windows terminal."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from labhq import cli, doctor
from labhq.adapters.base import AgentAdapter, RunContext, RunState
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings


def _config(tmp_path: Path, *, token: str = "private-client") -> Path:
    path = tmp_path / "labhq.yaml"
    path.write_text(
        "gateway:\n"
        f"  client_token: {token}\n"
        "  runner_token: private-runner\n"
        f"  state_dir: {tmp_path.as_posix()}/state\n"
        "runner:\n"
        f"  state_dir: {tmp_path.as_posix()}/state\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_an_explicit_missing_config_never_falls_back_to_defaults(tmp_path, monkeypatch, source):
    missing = tmp_path / "missing.yaml"
    if source == "environment":
        monkeypatch.setenv("LABHQ_CONFIG", str(missing))
        call = lambda: Settings.load()
    else:
        call = lambda: Settings.load(str(missing))
    with pytest.raises(FileNotFoundError, match="설정 파일이 없습니다"):
        call()


def test_cli_reports_a_missing_config_before_any_api_call(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(cli, "_api", lambda *args, **kwargs: pytest.fail("unexpected API call"))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(missing), "status"])
    assert stopped.value.code == 2 and "설정 파일이 없습니다" in capsys.readouterr().err


def test_gateway_rejects_the_published_client_token(tmp_path, capsys):
    config = _config(tmp_path, token="change-me-client")
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), "gateway"])
    assert stopped.value.code == 2
    assert "change-me" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["status", "open"])
def test_closed_gateway_is_one_line_and_exit_2(tmp_path, monkeypatch, capsys, command):
    config = _config(tmp_path)

    def down(method, url, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", down)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), command])
    assert stopped.value.code == 2
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1 and "labhq up" in lines[0]


def test_approve_404_prints_server_detail_without_a_traceback(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)

    def request(method, url, **kwargs):
        req = httpx.Request(method, url)
        if method == "GET":
            return httpx.Response(200, json=[], request=req)
        return httpx.Response(404, json={"detail": "no such pending approval"}, request=req)

    monkeypatch.setattr(httpx, "request", request)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), "approve", "appr_done"])
    assert stopped.value.code == 2
    lines = capsys.readouterr().err.strip().splitlines()
    assert lines == ["gateway 요청 실패(HTTP 404): no such pending approval"]


def test_clarify_without_a_note_keeps_the_request_and_prints_the_question(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        req = httpx.Request(method, url)
        return httpx.Response(200, json=[{
            "id": "appr_q", "kind": "clarify", "summary": "답해 주세요",
            "detail": {"questions": [{"question": "분석군은?", "options": ["a", "b"]}]},
        }], request=req)

    monkeypatch.setattr(httpx, "request", request)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), "approve", "appr_q"])
    assert stopped.value.code == 2 and calls == ["GET"]
    error = capsys.readouterr().err
    assert "분석군은?" in error and '--note "1: a, 2: b"' in error and "요청은 그대로 유지" in error


def test_up_and_down_manage_only_their_pid_files_and_clean_runner_path(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_health", lambda _settings: None)
    monkeypatch.setattr(
        cli, "_wait_for_health",
        lambda _settings, *, runner, timeout=20: {
            "service": "labhq gateway", "runners": [settings.runner.id] if runner else [],
        },
    )
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv(
        "PATH",
        r"C:\Tools;C:\Users\PI\AppData\Local\Microsoft\WindowsApps;"
        r"C:\Users\PI\AppData\Local\Python\bin;C:\Git\bin",
    )
    calls = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process(4100 + len(calls))

    cli._up(settings, popen=popen)
    assert [call[0][-1] for call in calls] == ["gateway", "runner"]
    assert all(call[1]["creationflags"] & 0x00000208 == 0x00000208 for call in calls)
    runner_path = calls[1][1]["env"]["PATH"].casefold()
    assert "windowsapps" not in runner_path and "appdata\\local\\python\\bin" not in runner_path
    assert cli._pid_file(settings, "gateway").read_text().strip() == "4101"
    assert cli._pid_file(settings, "runner").read_text().strip() == "4102"
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    cli._down(settings)
    assert [pid for pid, _ in killed] == [4102, 4101]
    assert not cli._pid_file(settings, "gateway").exists() and not cli._pid_file(settings, "runner").exists()
    assert "웹 사무실:" in capsys.readouterr().out


def test_up_does_not_spawn_when_gateway_and_runner_are_healthy(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_health", lambda _settings: {
        "service": "labhq gateway", "runners": [settings.runner.id],
    })
    cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("unexpected spawn"))


class _Adapter(AgentAdapter):
    def build_command(self, ctx):
        return []

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        return None


def test_staff_env_removes_windows_python_alias_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", r"C:\Tools;C:\Users\PI\AppData\Local\Microsoft\WindowsApps;C:\Git\bin")
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.mock)

    async def emit(kind, data):
        return None

    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x")
    path = _Adapter(settings).staff_env(ctx)["PATH"].casefold()
    assert "windowsapps" not in path and path == r"c:\tools;c:\git\bin".casefold()


def test_doctor_warns_when_python3_is_a_windows_app_alias(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name, path=None: (
        r"C:\Users\PI\AppData\Local\Microsoft\WindowsApps\python3.exe" if name == "python3" else None
    ))
    row = doctor._python3_alias_row({"PATH": "ignored"})
    assert row and row["status"] == "warn" and "앱 실행 별칭" in row["detail"]
    assert "python3를 끄세요" in row["hint"]


def test_demo_web_chooses_a_free_port_when_8787_is_busy(monkeypatch):
    monkeypatch.setattr(cli, "_port_available", lambda host, port: False)
    monkeypatch.setattr(cli, "free_port", lambda: 54321)
    assert cli._demo_web_port("127.0.0.1", 8787) == 54321


def test_watch_renders_staff_warnings(capsys):
    cli.render({"type": "agent.log", "agent_id": "worker", "data": {"level": "alert", "text": "UAC 대기"}})
    assert "UAC 대기" in capsys.readouterr().out


def test_repo_wrappers_use_the_local_venv():
    root = Path(__file__).resolve().parents[1]
    windows = (root / "labhq.cmd").read_text(encoding="utf-8")
    posix = (root / "labhq.sh").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in windows and "PYTHONUTF8=1" in windows
    assert ".venv/bin/python" in posix and '"$@"' in posix
