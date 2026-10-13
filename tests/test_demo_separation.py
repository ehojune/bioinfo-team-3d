"""Demo isolation; real listeners always use OS-assigned ports, never 8787."""

import asyncio
import errno
import json
import re
import shutil
import socket
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi.testclient import TestClient

from labhq import cli
from labhq.adapters.base import RunContext
from labhq.adapters.mock import MockAdapter
from labhq.gateway.server import create_app
from labhq.models import AgentSpec, ApprovalRequest, Engine, Task
from labhq.runner.daemon import Runner
from labhq.settings import Settings


@contextmanager
def listener(host="127.0.0.1"):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        assert sock.getsockname()[1] != 8787
        sock.listen()
        yield sock


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "127.0.0.1", "::1"])
def test_demo_avoids_loopback_listener_on_any_bind_host(host):
    with listener() as live:
        port = live.getsockname()[1]
        assert not cli._port_available(host, port)
        assert cli._demo_web_port(host, port) != port


def test_demo_avoids_ipv6_listener():
    try:
        with listener("::1") as live:
            port = live.getsockname()[1]
            assert cli._demo_web_port("0.0.0.0", port) != port
    except OSError:
        pytest.skip("IPv6 loopback unavailable")


def test_port_checks_every_bind_address_and_ignores_unavailable_optional_ipv6(monkeypatch):
    binds = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def setsockopt(self, *_):
            pass

        def bind(self, address):
            binds.append(address[0])
            if ":" in address[0]:
                raise OSError(errno.EAFNOSUPPORT, "IPv6 disabled")

    monkeypatch.setattr(cli.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: Probe())
    assert cli._port_available("127.0.0.1", 43210)
    assert binds == ["127.0.0.1", "0.0.0.0", "::1", "::"]
    assert not cli._port_available("::", 43210), "the requested family must work"


def boot(client, path):
    return json.loads(re.search(r"window.LABHQ_BOOT=(.*?)</script>", client.get(path).text)[1])


def test_demo_boot_and_decisions_stay_out_of_live_state(tmp_path, monkeypatch):
    real = Settings()
    real.gateway.state_dir = str(tmp_path / "real-state")
    monkeypatch.setenv("LABHQ_STATE_DIR", real.gateway.state_dir)
    demo = cli._demo_settings(tmp_path / "demo")
    live_app, demo_app = create_app(real), create_app(demo, demo=True)
    live_hub, demo_hub = live_app.state.hub, demo_app.state.hub
    try:
        with TestClient(live_app) as live_client, TestClient(demo_app) as demo_client:
            for path in ("/", "/3d/"):
                assert boot(demo_client, path) == {"mode": "live", "demo": True}
                assert boot(live_client, path) == {"mode": "live"}
            approval = ApprovalRequest(agent_id="data_steward", kind="tool_permission",
                                       summary="Bash: rm -rf tmp/ (mock)")
            entry = {"approval": approval.model_dump(mode="json"), "origin": None}
            demo_hub.approvals[approval.id] = entry
            demo_hub.store.put("approval", approval.id, entry)
            response = demo_client.post(f"/api/approvals/{approval.id}",
                headers={"Authorization": f"Bearer {demo.gateway.client_token}"},
                json={"approved": True, "note": "demo timeout auto-approve"})
            assert response.status_code == 200
            headers = {"Authorization": f"Bearer {real.gateway.client_token}"}
            assert live_client.get("/api/approvals/history", headers=headers).json() == []
            assert live_hub.store.all("approval_decision") == {}
            assert demo_hub.store.all("approval_decision")
            assert "demo_status" not in live_hub.snapshot()["data"]
            assert demo_hub.snapshot()["data"]["demo_status"]["phase"] == "starting"
    finally:
        live_hub.store.close()
        demo_hub.store.close()
    # Opening the live store again must still have no demo decisions.
    restarted = create_app(real).state.hub
    try:
        assert restarted.store.all("approval_decision") == {}
    finally:
        restarted.store.close()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "127.0.0.1"])
async def test_runner_dials_the_demo_server_when_live_port_is_busy(tmp_path, host):
    if host == "::":
        try:
            with listener("::1"):
                pass
        except OSError:
            pytest.skip("IPv6 unavailable")
    with listener() as live:
        s = cli._demo_settings(tmp_path)
        shutil.copytree(cli.REPO / "agents", tmp_path / "agents")
        port = cli._demo_web_port(host, live.getsockname()[1])
        s.gateway.host, s.gateway.port = host, port
        s.gateway.url = f"ws://{cli._demo_dial_host(host)}:{port}"
        s.runner.broker_port = cli.free_port()
        app = create_app(s, demo=True)
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
        runner = Runner(s)
        serving = asyncio.create_task(server.serve())
        running = None
        try:
            async def ready():
                while not server.started:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), 5)
            running = asyncio.create_task(runner.run_forever())

            async def joined():
                while len(app.state.hub.agents) < 5:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(joined(), 10)
            assert s.runner.id in app.state.hub.runners
            assert app.state.hub.demo_status["phase"] == "starting"
        finally:
            runner.stop()
            if running:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            server.should_exit = True
            await asyncio.wait_for(serving, 5)
            app.state.hub.store.close()
            runner.store.close()


@pytest.mark.parametrize("web,delay", [(False, 0.05), (True, 2.0)])
async def test_only_web_demo_slows_mock_steps(tmp_path, monkeypatch, web, delay):
    s = cli._demo_settings(tmp_path, web=web)
    assert Settings().runner.mock_delay_s == 0.05
    assert cli._approval_delay(False, 120, web=web) == (1.0 if web else 0.3)
    assert cli._approval_delay(True, 120, web=web) == 120
    sleep = AsyncMock()
    monkeypatch.setattr("labhq.adapters.mock.asyncio.sleep", sleep)
    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.mock)
    task = Task(agent_id=agent.id, prompt="mock test", meta={"kind": "direct"})
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=s,
                     mcp_servers=[], env={}, emit=AsyncMock(), prompt=task.prompt)
    assert (await MockAdapter(s).run(ctx)).ok
    sleep.assert_awaited_once_with(delay)


async def test_countdown_is_visible_in_reconnecting_snapshot(tmp_path, monkeypatch):
    app = create_app(cli._demo_settings(tmp_path), demo=True)
    hub = app.state.hub
    snapshots = []

    async def sleep(_):
        snapshots.append(hub.snapshot()["data"]["demo_status"].copy())
    monkeypatch.setattr(cli.asyncio, "sleep", sleep)
    try:
        await cli._demo_countdown(hub, 3)
        assert snapshots == [{"phase": "waiting", "remaining_s": n} for n in (3, 2, 1)]
        await cli._demo_status(hub, "running")
        assert hub.snapshot()["data"]["demo_status"]["phase"] == "running"
    finally:
        hub.store.close()


async def test_web_demo_command_uses_selected_server_and_warns_about_changed_url(tmp_path, monkeypatch, capsys):
    from labhq.gateway import server as gateway
    from labhq.runner import daemon

    class DemoFinished(Exception):
        pass

    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    monkeypatch.setattr(cli.tempfile, "mkdtemp", lambda **kw: str(demo_dir))
    settings_factory, app_factory = cli._demo_settings, gateway.create_app
    captured = {}

    def settings(path, web=False):
        assert web
        result = settings_factory(path, web=web)
        result.runner.mock_delay_s = 0.05  # real pacing is checked separately without waiting
        return result

    def app(settings, **kwargs):
        captured["app"] = app_factory(settings, **kwargs)
        return captured["app"]

    class DemoRunner(Runner):
        def __init__(self, settings):
            super().__init__(settings)
            captured["runner"] = self

    async def finished(hub):
        assert hub.runners and any(r["status"] == "done" for r in hub.requests.values())
        raise DemoFinished

    monkeypatch.setattr(cli, "_demo_settings", settings)
    monkeypatch.setattr(cli, "_demo_countdown", finished)
    monkeypatch.setattr(gateway, "create_app", app)
    monkeypatch.setattr(daemon, "Runner", DemoRunner)
    monkeypatch.setattr(cli, "_demo_url_hosts", lambda _: ["127.0.0.1"])
    try:
        with listener() as live:
            port = live.getsockname()[1]
            with pytest.raises(DemoFinished):
                await asyncio.wait_for(cli._demo(web=True, host="0.0.0.0", port=port), 30)
            runner = captured["runner"]
            assert runner.s.gateway.port != port
            assert runner.s.gateway.url == f"ws://127.0.0.1:{runner.s.gateway.port}"
            assert runner.s.gateway.host == "0.0.0.0"
            output = capsys.readouterr().out
            assert f"{port}은 실행 중인 서비스" in output
            assert f"http://127.0.0.1:{runner.s.gateway.port}/" in output
            assert "이 주소만 여세요" in output
    finally:
        if "app" in captured:
            captured["app"].state.hub.store.close()
        if "runner" in captured:
            captured["runner"].store.close()
