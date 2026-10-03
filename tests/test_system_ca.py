"""Staff trust the OS store on Windows (9th mock trial): an institution's TLS inspection root is in the Windows
store and not in certifi, so `requests` in a staff venv failed CERTIFICATE_VERIFY_FAILED."""

import os
import ssl

import certifi
import pytest

from labhq.adapters.read_only import read_only_engine_env
from labhq.models import Engine, Task
from labhq.runner import system_ca
from labhq.util import merge_staff_env
from tests.test_private_paths import _capture_runner, _runner_settings

FAKE_DER = b"0\x82fake-root"


class _Store:
    def __init__(self, certs):
        self.certs = certs

    def get_ca_certs(self, binary_form=False):
        return self.certs


def test_the_bundle_is_certifi_then_the_os_store_and_only_on_windows(monkeypatch):
    monkeypatch.setattr(system_ca.ssl, "create_default_context", lambda: _Store([FAKE_DER]))
    monkeypatch.setattr(system_ca, "WINDOWS", False)
    assert system_ca.system_ca_pem() is None
    monkeypatch.setattr(system_ca, "WINDOWS", True)
    with open(certifi.where(), encoding="ascii") as handle:
        roots = handle.read().rstrip("\n") + "\n"
    # certifi stays in: Windows fetches missing public roots on demand, so its local store alone can lack one
    assert system_ca.system_ca_pem() == roots + ssl.DER_cert_to_PEM_cert(FAKE_DER)
    monkeypatch.setattr(system_ca.ssl, "create_default_context", lambda: _Store([]))
    assert system_ca.system_ca_pem() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex])
async def test_runner_points_staff_at_the_bundle_in_the_workspace_root(tmp_path, monkeypatch, engine):
    for name in system_ca.CA_ENV:
        monkeypatch.delenv(name, raising=False)
    stores = iter(["PEM\n", "PEM\nNEW INSTITUTION ROOT\n"])
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: next(stores))
    settings = _runner_settings(tmp_path, [])
    runner, seen = _capture_runner(settings, monkeypatch, engine=engine)
    target = runner.ws_root / ".labhq-system-ca.pem"
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    assert target.read_text(encoding="ascii") == "PEM\n"
    assert (await runner.run_task(Task(id="t2", agent_id="worker", request_id="r", prompt="q"))).ok
    env = seen["ctx"].env
    assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == str(target)
    # the store is read at every spawn: a root the institution replaced needs no runner restart (PR #359 review)
    assert target.read_text(encoding="ascii") == "PEM\nNEW INSTITUTION ROOT\n"
    assert not list(runner.ws_root.glob(".labhq-system-ca.*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["off", "no store"])
async def test_runner_leaves_a_disabled_or_missing_store_alone(tmp_path, monkeypatch, case):
    for name in system_ca.CA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: None if case == "no store" else "PEM\n")
    settings = _runner_settings(tmp_path, [])
    if case == "off":
        settings.runner.system_ca_bundle = False
    runner, seen = _capture_runner(settings, monkeypatch)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    assert not set(system_ca.CA_ENV) & set(seen["ctx"].env)
    assert not (runner.ws_root / ".labhq-system-ca.pem").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("source,name,other", [
    ("runner", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"),
    ("engine", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"),
])
async def test_runner_fills_the_other_ca_variable_from_the_one_the_pi_set(
        tmp_path, monkeypatch, source, name, other, read_only):
    for variable in system_ca.CA_ENV:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: "AUTO PEM\n")
    settings = _runner_settings(tmp_path, [])
    chosen = "C:/pi/chosen-ca.pem"
    if source == "runner":
        monkeypatch.setenv(name, chosen)
    else:
        settings.engines.claude_code.env = {name: chosen}
    runner, seen = _capture_runner(settings, monkeypatch)
    meta = {"kind": "consult"} if read_only else {}

    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q", meta=meta))).ok
    engine_env = settings.engines.claude_code.env
    if read_only:
        engine_env = read_only_engine_env(engine_env)[0]
    effective = merge_staff_env(dict(os.environ), engine_env, seen["ctx"].env)

    assert effective[name] == effective[other] == chosen
    assert seen["ctx"].env[other] == chosen and name not in seen["ctx"].env
    assert not (runner.ws_root / ".labhq-system-ca.pem").exists()


@pytest.mark.asyncio
async def test_runner_leaves_two_pi_ca_variables_unchanged(tmp_path, monkeypatch):
    for variable in system_ca.CA_ENV:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", "C:/pi/ssl.pem")
    settings = _runner_settings(tmp_path, [])
    settings.engines.claude_code.env = {"REQUESTS_CA_BUNDLE": "C:/pi/requests.pem"}
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: "AUTO PEM\n")
    runner, seen = _capture_runner(settings, monkeypatch)

    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    effective = merge_staff_env(dict(os.environ), settings.engines.claude_code.env, seen["ctx"].env)

    assert effective["SSL_CERT_FILE"] == "C:/pi/ssl.pem"
    assert effective["REQUESTS_CA_BUNDLE"] == "C:/pi/requests.pem"
    assert not set(system_ca.CA_ENV) & set(seen["ctx"].env)
    assert not (runner.ws_root / ".labhq-system-ca.pem").exists()


@pytest.mark.asyncio
async def test_a_bundle_a_task_rewrote_is_restored_before_the_next_spawn(tmp_path, monkeypatch):
    """Staff share the runner's account and can rewrite the file; a planted CA must not reach the next task
    (PR #359 review)."""
    for name in system_ca.CA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: "PEM\n")
    runner, seen = _capture_runner(_runner_settings(tmp_path, []), monkeypatch)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    target = runner.ws_root / ".labhq-system-ca.pem"
    target.write_text("PEM\nATTACKER CA\n", encoding="ascii")
    assert (await runner.run_task(Task(id="t2", agent_id="worker", request_id="r", prompt="q"))).ok
    assert target.read_text(encoding="ascii") == "PEM\n"
    assert seen["ctx"].env["SSL_CERT_FILE"] == str(target)


def test_a_read_only_run_keeps_the_pis_requests_bundle():
    kept = read_only_engine_env({"REQUESTS_CA_BUNDLE": "/pi/ca.pem", "SSL_CERT_FILE": "/pi/ca.pem"})[0]
    assert kept == {"REQUESTS_CA_BUNDLE": "/pi/ca.pem", "SSL_CERT_FILE": "/pi/ca.pem"}
