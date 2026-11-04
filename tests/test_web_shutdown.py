"""Only synthetic stores and mocked process trees; never contact a running office."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from labhq import cli
from labhq.gateway import shutdown as shutdown_module
from labhq.gateway.server import create_app
from labhq.settings import Settings


@pytest.fixture
def office(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-pi"
    settings.gateway.runner_token = "test-runner"
    app = create_app(settings)
    calls = []
    app.state.shutdown.request_exit = lambda: calls.append("exit")  # the real one signals this very process
    monkeypatch.setattr(cli, "_process_record", lambda s, role, **kw:
                        ("alive", {"pid": os.getpid() if role == "gateway" else 12345, "created": "c"}, None))
    monkeypatch.setattr(cli, "_down", lambda s, **kw: calls.append(kw))
    monkeypatch.setattr(cli, "_terminate_tree", lambda *a, **kw: pytest.fail("real termination forbidden"))
    monkeypatch.setattr(shutdown_module, "spawn_exit_watch", lambda *a, **kw: calls.append("watch"))
    client = TestClient(app)
    yield client, app, calls
    client.close()


PI = {"Authorization": "Bearer test-pi"}


@pytest.mark.parametrize("token", ["", "test-runner", "wrong"])
def test_shutdown_requires_pi_token(office, token):
    client, _, calls = office
    assert client.post("/api/shutdown", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/api/shutdown", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert not calls


@pytest.mark.parametrize("kind", ["running", "waiting_pi", "waiting_quota", "waiting_login", "waiting_for_runner",
                                 "waiting_facilities_fix", "followup", "recruit", "finishing"])
def test_busy_shutdown_refuses_and_requires_explicit_force_confirmation(office, kind):
    client, app, calls = office
    hub = app.state.hub
    if kind == "recruit":
        hub.recruits_running["r"] = {"count": 1}
    else:
        hub.requests["q"] = {"id": "q", "text": "합성 요청", "status": kind}
        if kind == "followup":
            hub.requests["q"].update(status="done", followups=[{"status": "running", "text": "질문"}])
        if kind == "finishing":
            hub.requests["q"].update(status="cancelled", cancelling={})
    response = client.post("/api/shutdown", json={}, headers=PI)
    assert response.status_code == 409
    assert response.json()["detail"]["readiness"]["ready"] is False
    assert client.post("/api/shutdown", json={"force": True}, headers=PI).status_code == 422
    assert not calls
    assert client.post("/api/shutdown", json={"force": True, "confirmation": "interrupt-running-work"}, headers=PI).status_code == 202
    assert calls == [{"force": True, "keep_gateway": True}, "watch", "exit"]


@pytest.mark.parametrize("role,state", [("gateway", "missing"), ("runner", "missing"), ("gateway", "unknown"),
                                       ("gateway", "stale"), ("runner", "stale"), ("gateway", "other")])
def test_unrecorded_or_wrong_instance_is_never_stopped(office, monkeypatch, role, state):
    client, _, calls = office
    def record(s, name, **kw):
        if name == role:
            return ("alive" if state == "other" else state), {"pid": -1}, None
        return "alive", {"pid": os.getpid()}, None
    monkeypatch.setattr(cli, "_process_record", record)
    response = client.post("/api/shutdown", json={"force": True, "confirmation": "interrupt-running-work"}, headers=PI)
    assert response.status_code == 409 and "PID 기록" in response.text
    assert not calls


def test_idle_accepts_and_background_reuses_down(office):
    client, _, calls = office
    response = client.post("/api/shutdown", json={}, headers=PI)
    assert response.status_code == 202 and response.json() == {"status": "stopping"}
    # down stops the runner and keeps this gateway, the watcher starts, and only then does the gateway end itself.
    assert calls == [{"force": False, "keep_gateway": True}, "watch", "exit"]
    # The exit is under way: still "stopping", never "failed" or an early "stopped".
    assert client.get("/api/shutdown", headers=PI).json() == {"status": "stopping"}


def test_shutdown_never_signals_or_waits_on_its_own_process(office, monkeypatch):
    """PR #576 P1: `down` signalled this gateway and waited for it from the request's own background task, while
    Uvicorn waits for that task, so every web shutdown ended in a SIGKILL after the grace period."""
    client, app, calls = office
    monkeypatch.setattr(cli, "_down", lambda s, **kw: calls.append(kw))
    monkeypatch.setattr(cli, "_wait_for_process_end", lambda *a, **kw: pytest.fail("waited for a process"))
    started = time.monotonic()
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 202
    assert time.monotonic() - started < 5
    assert calls[-1] == "exit" and app.state.shutdown.status == "stopping"


@pytest.mark.parametrize("failure", ["down", "exit"])
def test_a_failed_shutdown_reopens_the_gateway_and_stops_the_watcher(office, monkeypatch, failure):
    client, app, calls = office

    class Watcher:
        def terminate(self):
            calls.append("watcher-terminated")

    def down(s, **kw):
        if failure == "down":
            raise SystemExit(2)
        calls.append(kw)

    def exit_now():
        raise RuntimeError("could not signal")

    monkeypatch.setattr(cli, "_down", down)
    monkeypatch.setattr(shutdown_module, "spawn_exit_watch", lambda *a, **kw: calls.append("watch") or Watcher())
    app.state.shutdown.request_exit = exit_now
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 202
    assert app.state.shutdown.status == "failed"
    # An abort before the runner stopped starts no watcher; a failed exit request stops the one it started.
    assert calls == ([] if failure == "down" else [{"keep_gateway": True, "force": False}, "watch", "watcher-terminated"])
    assert client.post("/api/requests", json={"text": "later"}, headers=PI).status_code != 409


def test_a_missing_watcher_does_not_keep_the_gateway_from_shutting_down(office, monkeypatch, capsys):
    client, app, calls = office

    def refuse(*args, **kwargs):
        raise OSError("no processes left")

    monkeypatch.setattr(shutdown_module, "spawn_exit_watch", refuse)
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 202
    assert calls == [{"force": False, "keep_gateway": True}, "exit"] and app.state.shutdown.status == "stopping"
    assert "종료 감시 프로세스를 시작하지 못했습니다" in capsys.readouterr().err


def test_the_real_exit_request_refuses_to_signal_pytest():
    with pytest.raises(RuntimeError, match="pytest"):
        shutdown_module._exit_this_process()


def test_work_arriving_before_down_recheck_aborts_shutdown(office, monkeypatch):
    client, app, calls = office
    original_down = cli._down_unlocked
    monkeypatch.setattr(cli, "_active_requests", lambda s: [])
    monkeypatch.setattr(cli, "_shutdown_readiness", lambda s: {"ready": False, "recruits": 1})
    monkeypatch.setattr(cli, "_down", original_down)
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 202
    assert app.state.shutdown.status == "failed"
    assert not calls  # no watcher, no exit request, and the runner was not touched


def test_stopping_blocks_new_mutations_and_duplicate_shutdown(office):
    client, app, calls = office
    app.state.shutdown.status = "stopping"
    assert client.post("/api/requests", json={"text": "late"}, headers=PI).status_code == 409
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 409
    assert client.get("/api/shutdown-readiness", headers=PI).status_code == 200
    assert not calls


def test_the_record_may_name_this_process_or_on_windows_its_launcher(monkeypatch):
    """A Windows venv's python.exe starts the real interpreter as a child, and `up` records the launcher's PID, so a
    web shutdown refused every such instance with "PID 기록을 확인할 수 없어" (found by the real-process test)."""
    names = shutdown_module._names_this_gateway
    assert names(os.getpid()) and not names(os.getpid() + 100000)
    launcher = os.getppid()
    monkeypatch.setattr(shutdown_module, "sys", type("Sys", (), {"platform": "win32"}))
    assert names(launcher)
    monkeypatch.setattr(shutdown_module, "sys", type("Sys", (), {"platform": "linux"}))
    assert not names(launcher) or launcher == os.getpid()  # elsewhere only the process itself
