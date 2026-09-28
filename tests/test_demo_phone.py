"""Phone demo options and approval fallback without a listening server."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from labhq import cli
from labhq.settings import Settings


@pytest.mark.parametrize(
    ("args", "web", "phone", "host"),
    [([], False, False, "127.0.0.1"),
     (["--web"], True, False, "127.0.0.1"),
     (["--phone"], True, True, "0.0.0.0"),
     (["--phone", "--host", "127.0.0.1"], True, True, "127.0.0.1")],
)
def test_demo_arguments(monkeypatch, args, web, phone, host):
    demo = AsyncMock()
    monkeypatch.setattr(cli, "_demo", demo)
    monkeypatch.setattr(cli.Settings, "load", lambda _: Settings())
    cli.main(["demo", *args])
    demo.assert_awaited_once_with(web, 8787, phone, host, 120)


@pytest.mark.parametrize("field", ["client_token", "runner_token"])
def test_exposed_demo_tokens_are_fresh(field):
    default = getattr(Settings().gateway, field)
    token = cli._demo_token(True, default)
    assert token != default and len(token) >= 16
    assert cli._demo_token(False, default) == default


def test_demo_url_hosts(monkeypatch):
    monkeypatch.setattr(cli, "_lan_ipv4_addresses", lambda: ["192.168.0.10"])
    monkeypatch.setattr(cli, "_lan_ipv6_addresses", lambda: ["[2001:db8::10]"])
    assert cli._demo_url_hosts("127.0.0.1") == ["127.0.0.1"]
    assert cli._demo_url_hosts("::1") == ["[::1]"]
    assert cli._demo_url_hosts("::") == ["[2001:db8::10]"]
    assert cli._demo_url_hosts("0.0.0.0") == ["192.168.0.10"]
    assert cli._demo_url_hosts("192.168.1.4") == ["192.168.1.4"]
    assert cli._demo_url_hosts("fe80::1") == ["[fe80::1]"]


def test_lan_ipv6_addresses_prefer_non_local_and_keep_family(monkeypatch):
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "lab")
    monkeypatch.setattr(cli.socket, "socket", lambda *_args, **_kw: pytest.fail("address discovery opened a socket"))
    monkeypatch.setattr(cli.socket, "getaddrinfo", lambda *_args, **_kw: [
        (cli.socket.AF_INET6, 0, 0, "", ("::1", 0, 0, 0)),
        (cli.socket.AF_INET6, 0, 0, "", ("fe80::2", 0, 0, 7)),
        (cli.socket.AF_INET6, 0, 0, "", ("fd00::3", 0, 0, 0)),
        (cli.socket.AF_INET6, 0, 0, "", ("2001:db8::4", 0, 0, 0)),
        (cli.socket.AF_INET, 0, 0, "", ("192.168.1.4", 0)),
    ])
    addresses = cli._lan_ipv6_addresses()
    assert addresses == ["[2001:db8::4]", "[fd00::3]"]
    assert cli._phone_url_lines(addresses, 8787, "demo-token") == [
        f"http://{ip}:8787/3d?token=demo-token  http://{ip}:8787/?token=demo-token"
        for ip in addresses
    ]


def test_lan_ipv6_fallback_is_ipv6(monkeypatch):
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "lab")
    monkeypatch.setattr(cli.socket, "getaddrinfo", lambda *_args, **_kw: [])
    assert cli._demo_url_hosts("::") == ["[::1]"]
    monkeypatch.setattr(cli.socket, "getaddrinfo", lambda *_args, **_kw: [
        (cli.socket.AF_INET6, 0, 0, "", ("fe80::2", 0, 0, 7)),
    ])
    assert cli._demo_url_hosts("::") == ["[fe80::2%257]"]


def test_mock_approval_timeout_follows_policy(monkeypatch, tmp_path):
    from labhq.adapters.base import RunContext
    from labhq.adapters.mock import MockAdapter
    from labhq.models import AgentSpec, Engine, Task

    s = Settings()
    s.policy.approvals.timeout_s = 150  # what phone mode sets for a 120 s tap fallback
    sent = []

    async def fake_broker(self, ctx, path, payload):
        sent.append((path, payload))
        return {"approved": True}

    async def emit(*_):
        pass

    monkeypatch.setattr(MockAdapter, "_broker", fake_broker)
    agent = AgentSpec(id="data_steward", name="D", role="test", engine=Engine("mock"), builtin_mcp=[])
    task = Task(agent_id=agent.id, prompt="x [needs-approval]", meta={"kind": "direct"})
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=s, mcp_servers=[], env={},
                     emit=emit, prompt=task.prompt)
    asyncio.run(MockAdapter(s).run(ctx))
    approvals = [p for path, p in sent if path == "/approval"]
    assert approvals and approvals[0]["timeout_s"] == 150


def test_custom_approval_timeout(monkeypatch):
    demo = AsyncMock()
    monkeypatch.setattr(cli, "_demo", demo)
    monkeypatch.setattr(cli.Settings, "load", lambda _: Settings())
    cli.main(["demo", "--phone", "--approve-timeout", "5"])
    demo.assert_awaited_once_with(True, 8787, True, "0.0.0.0", 5)
    with pytest.raises(SystemExit):
        cli.main(["demo", "--phone", "--approve-timeout", "0"])


def test_lan_addresses_and_url_lines(monkeypatch):
    class Datagram:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def connect(self, _):
            pass

        def getsockname(self):
            return ("192.168.1.4", 4567)

    monkeypatch.setattr(cli.socket, "gethostname", lambda: "lab")
    monkeypatch.setattr(cli.socket, "getaddrinfo", lambda *_args, **_kw: [
        (cli.socket.AF_INET, 0, 0, "", ("127.0.0.1", 0)),
        (cli.socket.AF_INET, 0, 0, "", ("10.0.0.2", 0)),
        (cli.socket.AF_INET, 0, 0, "", ("192.168.1.4", 0)),
    ])
    monkeypatch.setattr(cli.socket, "socket", lambda *_args: Datagram())
    addresses = cli._lan_ipv4_addresses()
    assert addresses == ["10.0.0.2", "192.168.1.4"]
    assert cli._phone_url_lines(addresses, 8787, "demo-token") == [
        f"http://{ip}:8787/3d?token=demo-token  http://{ip}:8787/?token=demo-token"
        for ip in addresses
    ]
    assert cli._phone_url_lines([], 8787, "demo-token") == [
        "http://127.0.0.1:8787/3d?token=demo-token  http://127.0.0.1:8787/?token=demo-token"
    ]


def test_approval_delay_and_resolved_fallback(capsys):
    assert cli._approval_delay(False, 120) == 0.3
    assert cli._approval_delay(True, 120) == 120
    assert cli._approval_delay(True, 5) == 5

    class Hub:
        def __init__(self):
            self.approvals = {"a": {}}
            self.calls = []

        async def resolve_approval(self, aid, approved, note):
            self.calls.append((aid, approved, note))
            self.approvals.pop(aid)

    async def exercise():
        hub = Hub()
        fallback = asyncio.create_task(cli._demo_auto_approve(hub, "a", 0.01, True))
        await hub.resolve_approval("a", False, "human denied")
        await fallback
        assert hub.calls == [("a", False, "human denied")]
        assert capsys.readouterr().out == ""

        hub = Hub()
        await cli._demo_auto_approve(hub, "a", 0, True)
        assert hub.calls == [("a", True, "demo timeout auto-approve")]
        assert "자동 승인" in capsys.readouterr().out

    asyncio.run(exercise())


def test_tap_publish_forwards_runner_sequence():
    calls, seen = [], []

    async def publish(ev, runner_id=None, runner_seq=None):
        calls.append((ev["type"], runner_id, runner_seq))

    tap = cli._tap_publish(publish, lambda ev: seen.append(ev["type"]))
    asyncio.run(tap({"type": "agent.status"}, runner_id="local", runner_seq=7))
    asyncio.run(tap({"type": "runner.online"}))
    assert calls == [("agent.status", "local", 7), ("runner.online", None, None)]
    assert seen == ["agent.status", "runner.online"]


def test_demo_dial_host_matches_bind_family():
    assert cli._demo_dial_host("127.0.0.1") == "127.0.0.1"
    assert cli._demo_dial_host("0.0.0.0") == "127.0.0.1"
    assert cli._demo_dial_host("::1") == "[::1]"
    assert cli._demo_dial_host("::") == "[::1]"
    assert cli._demo_dial_host("192.168.1.4") == "192.168.1.4"
