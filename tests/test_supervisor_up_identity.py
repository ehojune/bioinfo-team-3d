"""`up` with supervisors and the instance identity checks of #613 together, with fake processes only.

The gateway and runner write their own owner records and answer `/api/health` with their own identity, both computed
from the config file they were started with. The supervisor starts them with the same `-c` that `up` was given and
records them with this config's path, so `up` confirms and refuses exactly as before. These tests run `up` with a
supervisor in each of the states it can meet.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from labhq import cli, instance_guard, proc_tree
from labhq.settings import Settings

SUP = 950_000_001


def _settings(folder) -> Settings:
    config = folder / "labhq.yaml"
    config.write_text(
        "gateway:\n  client_token: private-client\n  runner_token: private-runner\n"
        f"  state_dir: {folder.as_posix()}/state\n"
        f"runner:\n  state_dir: {folder.as_posix()}/state\n"
        "hpc:\n  scheduler: none\n", encoding="utf-8")
    return Settings.load(str(config))


def _record(settings, role, pid):
    path = cli._pid_file(settings, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "role": role, "created": f"created:{pid}",
                                "config": settings.config_path}), encoding="utf-8")


@pytest.fixture(autouse=True)
def safety(monkeypatch):
    def forbidden(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"{name} reached a real process")
        return fail

    for name in ("_table", "_open", "kill_tree"):
        monkeypatch.setattr(proc_tree, name, forbidden(f"proc_tree.{name}"))
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: [])
    monkeypatch.setattr(cli, "_terminate_tree", forbidden("cli._terminate_tree"))
    monkeypatch.setattr(os, "killpg", forbidden("os.killpg"), raising=False)
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(cli, "_process_created", lambda pid: f"created:{pid}")


def _identity_health(settings, runners=()):
    return {"service": "labhq gateway", "runners": list(runners), "identity": instance_guard.public_identity(settings)}


def test_up_still_checks_whose_gateway_answers_while_a_supervisor_restarts_it(tmp_path, monkeypatch, capsys):
    """#613 identity checks and the supervisor's wait run together: the restarted gateway answers with this config's
    identity (it was started with the same `-c`), so `up` confirms it and records the config."""
    settings = _settings(tmp_path)
    _record(settings, "gateway-supervisor", SUP)
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: _identity_health(
        settings, [settings.runner.id]))

    cli._up_unlocked(settings, popen=lambda *args, **kwargs: pytest.fail("no second supervisor"))

    assert instance_guard.last_config(settings) == settings.config_path
    assert "웹 사무실:" in capsys.readouterr().out


def test_up_does_not_adopt_another_configs_gateway_that_took_the_port_from_a_restarting_one(tmp_path, monkeypatch,
                                                                                         capsys):
    settings = _settings(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = _settings(elsewhere)
    _record(settings, "gateway-supervisor", SUP)
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: _identity_health(
        other, [settings.runner.id]))

    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(settings, popen=lambda *args, **kwargs: pytest.fail("must not start anything"))

    assert stopped.value.code == 2
    assert "중단" in capsys.readouterr().err
    assert instance_guard.last_config(settings) is None  # a gateway that is someone else's is never recorded


def test_the_gateway_a_new_supervisor_started_is_confirmed_even_without_an_identity_in_its_answer(tmp_path,
                                                                                                monkeypatch):
    """`started_here` stays true when `up` starts the role through a supervisor: an answer that predates identities is
    trusted for the gateway this very `up` started."""
    settings = _settings(tmp_path)
    started = []
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_start_background", lambda s, role, *, popen=None: started.append(role))
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: {
        "service": "labhq gateway", "runners": [settings.runner.id]})

    cli._up_unlocked(settings)

    assert started == ["gateway"]
    assert instance_guard.last_config(settings) == settings.config_path


def test_up_waits_for_a_restarting_runner_and_records_the_config_once_the_gateway_is_confirmed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _record(settings, "runner-supervisor", SUP)  # the gateway answers; the runner is between two starts
    monkeypatch.setattr(cli, "_health", lambda s: _identity_health(settings, []))
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: _identity_health(
        settings, [settings.runner.id] if runner else []))

    cli._up_unlocked(settings, popen=lambda *args, **kwargs: pytest.fail("no second supervisor"))

    assert instance_guard.last_config(settings) == settings.config_path
