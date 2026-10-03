"""Staff trust the OS store on Windows (9th mock trial): an institution's TLS inspection root is in the Windows
store and not in certifi, so `requests` in a staff venv failed CERTIFICATE_VERIFY_FAILED."""

import ssl

import certifi
import pytest

from labhq.adapters.read_only import read_only_engine_env
from labhq.models import Engine, Task
from labhq.runner import system_ca
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
async def test_runner_points_staff_at_the_bundle_written_once_in_the_workspace_root(tmp_path, monkeypatch, engine):
    for name in system_ca.CA_ENV:
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: calls.append(1) or "PEM\n")
    settings = _runner_settings(tmp_path, [])
    runner, seen = _capture_runner(settings, monkeypatch, engine=engine)
    for task_id in ("t1", "t2"):
        assert (await runner.run_task(Task(id=task_id, agent_id="worker", request_id="r", prompt="q"))).ok
    env = seen["ctx"].env
    target = runner.ws_root / ".labhq-system-ca.pem"
    assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == str(target)
    assert target.read_text(encoding="ascii") == "PEM\n" and len(calls) == 1
    assert not list(runner.ws_root.glob(".labhq-system-ca.*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["runner env", "engine env", "off", "no store"])
async def test_runner_leaves_a_ca_the_pi_chose_or_a_missing_store_alone(tmp_path, monkeypatch, case):
    for name in system_ca.CA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("labhq.runner.daemon.system_ca_pem", lambda: None if case == "no store" else "PEM\n")
    settings = _runner_settings(tmp_path, [])
    if case == "runner env":
        monkeypatch.setenv("SSL_CERT_FILE", "/pi/ca.pem")
    if case == "engine env":
        settings.engines.claude_code.env = {"REQUESTS_CA_BUNDLE": "/pi/ca.pem"}
    if case == "off":
        settings.runner.system_ca_bundle = False
    runner, seen = _capture_runner(settings, monkeypatch)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
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
