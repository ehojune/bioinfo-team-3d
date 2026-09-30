import json
import subprocess
from types import SimpleNamespace

import pytest

from labhq import cli


@pytest.mark.parametrize("rows", [
    [{"IPAddress": "fe80::1234", "InterfaceIndex": 8}],
    {"IPAddress": "fe80::1234", "InterfaceIndex": 8},
])
def test_windows_interface_enumeration(monkeypatch, rows):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="win32"))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps(rows))

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._interface_ipv6_addresses() == [("fe80::1234", 0, 0, 8)]
    assert "Get-NetIPAddress" in calls[0][0][-1]
    assert calls[0][1]["timeout"] == 3


def test_linux_interface_enumeration(monkeypatch):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(cli.Path, "read_text", lambda *a, **k:
                        "fe800000000000000000000000001234 08 40 20 80 eth0\n")
    assert cli._interface_ipv6_addresses() == [("fe80::1234", 0, 0, 8)]


def test_bsd_interface_enumeration(monkeypatch):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="en0: flags=8863\n\tinet6 fe80::1234%en0 prefixlen 64 scopeid 0x8\n"))
    monkeypatch.setattr(cli.socket, "if_nametoindex", lambda name: 8 if name == "en0" else 0, raising=False)
    assert cli._interface_ipv6_addresses() == [("fe80::1234%en0", 0, 0, 8)]


@pytest.mark.parametrize("failure", [OSError(), subprocess.TimeoutExpired("powershell", 3)])
def test_interface_enumeration_failure_is_safe(monkeypatch, failure):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="win32"))

    def run(*a, **k):
        raise failure

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._interface_ipv6_addresses() == []
