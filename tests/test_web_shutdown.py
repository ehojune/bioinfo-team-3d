"""Only synthetic stores and mocked process trees; never contact a running office."""
import os

import pytest
from fastapi.testclient import TestClient

from labhq import cli
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
    monkeypatch.setattr(cli, "_process_record", lambda s, role, **kw:
                        ("alive", {"pid": os.getpid() if role == "gateway" else 12345}, None))
    monkeypatch.setattr(cli, "_down", lambda s, **kw: calls.append(kw))
    monkeypatch.setattr(cli, "_terminate_tree", lambda *a, **kw: pytest.fail("real termination forbidden"))
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
    assert calls == [{"force": True}]


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
    assert calls == [{"force": False}]
    # A process that still answers must not claim to be stopped, even if down returned.
    assert client.get("/api/shutdown", headers=PI).json() == {"status": "failed"}


def test_work_arriving_before_down_recheck_aborts_shutdown(office, monkeypatch):
    client, app, calls = office
    original_down = cli._down_unlocked
    monkeypatch.setattr(cli, "_active_requests", lambda s: [])
    monkeypatch.setattr(cli, "_shutdown_readiness", lambda s: {"ready": False, "recruits": 1})
    monkeypatch.setattr(cli, "_down", original_down)
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 202
    assert app.state.shutdown.status == "failed"
    assert not calls


def test_stopping_blocks_new_mutations_and_duplicate_shutdown(office):
    client, app, calls = office
    app.state.shutdown.status = "stopping"
    assert client.post("/api/requests", json={"text": "late"}, headers=PI).status_code == 409
    assert client.post("/api/shutdown", json={}, headers=PI).status_code == 409
    assert client.get("/api/shutdown-readiness", headers=PI).status_code == 200
    assert not calls
