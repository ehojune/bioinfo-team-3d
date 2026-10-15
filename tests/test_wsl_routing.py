import pytest
import yaml
from labhq import cli, doctor
from labhq.gateway.server import Hub
from labhq.models import AgentSpec
from labhq.orchestrator.cso import format_capabilities
from labhq.runner.daemon import Runner
from labhq.settings import Settings


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
    staff = [AgentSpec(id="bioinfo-agent", name="Bioinfo", role="analysis", engine="mock").summary()]
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    if configured:
        assert "bioinfo-agent" not in hub.agent_runner
    hub.set_roster("lab-wsl", staff, {"runtime_env": "wsl", "environment": {}})
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    assert hub.agent_runner["bioinfo-agent"] == "lab-wsl"
    # An offline WSL runner must not silently hand Linux work to Windows.
    hub.set_roster("lab-wsl", [], {"runtime_env": "wsl", "environment": {}})
    hub.set_roster("windows", staff, {"runtime_env": "windows", "environment": {}})
    assert "bioinfo-agent" not in hub.agent_runner


def test_runner_reports_runtime_without_replacing_login_environment(settings, monkeypatch):
    settings.runner.environment = "wsl"
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
