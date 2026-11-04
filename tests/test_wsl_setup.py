"""WSL calls are mocked; no test contacts WSL, a gateway or GitHub."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from labhq import cli, doctor, instance_guard
from labhq.settings import Settings

READY = dict(bioinfo_agent=True, bioinfo_skill=True, java=True, nextflow=True,
             claude_cli=True, claude_login=True)


@pytest.fixture(autouse=True)
def restore_bootstrap_environment(monkeypatch):
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("PYTHONUTF8", os.environ.get("PYTHONUTF8", "1"))
    previous = os.umask(0o077)
    os.umask(previous)
    yield
    os.umask(previous)


@pytest.fixture
def settings(tmp_path):
    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({"instance": "wsl-test", "gateway": {
        "state_dir": str(tmp_path / "gateway"), "port": 43123, "url": "ws://127.0.0.1:43123",
        "runner_token": "test-only-runner-secret", "client_token": "test-only-client-secret"},
        "runner": {"state_dir": str(tmp_path / "runner"), "agents_dir": str(tmp_path / "agents"),
                   "workspace_root": str(tmp_path / "runs")}, "hpc": {"scheduler": "none"}}), encoding="utf-8")
    return Settings.load(str(config))


def health(s):
    return {"service": "labhq gateway", "identity": instance_guard.public_identity(s), "runners": ["lab-wsl"]}


def test_check_reports_each_fact_without_install(settings, monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    monkeypatch.setattr(wsl_setup, "distributions", lambda: ["Ubuntu-22.04"])
    calls = []
    def remote(s, action, **kw):
        calls.append(action)
        return {"ok": True, "java": True, "nextflow": True, "docker": False,
                "bioinfo_agent": True, "labhq": True, "registered": True}
    monkeypatch.setattr(wsl_setup, "_remote", remote)
    monkeypatch.setattr(cli, "_health", lambda s: health(s))
    rows = wsl_setup.check(settings)
    assert len(rows) == 12 and calls == ["probe"]
    assert next(r for r in rows if r["name"] == "Docker 실행")["detail"] == "없음"
    assert rows[-1]["detail"] == "ok"
    assert "test-only" not in wsl_setup.render(rows)
    monkeypatch.setattr(cli, "_health", lambda s: {**health(s), "identity": {"config_id": "other", "fingerprint": "other"}})
    assert wsl_setup.check(settings)[-1]["detail"] == "없음"


def test_doctor_collapses_unconfigured_disabled_wsl(settings, monkeypatch):
    """An available distro is not an opted-in feature and must not flood a fresh install with warnings."""
    from labhq import wsl_setup

    calls = []
    monkeypatch.setattr(doctor, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: calls.append((a, kw)) or {"ok": True, **READY})
    monkeypatch.setattr(cli, "_health", lambda s: health(s))

    result = doctor.collect(settings, require_paper2agent=False)
    rows = [row for row in result["checks"] if row["group"] == "WSL"]

    assert rows == [{"group": "WSL", "name": "선택 기능", "status": "skip",
                     "detail": "꺼짐", "hint": "labhq setup-wsl"}]
    assert calls == []
    assert "CLAUDE_CONFIG_DIR" not in doctor.render(result)


def test_doctor_checks_enabled_wsl_individually_with_actual_login_path(settings, monkeypatch):
    from labhq import wsl_setup

    data = yaml.safe_load(Path(settings.config_path).read_text(encoding="utf-8"))
    data["runner"]["wsl"] = {"enabled": True, "root": "/home/runner/labhq-wsl"}
    Path(settings.config_path).write_text(yaml.safe_dump(data), encoding="utf-8")
    settings = Settings.load(settings.config_path)
    monkeypatch.setattr(doctor, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {"ok": True, **READY, "claude_login": False})
    monkeypatch.setattr(cli, "_health", lambda s: health(s))

    result = doctor.collect(settings, require_paper2agent=False)
    rows = [row for row in result["checks"] if row["group"] == "WSL"]
    rendered = doctor.render(result)

    assert len(rows) == len(wsl_setup.LABELS)
    assert any(row["name"] == "WSL Claude 로그인" for row in rows)
    assert "/home/runner/labhq-wsl/claude-staff" in rendered
    assert "CLAUDE_CONFIG_DIR" in rendered


def test_missing_wsl_only_prints_admin_advice(settings, monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: pytest.fail("must not install"))
    before = Path(settings.config_path).read_bytes()
    report = wsl_setup.render(wsl_setup.setup(settings))
    assert "wsl --install" in report and "sudo apt install" in report
    assert Path(settings.config_path).read_bytes() == before


def test_setup_repeat_uses_same_private_config_and_no_nextflow_install(settings, monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    monkeypatch.setattr(wsl_setup, "_shared_settings", lambda s: {"workspace_root": "/mnt/c/Shared/Runs",
                                                               "policy": s.policy.model_dump()})
    monkeypatch.setattr(wsl_setup, "distributions", lambda: ["Ubuntu-22.04"])
    monkeypatch.setattr(cli, "_health", lambda s: health(s))
    calls = []
    def remote(s, action, **kwargs):
        calls.append((action, kwargs))
        return {"ok": True, **READY, "url": "ws://192.0.2.1:43123"}
    monkeypatch.setattr(wsl_setup, "_remote", remote)
    wsl_setup.setup(settings)
    first = Path(settings.config_path).read_bytes()
    wsl_setup.setup(Settings.load(settings.config_path))
    assert Path(settings.config_path).read_bytes() == first
    assert all(not kw["install_nextflow"] for action, kw in calls if action == "install")
    assert Settings.load(settings.config_path).runner.wsl.enabled


def test_save_enabled_preserves_precise_lookup_share(settings):
    from decimal import Decimal
    from labhq import wsl_setup
    from labhq.runner.lookup_admission import share_cap

    path = Path(settings.config_path)
    with path.open("a", encoding="utf-8") as config:
        config.write("\nannot:\n  request_lookup_limit: 100\n  precedent_lookup_share: 0.29999999999999999\n")
    settings = Settings.load(str(path))
    wsl_setup._save_enabled(settings)
    reloaded = Settings.load(str(path))
    assert reloaded.runner.wsl.enabled
    assert reloaded.annot.precedent_lookup_share == Decimal("0.29999999999999999")
    assert share_cap(reloaded.annot.request_lookup_limit, reloaded.annot.precedent_lookup_share) == 29


def test_token_only_in_stdin_and_errors_do_not_echo_it(settings, monkeypatch, capsys):
    from labhq import wsl_bootstrap, wsl_setup
    monkeypatch.setattr(wsl_setup, "_linux_path", lambda *a: "/mnt/c/checkout with space")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, settings.gateway.runner_token, settings.gateway.runner_token)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(wsl_setup.WslError) as error:
        wsl_setup._remote(settings, "configure", token=settings.gateway.runner_token)
    argv, opts = calls[0]
    assert settings.gateway.runner_token not in str(argv)
    assert json.loads(opts["input"])["token"] == settings.gateway.runner_token
    assert settings.gateway.runner_token not in str(error.value) + str(capsys.readouterr())


def test_linux_private_config_is_idempotent_and_separate(settings, tmp_path, monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    root = tmp_path / "wsl"
    root.mkdir()
    source = tmp_path / "checkout"
    agent_dir = source / "agents/core"
    agent_dir.mkdir(parents=True)
    (agent_dir / "bioinfo-agent.yaml").write_text("id: bioinfo-agent\nname: Bioinfo\nrole: pipeline\nengine: mock\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(wsl_bootstrap.sys, "prefix", str(root / "venv"))
    data = {"action": "configure", "root": str(root), "source": str(source), "id": "lab-wsl",
            "url": "ws://192.0.2.1:43123", "token": settings.gateway.runner_token,
            "bioinfo_agent": "/opt/bioinfo-agent", "supervisor": {"restart": True},
            "workspace_root": "/mnt/c/Shared/Runs", "policy": settings.policy.model_dump()}
    assert wsl_bootstrap.main(data)["ok"]
    config = root / "labhq.yaml"
    before = config.stat().st_mtime_ns
    assert wsl_bootstrap.main(data)["ok"] and config.stat().st_mtime_ns == before
    actual = Settings.load(str(config))
    assert actual.runner.id != settings.runner.id
    assert actual.runner.state_dir != settings.runner.state_dir
    assert actual.runner.environment == "wsl"
    assert actual.runner.workspace_root == "/mnt/c/Shared/Runs"
    assert actual.gateway.client_token == "" and actual.gateway.runner_token == settings.gateway.runner_token
    if wsl_bootstrap.os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o600


def test_wsl_tools_require_running_docker(monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    monkeypatch.setattr(doctor, "_probe", lambda argv, env, timeout: (1 if argv[0] == "docker" else 0, ""))
    assert doctor.wsl_tool_status() == {"docker": False, "nextflow": True, "java": True}


def test_start_and_stop_use_existing_supervisor(settings, tmp_path, monkeypatch):
    from labhq import wsl_bootstrap, wsl_setup
    root = tmp_path
    monkeypatch.setattr(wsl_bootstrap, "Settings", Settings, raising=False)
    monkeypatch.setattr(cli, "_process_record", lambda *a, **kw: ("missing", None, None))
    monkeypatch.setattr(cli, "_supervised", lambda *a: False)
    calls = []
    monkeypatch.setattr(cli, "_start_background", lambda s, role: calls.append(("start", role)))
    monkeypatch.setattr(cli, "_stop_background", lambda s, role: calls.append(("stop", role)))
    wsl_bootstrap.lifecycle(root, "start")
    wsl_bootstrap.lifecycle(root, "stop")
    assert calls == [("start", "runner"), ("stop", "runner")]


def test_install_is_idempotent_and_never_runs_admin_commands(tmp_path, monkeypatch):
    from labhq import wsl_bootstrap

    root = tmp_path / "wsl"
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="test"\n')
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(wsl_bootstrap.shutil, "which", lambda name: "/existing/nextflow")
    calls = []
    def run(argv, timeout=20):
        calls.append(argv)
        if argv[:3] == ["python3", "-m", "venv"]:
            python = root / "venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
        return subprocess.CompletedProcess(argv, 0, b"")
    monkeypatch.setattr(wsl_bootstrap, "run", run)
    data = {"action": "install", "root": str(root), "source": str(source), "install_nextflow": True}
    assert wsl_bootstrap.main(data)["ok"] and wsl_bootstrap.main(data)["ok"]
    assert sum("pip" in call for call in calls) == 1
    assert sum("venv" in call for call in calls) == 1
    assert all("sudo" not in call and "apt" not in call for call in calls)
