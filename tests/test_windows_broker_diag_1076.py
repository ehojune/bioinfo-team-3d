"""#1076: Python's own clock beside the provider's phases, and which launch of the process this was. No PowerShell, native
API or process starts; the `subprocess.run` call and the clock reads the older broker tests count are left alone."""
import itertools
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import windows_broker as broker
from tests.test_windows_broker import world  # noqa: F401 - the fully fake native/ownership boundary
from tests.test_windows_broker_phases import CREATE, PHASES, frames


CLOCK_STEP_MS = 20


@pytest.fixture
def shell(tmp_path, monkeypatch):
    path = tmp_path / "powershell.exe"
    path.touch()
    monkeypatch.setattr(broker, "_powershell", lambda: path)
    monkeypatch.setattr(broker, "_LAUNCHES", itertools.count(1))
    return path


def create(tmp_path, diagnostics):
    return broker._create([str(tmp_path / "python.exe"), "script.py"], tmp_path, {}, diagnostics=diagnostics)


def test_a_launch_records_its_ordinal_and_the_run_call_time_on_success(shell, tmp_path, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=frames(len(PHASES)) + b'{"code":0,"pid":222}'))
    monkeypatch.setattr(broker.subprocess, "run", run)
    first, second = {}, {}
    assert create(tmp_path, first) == 222 and create(tmp_path, second) == 222
    assert (first["launch_ordinal"], second["launch_ordinal"]) == (1, 2)
    for row in (first, second):
        # `elapsed_ms` is on `time.monotonic` (about 16 ms steps on Windows) and `run_ms` on `perf_counter`.
        assert 0 <= row["run_ms"] <= row["elapsed_ms"] + CLOCK_STEP_MS, "the call is part of the launch"
        assert row["os_uptime_s"] is None or row["os_uptime_s"] >= 0


def test_a_timeout_still_records_the_run_call_time_before_it_is_re_raised(shell, tmp_path, monkeypatch):
    failure = subprocess.TimeoutExpired("fake-powershell", broker.PROVIDER_TIMEOUT, output=frames(3))
    monkeypatch.setattr(broker.subprocess, "run", Mock(side_effect=failure))
    diagnostic = {}
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        create(tmp_path, diagnostic)
    assert caught.value is failure
    assert diagnostic["launch_ordinal"] == 1 and 0 <= diagnostic["run_ms"] <= diagnostic["elapsed_ms"] + CLOCK_STEP_MS
    assert [row["phase"] for row in diagnostic["phases"]] == PHASES[:3]  # the provider's own phases are as before


def test_the_call_is_not_changed_by_the_diagnostics(shell, tmp_path, monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=frames(len(PHASES)) + b'{"code":0,"pid":222}'))
    monkeypatch.setattr(broker.subprocess, "run", run)
    create(tmp_path, {})
    run.assert_called_once()
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == broker.PROVIDER_TIMEOUT and kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == 0x08000000 and kwargs["check"] is False and isinstance(kwargs["input"], bytes)


def test_the_journal_row_of_a_failed_launch_carries_the_new_facts(world, shell, monkeypatch):  # noqa: F811
    failure = subprocess.TimeoutExpired("SECRET-command", broker.PROVIDER_TIMEOUT, output=frames(3), stderr=b"SECRET")
    monkeypatch.setattr(broker.subprocess, "run", Mock(side_effect=failure))
    world.create.side_effect = CREATE
    with pytest.raises(broker.BrokerFailure, match="provider_timeout"):
        broker.launch(world.command, state=world.state, role="gateway-supervisor", **world.options)
    text = (world.state / "logs/detached-launch.jsonl").read_text(encoding="utf-8")
    failed = [json.loads(line) for line in text.splitlines()][-1]
    assert failed["status"] == "wmi_failed" and failed["reason"] == "provider_timeout"
    provider = failed["provider"]
    assert provider["launch_ordinal"] == 1 and provider["run_ms"] >= 0 and "os_uptime_s" in provider
    assert "SECRET" not in text
