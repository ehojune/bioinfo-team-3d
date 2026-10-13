"""Read-only dev log checks never grant publication permission."""

import asyncio
import json

import httpx
import pytest

from labhq import doctor
from labhq.gateway.server import create_app
from labhq.settings import Settings


def config(tmp_path, monkeypatch):
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "gateway")
    cfg.runner.state_dir = str(tmp_path / "runner")
    cfg.runner.agents_dir = str(tmp_path / "no-agents")
    cfg.dev_log.repo = "records/example"
    cfg.github.token_env = "DEVLOG_TEST_TOKEN"
    cfg.github.api_url = "https://github.example/api/v3"
    monkeypatch.setenv(cfg.github.token_env, "test-secret-value")
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    return cfg


@pytest.mark.parametrize("case,expected", [
    ("private", "private"), ("public", "public"), ("401", "unknown"),
    ("timeout", "unknown"), ("malformed", "unknown"), ("internal", "unknown"),
])
@pytest.mark.asyncio
async def test_visibility_read_only(tmp_path, monkeypatch, caplog, case, expected):
    from labhq.integrations.visibility import dev_log_visibility

    cfg = config(tmp_path, monkeypatch)
    calls = []

    def reply(request):
        calls.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://github.example/api/v3/repos/records/example"
        assert request.headers["Authorization"] == "Bearer test-secret-value"
        if case == "timeout":
            raise httpx.ReadTimeout("test-secret-value", request=request)
        if case == "401":
            return httpx.Response(401, json={"message": "test-secret-value"})
        if case == "malformed":
            return httpx.Response(200, json=["test-secret-value"])
        return httpx.Response(200, json={"visibility": case})

    result = await dev_log_visibility(cfg, httpx.MockTransport(reply))
    assert result == expected and len(calls) == 1
    assert "test-secret-value" not in result + caplog.text


@pytest.mark.asyncio
async def test_visibility_total_timeout_and_event_loop(tmp_path, monkeypatch):
    from labhq.integrations.visibility import dev_log_visibility

    cfg = config(tmp_path, monkeypatch)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def reply(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(dev_log_visibility(cfg, httpx.MockTransport(reply), timeout_s=0.05))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()  # The event loop remains free while the GET is pending.
    assert await asyncio.wait_for(task, 1) == "unknown"
    assert cancelled.is_set()


@pytest.mark.parametrize("case", ["disabled", "no_repo", "no_token"])
@pytest.mark.asyncio
async def test_visibility_skips_network(tmp_path, monkeypatch, case):
    from labhq.integrations.visibility import dev_log_visibility

    cfg = config(tmp_path, monkeypatch)
    if case == "disabled":
        cfg.dev_log.enabled = False
    elif case == "no_repo":
        cfg.dev_log.repo = None
    else:
        monkeypatch.delenv(cfg.github.token_env)

    def unexpected(request):
        pytest.fail("visibility must not make a network request")

    assert await dev_log_visibility(cfg, httpx.MockTransport(unexpected)) == (
        "unknown" if case == "no_token" else "skipped")


@pytest.mark.parametrize("private,code,expected", [(True, 200, "ok"), (False, 200, "warn"), (None, 401, "warn")])
def test_doctor_visibility_and_exit_conditions(tmp_path, monkeypatch, private, code, expected):
    cfg = config(tmp_path, monkeypatch)
    transport = httpx.MockTransport(lambda r: httpx.Response(code, json={"private": private}))
    result = doctor.collect(cfg, github_transport=transport)
    row = next(row for row in result["checks"] if row["name"] == "dev log")
    assert row["status"] == expected
    assert all(text in row["detail"] for text in ("records/example", "v1.0", "20", "#69"))
    assert "test-secret-value" not in json.dumps(result)


@pytest.mark.parametrize("case", ["disabled", "no_repo", "no_token"])
def test_doctor_without_remote_check(tmp_path, monkeypatch, case):
    cfg = config(tmp_path, monkeypatch)
    if case == "disabled":
        cfg.dev_log.enabled = False
    elif case == "no_repo":
        cfg.dev_log.repo = None
    else:
        monkeypatch.delenv(cfg.github.token_env)

    def unexpected(request):
        pytest.fail("doctor must not make a network request")

    result = doctor.collect(cfg, github_transport=httpx.MockTransport(unexpected))
    rows = [row for row in result["checks"] if row["name"] == "dev log"]
    assert len(rows) == (0 if case == "disabled" else 1)
    if rows:
        assert rows[0]["status"] == "warn" and "unknown" in rows[0]["detail"]


@pytest.mark.parametrize("case", ["private", "public", "401", "timeout", "disabled", "no_repo", "no_token"])
@pytest.mark.asyncio
async def test_gateway_warns_once_per_instance(tmp_path, monkeypatch, caplog, case):
    cfg = config(tmp_path, monkeypatch)
    cfg.dev_log.allow_public = True  # This explicit override must not hide the warning.
    if case == "disabled":
        cfg.dev_log.enabled = False
    if case == "no_repo":
        cfg.dev_log.repo = None
    if case == "no_token":
        monkeypatch.delenv(cfg.github.token_env)
    calls = []

    def reply(request):
        calls.append(request.method)
        if case == "timeout":
            raise httpx.ReadTimeout("test-secret-value", request=request)
        return httpx.Response(401 if case == "401" else 200,
                              json={"private": case == "private", "message": "test-secret-value"})

    for index in range(2):
        cfg.gateway.state_dir = str(tmp_path / str(index))
        app = create_app(cfg, httpx.MockTransport(reply))
        for _ in range(2):
            for startup in app.router.on_startup:
                await startup()
        hub = app.state.hub
        warnings = [event for event in hub.events if event.get("data", {}).get("code") == "dev_log_visibility"]
        assert len(warnings) == (1 if case in {"public", "401", "timeout", "no_token"} else 0)
        assert "test-secret-value" not in json.dumps(list(hub.events)) + caplog.text
        for shutdown in app.router.on_shutdown:
            await shutdown()
    assert calls == ([] if case in {"disabled", "no_repo", "no_token"} else ["GET", "GET"])
