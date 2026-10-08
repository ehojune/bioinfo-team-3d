"""PI launch and recovery paths stay usable from a fresh Windows terminal."""

from __future__ import annotations

import json
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
    assert len(lines) == 1 and "`labhq -c" in lines[0] and " up`" in lines[0]


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


def test_up_and_down_manage_only_their_process_records_and_clean_runner_path(tmp_path, monkeypatch, capsys):
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

    monkeypatch.setattr(cli, "_process_created", lambda pid: f"created:{pid}")
    cli._up(settings, popen=popen)
    assert [call[0][-1] for call in calls] == ["gateway", "runner"]
    assert all(call[1]["creationflags"] & 0x00000208 == 0x00000208 for call in calls)
    runner_path = calls[1][1]["env"]["PATH"].casefold()
    assert "windowsapps" not in runner_path and "appdata\\local\\python\\bin" not in runner_path
    gateway = json.loads(cli._pid_file(settings, "gateway").read_text())
    runner = json.loads(cli._pid_file(settings, "runner").read_text())
    assert gateway == {"pid": 4101, "role": "gateway", "created": "created:4101",
                       "config": settings.config_path}
    assert runner == {"pid": 4102, "role": "runner", "created": "created:4102",
                      "config": settings.config_path}
    killed = []
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid: killed.append(pid))
    cli._down(settings, force=False)
    assert killed == [4102, 4101]
    assert not cli._pid_file(settings, "gateway").exists() and not cli._pid_file(settings, "runner").exists()
    assert "웹 사무실:" in capsys.readouterr().out


def test_up_does_not_spawn_when_gateway_and_runner_are_healthy(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_health", lambda _settings: {
        "service": "labhq gateway", "runners": [settings.runner.id],
    })
    cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("unexpected spawn"))


def _write_process_record(settings, role, pid, created):
    path = cli._pid_file(settings, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "role": role, "created": created,
                                "config": settings.config_path}), encoding="utf-8")
    return path


def test_a_reused_pid_is_never_terminated_and_its_record_is_removed(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    path = _write_process_record(settings, "runner", 4242, "old-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "new-generation")
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid: pytest.fail("must not terminate a reused PID"))

    assert cli._stop_background(settings, "runner") is None
    assert not path.exists()


def test_an_unverifiable_process_is_left_untouched(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    path = _write_process_record(settings, "runner", 4242, "same-generation")

    def denied(pid):
        raise cli.ProcessIdentityError("access denied")

    monkeypatch.setattr(cli, "_process_created", denied)
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid: pytest.fail("must not terminate an unknown process"))
    with pytest.raises(SystemExit) as stopped:
        cli._stop_background(settings, "runner")
    assert stopped.value.code == 2 and path.exists()
    assert "신원을 확인할 수 없어 건드리지 않습니다" in capsys.readouterr().err


def test_up_waits_for_a_recorded_runner_instead_of_spawning_a_duplicate(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4243, "same-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "same-generation")
    monkeypatch.setattr(cli, "_health", lambda _settings: {"service": "labhq gateway", "runners": []})
    monkeypatch.setattr(cli, "_wait_for_health", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as stopped:
        cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("must not spawn a duplicate runner"))
    assert stopped.value.code == 2
    assert "runner 프로세스는 살아 있으나 gateway에 연결되지 않음" in capsys.readouterr().err


def test_down_refuses_active_work_and_force_terminates_only_recorded_trees(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    _write_process_record(settings, "gateway", 4245, "gateway-generation")
    created = {4244: "runner-generation", 4245: "gateway-generation"}
    monkeypatch.setattr(cli, "_process_created", lambda pid: created[pid])
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [{
        "id": "req_busy", "status": "running", "text": "분석 중",
        "step_progress": {"steps": {"download": "done", "analysis": "hibernating"}},
    }])
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid: killed.append(pid))

    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    assert stopped.value.code == 2 and killed == []
    error = capsys.readouterr().err
    assert "req_busy" in error and "analysis=hibernating" in error

    cli._down(settings, force=True)
    assert killed == [4244, 4245]


def test_authenticated_status_explains_a_wrong_client_token(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))

    def unauthorized(method, url, **kwargs):
        request = httpx.Request(method, url)
        return httpx.Response(401, json={"detail": "bad client token"}, request=request)

    monkeypatch.setattr(httpx, "request", unauthorized)
    with pytest.raises(SystemExit) as stopped:
        cli._health(settings)
    assert stopped.value.code == 2
    assert capsys.readouterr().err.strip() == "client_token이 이 gateway와 다릅니다."


def test_wrapper_name_is_used_in_launch_guidance(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setenv("LABHQ_LAUNCHER", r".\labhq")
    assert cli._config_command(settings, "open") == rf'.\labhq -c "{settings.config_path}" open'


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
    assert r'set "LABHQ_LAUNCHER=.\labhq"' in windows
    assert ".venv/bin/python" in posix and '"$@"' in posix
    assert "LABHQ_LAUNCHER=./labhq.sh" in posix


@pytest.mark.parametrize("argv", [["watch"], ["send", "샘플 표를 정리해 줘"]])
def test_a_closed_gateway_on_the_websocket_commands_is_one_line_and_exit_2(tmp_path, monkeypatch, capsys, argv):
    """Docs audit 2026-10-09: `send` (waiting) and `watch` open the websocket first and printed a traceback."""
    import websockets
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    config = _config(tmp_path)

    def refused(*args, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(websockets, "connect", refused)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), *argv])
    assert stopped.value.code == 2
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1 and "gateway에 연결할 수 없습니다" in lines[0] and " up`" in lines[0]

    def wrong_token(*args, **kwargs):  # the gateway accepts, then closes with 1008 "token"
        raise ConnectionClosedError(Close(1008, "token"), None)

    monkeypatch.setattr(websockets, "connect", wrong_token)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), *argv])
    assert stopped.value.code == 2
    assert "client_token을 거부" in capsys.readouterr().err


def test_down_also_refuses_a_running_follow_up_or_recruitment(tmp_path, monkeypatch, capsys):
    """#500: a follow-up on a finished request or a recruitment is not an active request, so `down` used to stop it."""
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "runner-generation")
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid: killed.append(pid))

    def gateway(method, url, **kwargs):
        request = httpx.Request(method, url)
        if "/api/requests" in url:
            return httpx.Response(200, json=[], request=request)
        if url.endswith("/api/shutdown-readiness"):
            return httpx.Response(200, request=request, json={
                "requests": [], "recruits": 1, "ready": False,
                "followups": [{"request_id": "req_done", "followup_id": "fu_1", "text": "그림 다시"}]})
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "request", gateway)
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    error = capsys.readouterr().err
    assert stopped.value.code == 2 and killed == []
    assert "req_done" in error and "진행 중 채용: 1건" in error
    cli._down(settings, force=True)
    assert killed == [4244]

    # An older gateway without the endpoint: the request list alone decides, as before.
    def old_gateway(method, url, **kwargs):
        request = httpx.Request(method, url)
        if "/api/requests" in url:
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "request", old_gateway)
    killed.clear()
    _write_process_record(settings, "runner", 4244, "runner-generation")  # the forced down removed the record
    cli._down(settings, force=False)
    assert killed == [4244]
