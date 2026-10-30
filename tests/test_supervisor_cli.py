"""How `up`, `down`, `status` and `doctor` deal with the per-role supervisors (#589), with fake processes only.

Every PID is one no machine allocates, and each real process primitive is replaced by one that fails the test, so a
slip in these paths cannot touch a real process. The same flows with real processes are in
tests/test_supervisor_process.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from labhq import cli, doctor, proc_tree
from labhq.gateway.shutdown import WebShutdown
from labhq.process_diagnostics import latest_child_exit
from labhq.settings import Settings
from tests.support.time_proxy import RuntimeProxy

SUP, CHILD = 940_000_001, 940_000_002
ROLE = "gateway"
REAL_TERMINATE_TREE = cli._terminate_tree


def _settings(tmp_path) -> Settings:
    config = tmp_path / "labhq.yaml"
    config.write_text(
        "gateway:\n  client_token: private-client\n  runner_token: private-runner\n"
        f"  state_dir: {tmp_path.as_posix()}/state\n"
        f"runner:\n  state_dir: {tmp_path.as_posix()}/state\n"
        "hpc:\n  scheduler: none\n", encoding="utf-8")
    return Settings.load(str(config))


def _record(settings, role, pid, created=None):
    path = cli._pid_file(settings, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "role": role, "created": created or f"created:{pid}",
                                "config": settings.config_path}), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def safety(monkeypatch):
    from labhq import windows_launch
    monkeypatch.setattr(windows_launch, "current_job", lambda: {"in_job": False})
    monkeypatch.setattr(windows_launch, "child_job_membership", lambda child, **kw: False)
    def forbidden(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"{name} reached a real process")
        return fail

    for name in ("_table", "_open", "kill_tree"):
        monkeypatch.setattr(proc_tree, name, forbidden(f"proc_tree.{name}"))
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: [])  # read-only: `status` lists a live launcher's children
    monkeypatch.setattr(cli, "_terminate_tree", forbidden("cli._terminate_tree"))
    monkeypatch.setattr(os, "killpg", forbidden("os.killpg"), raising=False)
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(cli, "_process_created", lambda pid: f"created:{pid}")
    monkeypatch.setattr(cli, "time", RuntimeProxy(cli.time, sleep=lambda seconds: None))


# --- `up` starts a supervisor and waits for its record ----------------------------------------------------------

def test_up_starts_the_supervisor_records_it_and_waits_for_the_role_it_starts(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    cli._mark_stopping(settings, "runner")  # left by an earlier `down`
    waited, calls = [], []
    monkeypatch.setattr(cli, "_await_child_record", lambda s, role, **kw: waited.append((role, kw)))

    def popen(command, **options):
        calls.append((command, options, options["stdout"].name))
        return type("Supervisor", (), {"pid": SUP})()

    assert cli._start_background(settings, "runner", popen=popen) == SUP

    command, options, log = calls[0]
    executable = getattr(sys, "_base_executable", sys.executable) if sys.platform == "win32" else sys.executable
    assert command == [executable, "-m", "labhq.supervisor", settings.config_path, "runner"]
    if sys.platform == "win32":
        assert options["env"]["__PYVENV_LAUNCHER__"] == sys.executable
    assert log.endswith("runner-supervisor.log") and options["stderr"] == subprocess.STDOUT
    assert options["stdin"] == subprocess.DEVNULL and options["close_fds"] is True
    assert ("creationflags" in options) != ("start_new_session" in options)
    record = json.loads(cli._pid_file(settings, "runner-supervisor").read_text(encoding="utf-8"))
    assert record == {"pid": SUP, "role": "runner-supervisor", "created": f"created:{SUP}",
                      "config": settings.config_path}
    assert not cli._pid_file(settings, "runner").exists()  # the supervisor records the role, not `up`
    assert not cli._stopping_file(settings, "runner").exists()  # starting the role again lifts the earlier stop
    assert waited == [("runner", {"supervisor_pid": SUP, "supervisor_created": f"created:{SUP}"})]


def test_up_waits_for_the_role_record_that_proves_the_supervisor_pinned_its_parents(tmp_path):
    settings = _settings(tmp_path)
    _record(settings, ROLE, CHILD)

    cli._await_child_record(settings, ROLE, supervisor_pid=SUP, supervisor_created=f"created:{SUP}", timeout=0)


def test_up_fails_the_start_and_removes_a_supervisor_that_ended_before_recording_anything(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP)
    ended = []
    monkeypatch.setattr(cli, "_process_created", lambda pid: None)  # the supervisor is gone
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: ended.append((pid, kwargs)))

    with pytest.raises(SystemExit) as stopped:
        cli._await_child_record(settings, ROLE, supervisor_pid=SUP, supervisor_created=f"created:{SUP}", timeout=5)

    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "gateway을 시작하지 못했습니다" in error and "감독 프로세스가 먼저 끝났습니다" in error
    assert "gateway-supervisor.log" in error
    assert ended == [(SUP, {"created": f"created:{SUP}"})] and not supervisor.exists()


def test_up_gives_up_on_a_supervisor_that_never_records_the_role(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    ended = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: ended.append(pid))

    with pytest.raises(SystemExit) as stopped:
        cli._await_child_record(settings, ROLE, supervisor_pid=SUP, supervisor_created=f"created:{SUP}", timeout=0)

    assert stopped.value.code == 2 and ended == [SUP]
    assert "기록을 남기지 않았습니다" in capsys.readouterr().err


@pytest.mark.parametrize("role", ["gateway", "runner"])
@pytest.mark.parametrize("identity", ["alive", "gone", "unreadable"])
def test_up_keeps_supervisor_record_and_reports_cleanup_failure(tmp_path, monkeypatch, capsys, role, identity):
    settings = _settings(tmp_path)
    supervisor = _record(settings, f"{role}-supervisor", SUP)
    saved = supervisor.read_bytes()
    calls = []

    def process_created(pid):
        if identity == "unreadable":
            raise cli.ProcessIdentityError("fixture identity unavailable")
        return f"created:{pid}" if identity == "alive" else None

    def refuse(pid, *, created=None):
        calls.append((pid, created))
        raise OSError("fixture cleanup refused")

    monkeypatch.setattr(cli, "_process_created", process_created)
    monkeypatch.setattr(cli, "_terminate_tree", refuse)

    with pytest.raises(SystemExit) as stopped:
        cli._await_child_record(settings, role, supervisor_pid=SUP,
                                supervisor_created=f"created:{SUP}", timeout=0)

    assert stopped.value.code == 2
    assert calls == [(SUP, f"created:{SUP}")]
    assert supervisor.read_bytes() == saved  # retry keeps the same PID, creation time and config
    error = capsys.readouterr().err
    assert f"{role}을 시작하지 못했습니다" in error
    assert f"{role}-supervisor PID {SUP} 정리 실패" in error
    assert "fixture cleanup refused" in error and "PID 기록을 유지" in error
    assert f"{role}-supervisor.log" in error
    assert ("감독 프로세스가 먼저 끝났습니다" if identity == "gone" else "기록을 남기지 않았습니다") in error
    if identity == "alive":
        assert cli._supervised(settings, role)  # the next up still finds the original supervisor


def test_up_does_not_start_a_second_supervisor_while_the_first_is_restarting_its_role(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)  # alive, but the role's own record is gone: a restart is pending
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: {
        "service": "labhq gateway", "runners": [settings.runner.id]})

    cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("a second supervisor would race the first"))

    assert "웹 사무실:" in capsys.readouterr().out


def test_up_reports_a_supervisor_whose_role_never_answers(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    _record(settings, "runner-supervisor", SUP)
    monkeypatch.setattr(cli, "_health", lambda s: {"service": "labhq gateway", "runners": []})
    monkeypatch.setattr(cli, "_wait_for_health", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as stopped:
        cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("must not start another supervisor"))

    assert stopped.value.code == 2
    assert "runner 감독 프로세스는 살아 있으나" in capsys.readouterr().err


def test_up_refuses_a_supervisor_record_it_cannot_check(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    path = _record(settings, "runner-supervisor", SUP)

    def denied(pid):
        raise cli.ProcessIdentityError("access denied")

    monkeypatch.setattr(cli, "_process_created", denied)
    with pytest.raises(SystemExit) as stopped:
        cli._up(settings, popen=lambda *args, **kwargs: pytest.fail("must not start anything"))

    assert stopped.value.code == 2 and path.exists()
    assert "runner-supervisor 프로세스 신원을 확인할 수 없어" in capsys.readouterr().err


# --- `down` ends the supervisor first --------------------------------------------------------------------------

def test_down_marks_the_role_then_ends_its_supervisor_then_what_is_left_of_the_role(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP)
    child = _record(settings, ROLE, CHILD)
    calls = []

    def terminate(pid, *, created=None):
        calls.append((pid, created, cli._stopping_file(settings, ROLE).exists()))

    monkeypatch.setattr(cli, "_terminate_tree", terminate)

    assert cli._stop_background(settings, ROLE) == CHILD

    # the marker is there before the first end, so a supervisor that restarts roles sees it in time
    assert calls == [(SUP, f"created:{SUP}", True), (CHILD, f"created:{CHILD}", True)]
    assert not supervisor.exists() and not child.exists()


def test_down_reports_the_supervisor_when_the_role_was_already_gone(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    _record(settings, ROLE, CHILD, created="an-earlier-process")  # its PID now belongs to someone else: stale
    ended = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: ended.append(pid))

    assert cli._stop_background(settings, ROLE) == SUP

    assert ended == [SUP]  # the stale record's PID is never touched


def test_down_does_not_touch_a_role_that_ended_with_its_supervisor_and_reports_the_roles_pid(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    child = _record(settings, ROLE, CHILD)
    ended = []
    identity = {SUP: f"created:{SUP}", CHILD: f"created:{CHILD}"}
    monkeypatch.setattr(cli, "_process_created", lambda pid: identity.get(pid))

    def terminate(pid, **kwargs):
        ended.append(pid)
        identity.pop(pid)
        identity.pop(CHILD, None)  # ending the supervisor's tree ended the role too, and its PID may be reused now

    monkeypatch.setattr(cli, "_terminate_tree", terminate)

    assert cli._stop_background(settings, ROLE) == CHILD

    assert ended == [SUP] and not child.exists()  # the role was not ended a second time through a stale record


def test_down_waits_until_the_later_supervisor_has_really_ended(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP, created=f"windows:{SUP}")
    child = _record(settings, ROLE, CHILD, created=f"windows:{CHILD}")
    alive = {SUP: f"windows:{SUP}", CHILD: f"windows:{CHILD}"}
    ended, waited = [], []
    monkeypatch.setattr(cli, "_process_created", lambda pid: alive.get(pid))
    monkeypatch.setattr(cli.sys, "platform", "win32")

    def terminate(pid, **kwargs):
        ended.append(pid)
        assert pid == SUP
        alive.pop(CHILD)  # the role exits first; its supervisor is still winding down
        return proc_tree.KillResult(killed=[pid])

    def wait_for_end(pid, created, timeout):
        waited.append((pid, created, timeout, dict(alive)))
        alive.pop(pid)
        return True

    monkeypatch.setattr(proc_tree, "kill_tree", terminate)
    monkeypatch.setattr(cli, "_wait_for_process_end", wait_for_end)
    monkeypatch.setattr(cli, "_terminate_tree", REAL_TERMINATE_TREE)

    assert cli._stop_background(settings, ROLE) == CHILD

    assert ended == [SUP]
    assert waited == [(SUP, f"windows:{SUP}", cli._TERMINATE_GRACE_SECONDS,
                       {SUP: f"windows:{SUP}"})]
    assert not alive and not supervisor.exists() and not child.exists()


def test_down_timeout_names_the_role_and_remaining_pid_and_fails(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    child = _record(settings, ROLE, CHILD, created=f"windows:{CHILD}")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_process_created", lambda pid: f"windows:{pid}")
    monkeypatch.setattr(proc_tree, "kill_tree", lambda *args, **kwargs: proc_tree.KillResult(killed=[CHILD]))
    monkeypatch.setattr(cli, "_wait_for_process_end", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "_terminate_tree", REAL_TERMINATE_TREE)

    with pytest.raises(SystemExit) as stopped:
        cli._stop_background(settings, ROLE)

    assert stopped.value.code == 2 and child.exists()
    error = capsys.readouterr().err
    assert ROLE in error and f"PID {CHILD}" in error and "끝나지" in error


def test_down_stops_nothing_more_when_the_supervisor_cannot_be_ended(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP)
    child = _record(settings, ROLE, CHILD)
    ended = []

    def refuse(pid, **kwargs):
        ended.append(pid)
        raise OSError(f"PID {pid}: Access is denied")

    monkeypatch.setattr(cli, "_terminate_tree", refuse)

    with pytest.raises(SystemExit) as stopped:
        cli._stop_background(settings, ROLE)

    assert stopped.value.code == 2 and ended == [SUP]
    assert supervisor.exists() and child.exists()  # both records stay, so a retry finds them
    assert "gateway-supervisor PID" in capsys.readouterr().err


def test_down_without_a_supervisor_still_ends_a_role_started_the_old_way(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    child = _record(settings, ROLE, CHILD)
    ended = []
    monkeypatch.setattr(cli, "_terminate_tree", lambda pid, **kwargs: ended.append(pid))

    assert cli._stop_background(settings, ROLE) == CHILD

    assert ended == [CHILD] and not child.exists() and not cli._stopping_file(settings, ROLE).exists()


def test_down_checks_every_supervisor_before_it_ends_anything(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    _record(settings, "runner", CHILD)
    path = _record(settings, "gateway-supervisor", SUP)

    def identity(pid):
        if pid == SUP:
            raise cli.ProcessIdentityError("access denied")
        return f"created:{pid}"

    monkeypatch.setattr(cli, "_process_created", identity)
    monkeypatch.setattr(cli, "_active_requests", lambda s: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda s: {"requests": [], "followups": [], "recruits": 0, "ready": True})

    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=False)

    assert stopped.value.code == 2 and path.exists()  # the runner, whose own record was fine, was not ended first
    assert "gateway-supervisor 프로세스 신원을 확인할 수 없어" in capsys.readouterr().err


def test_the_web_shutdown_tells_the_gateways_supervisor_before_it_asks_the_gateway_to_exit(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "_down", lambda s, **kwargs: seen.append("runner stopped"))
    shutdown = WebShutdown(settings, hub=None, request_exit=lambda: seen.append(
        f"exit asked, marked={cli._stopping_file(settings, 'gateway').exists()}"))
    monkeypatch.setattr(shutdown, "_start_watcher", lambda: None)

    shutdown.run(False)

    assert seen == ["runner stopped", "exit asked, marked=True"] and shutdown.status != "failed"


# --- `status` and `doctor` ---------------------------------------------------------------------------------------

def _exit_row(time, code, **extra):
    return json.dumps({"time": time, "pid": SUP, "role": "gateway-supervisor", "event": "child_exit",
                       "child": ROLE, "child_pid": CHILD, "code": code, "code_hex": None, "signal": None,
                       "attempt": 1, "uptime_s": 61.5, "stop_requested": False, **extra})


def _write_logs(settings, *lines):
    logs = cli._launch_state(settings) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "gateway-supervisor-diagnostics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(settings, name=ROLE):
    return next(row for row in cli._local_process_diagnostics(settings) if row["name"] == name)


def test_status_shows_the_supervisor_and_the_last_exit_code_and_time_of_the_role(tmp_path):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    _record(settings, ROLE, CHILD)
    _write_logs(settings, _exit_row("2026-10-14T01:00:00+00:00", 0),
                json.dumps({"event": "heartbeat", "pid": SUP}),
                _exit_row("2026-10-15T01:48:05+00:00", 1),
                json.dumps({"event": "heartbeat", "pid": SUP}))

    row = _row(settings)

    assert row["status"] == "ok"
    assert f"감독 PID {SUP} 실행 중" in row["detail"]
    assert f"마지막 종료 2026-10-15T01:48:05+00:00 code=1 (PID {CHILD}, 가동 61.5초)" in row["detail"]
    assert "2026-10-14T01:00:00" not in row["detail"]


@pytest.mark.parametrize("extra,shown", [
    ({"code": 3221225786, "code_hex": "0xC000013A"}, "code=3221225786 (0xC000013A)"),
    ({"code": -15, "signal": "SIGTERM"}, "code=-15 SIGTERM"),
    ({"stop_requested": True}, "종료 요청(down·웹 종료)"),
])
def test_the_exit_line_names_a_windows_status_a_signal_or_a_requested_stop(tmp_path, extra, shown):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    _record(settings, ROLE, CHILD)
    row = json.loads(_exit_row("2026-10-15T01:48:05+00:00", 1))
    row.update(extra)
    _write_logs(settings, json.dumps(row))

    assert shown in _row(settings)["detail"]


def test_a_role_started_before_supervisors_existed_says_so_and_is_not_a_warning(tmp_path):
    settings = _settings(tmp_path)
    _record(settings, ROLE, CHILD)

    row = _row(settings)

    assert row["status"] == "ok"
    assert "감독 없음(감독 도입 전 방식으로 시작됨" in row["detail"] and "마지막 종료 기록 없음" in row["detail"]


def test_a_dead_supervisor_over_a_running_role_is_a_warning_and_the_record_is_not_deleted(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP, created="an-earlier-process")
    _record(settings, ROLE, CHILD)

    row = _row(settings)

    assert row["status"] == "warn" and f"감독 PID {SUP} 프로세스 없음" in row["detail"]
    assert supervisor.exists()  # reading never removes a record


def test_a_supervisor_whose_identity_cannot_be_read_is_a_warning_over_a_running_role(tmp_path, monkeypatch):
    """Nobody can say the role is protected then (the bot's P2 on #617): warn, not ok."""
    settings = _settings(tmp_path)
    supervisor = _record(settings, "gateway-supervisor", SUP)
    _record(settings, ROLE, CHILD)

    def identity(pid):
        if pid == SUP:
            raise cli.ProcessIdentityError("access denied")
        return f"created:{pid}"

    monkeypatch.setattr(cli, "_process_created", identity)

    row = _row(settings)

    assert row["status"] == "warn"
    assert "감독 신원 확인 불가 — access denied" in row["detail"]
    assert f"PID {CHILD} 실행 중" in row["detail"]
    assert supervisor.exists()


def test_a_supervisor_record_of_another_config_is_a_warning_over_a_running_role(tmp_path):
    settings = _settings(tmp_path)
    path = _record(settings, "gateway-supervisor", SUP)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["config"] = str(tmp_path / "another.yaml")
    path.write_text(json.dumps(record), encoding="utf-8")
    _record(settings, ROLE, CHILD)

    row = _row(settings)

    assert row["status"] == "warn" and "감독 신원 확인 불가" in row["detail"] and "다른 설정 파일로 실행 중" in row["detail"]


def test_a_role_between_restarts_still_shows_up_with_its_supervisor(tmp_path):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    _write_logs(settings, _exit_row("2026-10-15T01:48:05+00:00", 1))

    row = _row(settings)

    assert row["status"] == "warn" and "프로세스 기록 없음(감독이 다시 시작하는 중일 수 있음)" in row["detail"]
    assert f"감독 PID {SUP} 실행 중" in row["detail"] and "code=1" in row["detail"]


def test_a_role_with_no_record_at_all_is_not_listed(tmp_path):
    assert not [row for row in cli._local_process_diagnostics(_settings(tmp_path)) if row["name"] == ROLE]


def test_doctor_lists_the_same_rows(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    _record(settings, ROLE, CHILD)
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)

    rows = [row for row in doctor.collect(settings)["checks"] if row["group"] == "process"]

    assert [row["name"] for row in rows] == [ROLE] and f"감독 PID {SUP}" in rows[0]["detail"]


def test_the_last_exit_is_read_from_a_bounded_tail_and_skips_what_it_cannot_parse(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    assert latest_child_exit(logs, ROLE) is None  # no file
    old = _exit_row("2026-01-01T00:00:00+00:00", 9)
    filler = json.dumps({"event": "heartbeat", "pid": SUP, "rss_bytes": 1, "handles": 2})
    body = [old] + [filler] * 3000 + ["not json", _exit_row("2026-10-15T01:48:05+00:00", 4), '{"event": "child_ex']
    (logs / "gateway-supervisor-diagnostics.jsonl").write_text("\n".join(body), encoding="utf-8")

    last = latest_child_exit(logs, ROLE)

    assert last["code"] == 4  # the row older than the tail window is out of reach, the newest one is not
