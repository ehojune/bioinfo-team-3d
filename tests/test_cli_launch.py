"""PI launch and recovery paths stay usable from a fresh Windows terminal."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import httpx
import pytest

from labhq import cli, doctor
from labhq.adapters.base import AgentAdapter, RunContext, RunState
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings
from tests.proc_table_fake import FakeProcessTable


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


QUESTION_CARD = {"id": "appr_hs", "kind": "question", "summary": "Hard stop (reference): 어느 genome?",
                 "detail": {"ask_id": "ask_1", "from": "bioinfo-agent", "options": ["a) GRCh38", "GRCh37", " "]}}


def test_a_question_card_with_options_needs_an_answer_and_shows_the_options(tmp_path, monkeypatch, capsys):
    # #521: the web refuses 승인 without a pick (R19); the CLI sent approved=true with an empty note instead.
    config = _config(tmp_path)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, kwargs.get("json")))
        return httpx.Response(200, json=[QUESTION_CARD] if method == "GET" else {"ok": True},
                              request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", str(config), "approve", "appr_hs"])
    assert stopped.value.code == 2 and [method for method, _ in calls] == ["GET"]
    error = capsys.readouterr().err
    assert "a) GRCh38" in error and "b) GRCh37" in error and "c)" not in error
    assert 'labhq approve appr_hs --note "a"' in error
    cli.main(["-c", str(config), "approvals"])
    out = capsys.readouterr().out
    assert "appr_hs  [question] Hard stop (reference): 어느 genome?" in out
    assert "a) GRCh38" in out and "b) GRCh37" in out
    calls.clear()
    cli.main(["-c", str(config), "approve", "appr_hs", "--note", "a"])
    cli.main(["-c", str(config), "approve", "appr_hs", "--deny"])
    assert [body for method, body in calls if method == "POST"] == [
        {"approved": True, "note": "a"}, {"approved": False, "note": ""}]


def test_watch_shows_a_revise_choice_and_a_timeout_apart_from_a_refusal(capsys):
    # #521: CP1 --choice revise read "거절" in watch/send, and a timed-out card read as the PI's refusal.
    for data in ({"id": "a", "approved": False, "note": "s2에 민감도 분석 추가", "choice": "revise"},
                 {"id": "b", "approved": False, "note": "timed out", "state": "timed_out"},
                 {"id": "c", "approved": False, "note": "closed", "state": "expired"},
                 {"id": "d", "approved": False, "note": ""}, {"id": "e", "approved": True, "note": ""}):
        cli.render({"type": "approval.resolved", "ts": 1, "data": data})
    lines = [line.split(" ", 1)[1].strip() for line in capsys.readouterr().out.splitlines()]
    assert lines == ["✅ 승인 결과: 수정 요청 s2에 민감도 분석 추가", "⏰ 승인 결과: 시간 초과 timed out",
                     "⏰ 승인 결과: 답 없이 닫힘 closed", "✅ 승인 결과: 거절", "✅ 승인 결과: 허가"]


def test_up_and_down_manage_only_their_process_records_and_clean_runner_path(tmp_path, monkeypatch, capsys):
    from labhq import windows_launch
    monkeypatch.setattr(windows_launch, "current_job", lambda: {"in_job": False})
    monkeypatch.setattr(windows_launch, "child_job_membership", lambda child, **kw: False)
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
    # `up` starts each role's supervisor (#589) and the supervisor records the role: none runs in this test.
    monkeypatch.setattr(cli, "_await_child_record", lambda *args, **kwargs: None)
    cli._up(settings, popen=popen)
    assert [call[0][-1] for call in calls] == ["gateway", "runner"]
    assert all(call[0][1:3] == ["-m", "labhq.supervisor"] for call in calls)
    assert all(call[1]["creationflags"] & 0x00000208 == 0x00000208 for call in calls)
    runner_path = calls[1][1]["env"]["PATH"].casefold()
    assert "windowsapps" not in runner_path and "appdata\\local\\python\\bin" not in runner_path
    gateway = json.loads(cli._pid_file(settings, "gateway-supervisor").read_text())
    runner = json.loads(cli._pid_file(settings, "runner-supervisor").read_text())
    assert gateway == {"pid": 4101, "role": "gateway-supervisor", "created": "created:4101",
                       "config": settings.config_path}
    assert runner == {"pid": 4102, "role": "runner-supervisor", "created": "created:4102",
                      "config": settings.config_path}
    killed = []
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    # No live gateway in tests (#515 review): the readiness answer is given, as the request list is.
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))
    cli._down(settings, force=False)
    assert killed == [4102, 4101]  # the supervisors: ending one ends the role under it
    assert not cli._pid_file(settings, "gateway").exists() and not cli._pid_file(settings, "runner").exists()
    assert not cli._pid_file(settings, "gateway-supervisor").exists()
    assert not cli._pid_file(settings, "runner-supervisor").exists()
    assert "웹 사무실:" in capsys.readouterr().out


def test_up_does_not_spawn_when_gateway_and_runner_are_healthy(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_health", lambda _settings: {
        "service": "labhq gateway", "runners": [settings.runner.id],
    })
    cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("unexpected spawn"))


def test_two_overlapping_up_calls_start_only_one_instance(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    entered = threading.Event()
    release = threading.Event()
    launched = []

    monkeypatch.setattr(cli, "_health", lambda _settings: None)
    monkeypatch.setattr(cli, "_process_record", lambda _settings, role: ("missing", None, None))

    def start(_settings, role, *, popen=None):
        launched.append(role)
        entered.set()
        assert release.wait(5)
        return 4301

    monkeypatch.setattr(cli, "_start_background", start)
    monkeypatch.setattr(cli, "_wait_for_health", lambda *args, **kwargs: {
        "service": "labhq gateway", "runners": [settings.runner.id],
    })

    first_error = []

    def first_up():
        try:
            cli._up(settings)
        except BaseException as exc:  # kept for assertion in the test thread
            first_error.append(exc)

    worker = threading.Thread(target=first_up)
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(SystemExit) as stopped:
            cli._up(settings)
        assert stopped.value.code == 2
    finally:
        release.set()
        worker.join(5)

    assert not worker.is_alive() and first_error == []
    assert launched == ["gateway"]
    assert capsys.readouterr().err.strip().splitlines() == ["다른 up/down이 진행 중입니다."]


def test_operation_lock_is_exclusive_across_processes(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    settings = Settings.load(str(config))
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys\n"
            "from labhq import cli\n"
            "from labhq.settings import Settings\n"
            "settings = Settings.load(sys.argv[1])\n"
            "with cli._operation_lock(settings):\n"
            "    print('locked', flush=True)\n"
            "    sys.stdin.readline()\n"
        ), str(config)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout and holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(cli, "_health", lambda _settings: {
            "service": "labhq gateway", "runners": [settings.runner.id],
        })
        with pytest.raises(SystemExit) as stopped:
            cli._up(settings)
        assert stopped.value.code == 2
        assert capsys.readouterr().err.strip().splitlines() == ["다른 up/down이 진행 중입니다."]
    finally:
        if holder.stdin:
            holder.stdin.write("\n")
            holder.stdin.close()
        holder.wait(timeout=5)
    assert holder.returncode == 0


def test_up_recovers_a_lock_file_left_by_a_dead_process(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    lock_path = cli._launch_state(settings) / "labhq-up-down.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text('{"pid":999999,"created_at":"2000-01-01T00:00:00Z"}\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_health", lambda _settings: {
        "service": "labhq gateway", "runners": [settings.runner.id],
    })

    cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("unexpected spawn"))

    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()


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
    monkeypatch.setattr(cli, "_terminate_tree",
                        lambda pid, **kwargs: pytest.fail("must not terminate a reused PID"))

    assert cli._stop_background(settings, "runner") is None
    assert not path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group termination")
def test_posix_stop_waits_then_kills_a_term_ignoring_process_before_removing_record(tmp_path, monkeypatch):
    settings = Settings.load(str(_config(tmp_path)))
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [sys.executable, "-c", (
            "import pathlib,signal,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            f"pathlib.Path({str(ready)!r}).write_text('ready');"
            "time.sleep(60)"
        )],
        start_new_session=True,
    )
    real_killpg = os.killpg
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        created = cli._process_created(process.pid)
        record = _write_process_record(settings, "runner", process.pid, created)
        monkeypatch.setattr(cli, "_TERMINATE_GRACE_SECONDS", 0.2)
        signals = []

        def killpg(pid, sent_signal):
            if sent_signal == signal.SIGKILL:
                assert record.exists(), "process record was removed before SIGKILL"
            signals.append(sent_signal)
            real_killpg(pid, sent_signal)

        monkeypatch.setattr(cli.os, "killpg", killpg)
        assert cli._stop_background(settings, "runner") == process.pid
        process.wait(timeout=5)
        assert signals == [signal.SIGTERM, signal.SIGKILL]
        assert not record.exists()
    finally:
        if process.poll() is None:
            real_killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_an_unverifiable_process_is_left_untouched(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    path = _write_process_record(settings, "runner", 4242, "same-generation")

    def denied(pid):
        raise cli.ProcessIdentityError("access denied")

    monkeypatch.setattr(cli, "_process_created", denied)
    monkeypatch.setattr(cli, "_terminate_tree",
                        lambda pid, **kwargs: pytest.fail("must not terminate an unknown process"))
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
    monkeypatch.setattr(cli, "_shutdown_readiness", lambda _settings: "unknown")  # no live gateway in tests
    monkeypatch.setattr(cli, "_health", lambda _settings: None)  # `down` re-checks the gateway after stopping
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))

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
    monkeypatch.setattr(cli, "_port_available", lambda host, port: port != 8787)
    monkeypatch.setattr(cli, "free_port", lambda: 54321)
    assert cli._demo_web_port("127.0.0.1", 8787) == 54321


def test_watch_renders_staff_warnings(capsys):
    cli.render({"type": "agent.log", "agent_id": "worker", "data": {"level": "alert", "text": "UAC 대기"}})
    assert "UAC 대기" in capsys.readouterr().out


def test_cli_distinguishes_the_capybara_recruiter_from_the_beaver_editor(capsys):
    cli.render({"type": "agent.status", "agent_id": "recruiter", "ts": 1, "data": {"state": "done"}})
    cli.render({"type": "agent.status", "agent_id": "editor", "ts": 1, "data": {"state": "done"}})
    cli.render({"type": "recruit.status", "ts": 1, "data": {"slug": "paper", "stage": "준비"}})
    cli.render({"type": "recruit.failed", "ts": 1, "data": {"error": "중단"}})
    lines = capsys.readouterr().out.splitlines()
    assert "카피바라 recruiter" in lines[0]
    assert "🦫 editor" in lines[1]
    assert "카피바라 인사팀" in lines[2]
    assert "카피바라 채용 실패" in lines[3]


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
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))

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



def test_down_names_a_finished_request_whose_record_is_still_being_written(tmp_path, monkeypatch, capsys):
    """#521: a cancel waiting for its turns, or a bundle copied after the terminal record, is lost by a forced stop."""
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "runner-generation")
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))

    def gateway(method, url, **kwargs):
        request = httpx.Request(method, url)
        if "/api/requests" in url:
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(200, request=request, json={
            "requests": [], "followups": [], "recruits": 0, "ready": False,
            "finishing": [{"id": "req_c", "status": "cancelled", "text": "t", "stage": "cancel"},
                          {"id": "req_b", "status": "done", "text": "t", "stage": "bundle"}]})

    monkeypatch.setattr(httpx, "request", gateway)
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    error = capsys.readouterr().err
    assert stopped.value.code == 2 and killed == []
    assert "마무리 중인 요청: req_c · 취소 기록을 쓰는 중" in error
    assert "마무리 중인 요청: req_b · 보고서 묶음 저장을 마무리하는 중" in error
    assert "다음 시작 때 이어서 만듭니다" in error  # a bundle is rebuilt at the next start (#658)
    assert "gateway가 아직 끝나지 않은 일이 있다고" not in error


def test_down_words_each_finishing_stage_with_its_age_and_does_not_wait_for_a_stuck_bundle(tmp_path, monkeypatch, capsys):
    """#658: the refusal names what is being finished and for how long; a bundle worker past the gateway's limit is
    reported as stuck and does not hold `down` back, because its bundle is rebuilt at the next start."""
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "runner-generation")
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))
    answers = {}

    def gateway(method, url, **kwargs):
        request = httpx.Request(method, url)
        if "/api/requests" in url:
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(200, request=request, json=answers["readiness"])

    monkeypatch.setattr(httpx, "request", gateway)
    answers["readiness"] = {"requests": [], "followups": [], "recruits": 0, "ready": False, "finishing": [
        {"id": "req_b", "status": "done", "stage": "bundle", "elapsed_s": 300, "stuck": False},
        {"id": "req_s", "status": "done", "stage": "result_storage"},
        {"id": "req_p", "status": "done", "stage": "pi_interpretation"}]}
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    error = capsys.readouterr().err
    assert stopped.value.code == 2 and killed == []
    assert "마무리 중인 요청: req_b · 보고서 묶음 저장을 마무리하는 중 (5분째)" in error
    assert "마무리 중인 요청: req_s · 결과 저장을 마무리하는 중" in error
    assert "마무리 중인 요청: req_p · PI 해석을 마무리하는 중" in error
    assert "요청 묶음을 복사하는 중" not in error

    answers["readiness"] = {"requests": [], "followups": [], "recruits": 0, "ready": True, "finishing": [
        {"id": "req_b", "status": "done", "stage": "bundle", "elapsed_s": 900, "stuck": True}]}
    cli._down(settings, force=False)
    error = capsys.readouterr().err
    assert killed == [4244]
    assert "멈춘 마무리: req_b" in error and "15분째" in error and "다음 시작 때 이어서 마무리합니다" in error
    assert "종료를 거부했습니다" not in error


def test_down_refuses_when_readiness_fails_or_names_a_request_the_list_missed(tmp_path, monkeypatch, capsys):
    """#515 review: a readiness error other than an old gateway's 404 refuses (fail closed), and the readiness
    answer's own requests count, so a request submitted after the list was read is not cut off."""
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "runner-generation")
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))
    answers = {}

    def gateway(method, url, **kwargs):
        request = httpx.Request(method, url)
        if "/api/requests" in url:
            return httpx.Response(200, json=[], request=request)
        status, body = answers["readiness"]
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(httpx, "request", gateway)
    for status in (500, 503):
        answers["readiness"] = (status, {"detail": "boom"})
        with pytest.raises(SystemExit) as stopped:
            cli._down(settings, force=False)
        assert stopped.value.code == 2 and killed == [] and "확인할 수 없어" in capsys.readouterr().err
    answers["readiness"] = (200, {"requests": [{"id": "req_new", "status": "running", "text": "방금 보낸 요청"}],
                                  "followups": [], "recruits": 0, "ready": False})
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    assert stopped.value.code == 2 and killed == [] and "req_new" in capsys.readouterr().err


def test_a_failed_windows_kill_still_raises_oserror_with_the_reason_and_never_starts_taskkill(monkeypatch):
    """#589 replaced the Windows tree-kill command, whose output decoding this used to cover (trial-ready 2026-10-12):
    a failure to end the recorded process must still reach `down` as an OSError carrying the reason."""
    started = []
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: started.append(args))
    table = FakeProcessTable({4242: (1, 1000)}).install(monkeypatch)
    table.refuse[4242] = "액세스가 거부되었습니다."
    with pytest.raises(OSError, match="액세스가 거부되었습니다"):
        cli._terminate_tree(4242)
    assert started == []


def test_down_on_windows_ends_the_recorded_tree_through_the_helper_and_checks_each_identity(tmp_path, monkeypatch):
    """#589: `down` ended the PI's gateway and runner with a tree collected by parent PID alone; the tree it collects
    now skips a process older than its claimed parent, and a recorded PID that changed hands before the kill is not
    touched."""
    gateway, runner, child, orphan = 910_000_245, 910_000_244, 910_000_246, 910_000_300
    table = FakeProcessTable({
        gateway: (1, 1000), child: (gateway, 1100),
        orphan: (gateway, 400),  # older than the gateway: a stale parent PID, not its child
        runner: (1, 3000),  # created after the record was written: the PID changed hands
    }).install(monkeypatch)
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", runner, "windows:2000")
    _write_process_record(settings, "gateway", gateway, "windows:1000")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_process_created", lambda pid: (
        f"windows:{table.live[pid].created}" if pid in table.live else None
    ))
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_health", lambda _settings: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: pytest.fail(f"taskkill started: {args}"))

    cli._down(settings, force=False)

    assert table.killed == [child, gateway]  # the reused runner PID and the stale-parent orphan were not touched
    assert set(table.live) == {runner, orphan}
    assert not cli._pid_file(settings, "gateway").exists() and not cli._pid_file(settings, "runner").exists()


@pytest.mark.parametrize("failure", ["terminate", "open", None])
def test_down_on_windows_keeps_the_records_when_a_child_of_the_launcher_cannot_be_ended(
        tmp_path, monkeypatch, capsys, failure):
    """The bot's P1s on #597: the venv interpreter under a launcher refused TerminateProcess, or could not even be
    opened (Access denied), while the launcher itself was ended, and `down` removed the PID records and printed
    success with the runner still alive."""
    runner, runner_child, gateway = 910_000_244, 910_000_245, 910_000_246
    table = FakeProcessTable({
        runner: (1, 2000), runner_child: (runner, 2100), gateway: (1, 1000),
    }).install(monkeypatch)
    reason = {"terminate": "액세스가 거부되었습니다.", "open": "Access is denied"}.get(failure)
    if failure == "terminate":
        table.refuse[runner_child] = reason
    elif failure == "open":
        table.unreadable.add(runner_child)
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", runner, "windows:2000")
    _write_process_record(settings, "gateway", gateway, "windows:1000")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_process_created",
                        lambda pid: f"windows:{table.live[pid].created}" if pid in table.live else None)
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_health", lambda _settings: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: pytest.fail(f"taskkill started: {args}"))

    if failure:
        with pytest.raises(SystemExit) as stopped:
            cli._down(settings, force=False)
        error = capsys.readouterr().err
        assert stopped.value.code == 2
        assert f"runner PID {runner}을 종료하지 못했습니다" in error
        assert f"PID {runner_child}: {reason}" in error
        assert cli._pid_file(settings, "runner").exists() and cli._pid_file(settings, "gateway").exists()
        assert set(table.live) == {runner, runner_child, gateway}  # nothing half-stopped: the retry can reach it
    else:
        cli._down(settings, force=False)
        assert table.killed == [runner_child, runner, gateway] and not table.live
        assert not cli._pid_file(settings, "runner").exists() and not cli._pid_file(settings, "gateway").exists()


@pytest.mark.skipif(os.name != "nt", reason="the OEM code page is a Windows thing")
def test_oem_decoding_never_raises_on_bytes_that_are_not_utf8():
    result = subprocess.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([0xbc, 0xba, 0xb0]))"],
                            capture_output=True, encoding="oem", errors="replace", timeout=30)
    assert isinstance(result.stdout, str) and result.stdout


def test_down_does_not_report_success_while_an_unrecorded_gateway_still_answers(tmp_path, monkeypatch, capsys):
    """Trial-ready 2026-10-12: a gateway started by a script has no PID record; `down` printed `기록된 프로세스 없음`
    with exit 0 while it kept serving, and the next `up` reused it."""
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_health", lambda _settings: {"service": "labhq gateway", "runners": [settings.runner.id]})
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: pytest.fail("nothing was recorded"))
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    captured = capsys.readouterr()
    assert stopped.value.code == 2
    assert "`up`이 아닌 방법으로 시작한 gateway/runner가 아직 실행 중" in captured.err and "종료하지 못했습니다" in captured.err
    assert "종료:" not in captured.out


def test_down_names_what_it_stopped_and_still_fails_when_another_gateway_keeps_answering(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "runner", 4244, "runner-generation")
    _write_process_record(settings, "gateway", 4245, "gateway-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: {4244: "runner-generation", 4245: "gateway-generation"}[pid])
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_health", lambda _settings: {"service": "labhq gateway", "runners": []})
    monkeypatch.setattr(cli, "_DOWN_RECHECK_SECONDS", 0.0)
    killed = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: killed.append(pid))
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    captured = capsys.readouterr()
    assert stopped.value.code == 2 and killed == [4244, 4245]
    assert "종료: runner PID 4244, gateway PID 4245" in captured.out
    assert "`up`이 아닌 방법으로 시작한" in captured.err


def test_down_with_nothing_running_still_says_there_was_nothing_to_stop(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_health", lambda _settings: None)
    cli._down(settings, force=False)
    assert capsys.readouterr().out.strip() == "종료: 기록된 프로세스 없음"


def test_down_waits_briefly_for_a_stopped_gateway_to_stop_answering(tmp_path, monkeypatch, capsys):
    settings = Settings.load(str(_config(tmp_path)))
    _write_process_record(settings, "gateway", 4245, "gateway-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "gateway-generation")
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    answers = iter([{"service": "labhq gateway", "runners": []}, None])  # the socket closes a moment after the kill
    monkeypatch.setattr(cli, "_health", lambda _settings: next(answers))
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: None)
    cli._down(settings, force=False)
    assert capsys.readouterr().out.strip() == "종료: gateway PID 4245"


def _remote_gateway_settings(tmp_path) -> Settings:
    settings = Settings.load(str(_config(tmp_path)))
    settings.gateway.url = "ws://10.20.30.40:8787"  # a gateway on another PC: this PC only runs the runner
    return settings


def _quiet_down(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_DOWN_RECHECK_SECONDS", 0.0)


@pytest.mark.parametrize("host", ["workstation", "192.0.2.15", "proxy.example", "unknown.example"])
def test_down_checks_url_identity_for_a_wildcard_bound_gateway(tmp_path, monkeypatch, capsys, host):
    settings = Settings.load(str(_config(tmp_path)))
    settings.gateway.host = "0.0.0.0"
    settings.gateway.url = f"wss://{host}:8787"
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "workstation")

    def addresses(name, port):
        if name == "unknown.example":
            raise cli.socket.gaierror("not resolved")
        return [(cli.socket.AF_INET, cli.socket.SOCK_STREAM, 6, "", ("192.0.2.15", 0))]

    monkeypatch.setattr(cli.socket, "getaddrinfo", addresses)
    _quiet_down(monkeypatch)
    monkeypatch.setattr(cli, "_health", lambda _: {"service": "labhq gateway", "runners": []})
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings)
    assert stopped.value.code == 2
    assert "PID 기록이 없어 종료하지 못했습니다" in capsys.readouterr().err


def test_gateway_unknown_local_interfaces_are_conservative(tmp_path, monkeypatch):
    settings = _remote_gateway_settings(tmp_path)
    monkeypatch.setattr(cli.socket, "getaddrinfo", lambda *args: (_ for _ in ()).throw(cli.socket.gaierror()))
    assert cli._gateway_is_local(settings)


def test_down_on_a_runner_only_pc_is_not_blamed_for_the_gateway_on_the_other_pc(tmp_path, monkeypatch, capsys):
    settings = _remote_gateway_settings(tmp_path)
    _write_process_record(settings, "runner", 4244, "runner-generation")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "runner-generation")
    _quiet_down(monkeypatch)
    monkeypatch.setattr(cli, "_health", lambda _settings: {"service": "labhq gateway", "runners": ["lab-pc"]})
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: None)
    cli._down(settings, force=False)  # the gateway keeps answering, but it is not ours and our runner is gone
    assert capsys.readouterr().out.strip() == "종료: runner PID 4244"


def test_down_on_a_runner_only_pc_fails_when_its_own_unrecorded_runner_is_still_connected(tmp_path, monkeypatch, capsys):
    settings = _remote_gateway_settings(tmp_path)
    _quiet_down(monkeypatch)
    monkeypatch.setattr(cli, "_health",
                        lambda _settings: {"service": "labhq gateway", "runners": [settings.runner.id]})
    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)
    assert stopped.value.code == 2
    assert f"runner({settings.runner.id})가 gateway" in capsys.readouterr().err


def test_free_ports_holds_every_socket_until_all_ports_are_drawn(monkeypatch):
    """Two `free_port()` calls in a row can return one number once the first socket is closed; a test that took the
    gateway and the broker port that way could bind the same port twice (#1238)."""
    import socket

    from labhq import util

    live, peak = set(), [0]
    real = socket.socket

    class Tracking(real):
        def bind(self, address):
            super().bind(address)
            live.add(id(self))
            peak[0] = max(peak[0], len(live))

        def close(self):
            live.discard(id(self))
            super().close()

    monkeypatch.setattr(socket, "socket", Tracking)
    ports = util.free_ports(3)
    assert peak[0] == 3 and len(set(ports)) == 3 and not live
