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


def test_phone_client_token_is_fresh():
    default = Settings().gateway.client_token
    token = cli._demo_client_token(True, default)
    assert token != default and len(token) >= 16
    assert cli._demo_client_token(False, default) == default


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
