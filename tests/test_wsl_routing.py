import pytest
import yaml
from labhq import cli, doctor, wsl_setup
from labhq.gateway.shutdown import WebShutdown
from labhq.gateway.server import Hub
from labhq.models import AgentSpec
from labhq.orchestrator.cso import format_capabilities
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from test_wsl_setup import READY


@pytest.fixture
def settings(tmp_path):
    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({"instance": "wsl-test", "gateway": {
        "state_dir": str(tmp_path / "gateway"), "port": 43123, "url": "ws://127.0.0.1:43123",
        "runner_token": "test-only-runner-secret", "client_token": "test-only-client-secret"},
        "runner": {"state_dir": str(tmp_path / "runner"), "agents_dir": str(tmp_path / "agents"),
                   "workspace_root": str(tmp_path / "runs")}, "hpc": {"scheduler": "none"}}), encoding="utf-8")
    return Settings.load(str(config))



@pytest.mark.parametrize("configured", [False, True])
def test_bioinfo_routes_to_wsl_after_windows_reconnect(settings, configured):
    settings.runner = type(settings.runner).model_validate({**settings.runner.model_dump(), "wsl": {"enabled": configured}})
    hub = Hub(settings)
    hub.runners["windows"] = object()
    staff = [AgentSpec(id="bioinfo-agent", name="Bioinfo", role="analysis", engine="mock").summary()]
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    if configured:
        assert hub.agent_runner["bioinfo-agent"] == "windows"
    hub.runners["lab-wsl"] = object()
    hub.set_roster("lab-wsl", staff, {"runtime_env": "wsl", "environment": {}, "wsl_readiness": READY})
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    assert hub.agent_runner["bioinfo-agent"] == "lab-wsl"
    # A WSL runner without this staff member no longer takes precedence.
    hub.set_roster("lab-wsl", [], {"runtime_env": "wsl", "environment": {}})
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    assert hub.agent_runner["bioinfo-agent"] == "windows"


def test_runner_reports_runtime_without_replacing_login_environment(settings, monkeypatch):
    settings.runner.environment = "wsl"
    settings.runner.workspace_root = "/mnt/c/Shared/Runs"
    monkeypatch.setenv("XDG_CONFIG_HOME", "/test/login-config")

    capabilities = Runner(settings).capabilities()

    assert capabilities["runtime_env"] == "wsl"
    assert capabilities["environment"]["XDG_CONFIG_HOME"] == "/test/login-config"


def test_cso_names_wsl_and_only_reports_available_tools():
    text = format_capabilities([{"id": "bioinfo-agent", "runner_id": "lab-wsl"}], {
        "lab-wsl": {"runtime_env": "wsl", "environment": {},
                    "local_software": {"tools": {"nextflow": True, "docker": False}}}})
    assert "로컬 WSL" in text and "nextflow=yes" in text and "docker=no" in text


def test_doctor_has_wsl_section(settings, monkeypatch):
    settings.runner = type(settings.runner).model_validate({**settings.runner.model_dump(), "wsl": {"enabled": True}})
    monkeypatch.setattr(doctor, "_local_process_diagnostics", lambda s: [], raising=False)
    monkeypatch.setattr(doctor, "_paper2agent_row", lambda *a, **kw: doctor._row("staff", "paper2agent", "skip", "mock", ""))
    result = doctor.collect(settings, dry_run=True)
    assert any(row["group"] == "WSL" for row in result["checks"])


def _mock_native_shutdown(monkeypatch, calls):
    monkeypatch.setattr(cli, "_active_requests", lambda _settings: [])
    monkeypatch.setattr(cli, "_shutdown_readiness",
                        lambda _settings: {"requests": [], "followups": [], "recruits": 0, "ready": True})
    monkeypatch.setattr(cli, "_process_record", lambda *args, **kwargs: ("missing", None, None))
    monkeypatch.setattr(cli, "_stop_background",
                        lambda _settings, role: calls.append(f"stop:{role}") or 900_000_001)
    monkeypatch.setattr(cli, "_unrecorded_process_still_running", lambda *args, **kwargs: None)
    monkeypatch.setattr(wsl_setup, "control",
                        lambda *_args: (_ for _ in ()).throw(wsl_setup.WslError("WSL stop failed")))


def test_force_down_stops_native_roles_before_reporting_wsl_failure(settings, monkeypatch, capsys):
    settings.runner.wsl.enabled = True
    calls = []
    _mock_native_shutdown(monkeypatch, calls)

    with pytest.raises(SystemExit) as stopped:
        cli._down(settings, force=True)

    assert stopped.value.code == 2
    assert calls == ["stop:runner", "stop:gateway"]
    assert "WSL stop failed" in capsys.readouterr().err


def test_web_shutdown_stops_native_runner_and_gateway_when_wsl_stop_fails(settings, monkeypatch):
    settings.runner.wsl.enabled = True
    calls = []
    _mock_native_shutdown(monkeypatch, calls)
    shutdown = WebShutdown(settings, hub=None, request_exit=lambda: calls.append("exit:gateway"))
    shutdown.status = "stopping"  # prepare() sets this before the background task runs
    monkeypatch.setattr(shutdown, "_start_watcher", lambda: None)

    shutdown.run(force=True)

    assert calls == ["stop:runner", "exit:gateway"]
    assert shutdown.status == "stopping"
