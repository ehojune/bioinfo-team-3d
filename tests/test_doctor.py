import json
import pytest
from labhq import doctor
from labhq.settings import DataZone, Settings


def _settings(tmp_path):
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").write_text("id: worker\nname: Worker\nrole: test\nengine: claude_code\n", encoding="utf-8")
    return Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state"),
                                               "workspace_root": str(tmp_path / "runs"),
                                               "agents_dir": str(agents.parent)},
                                    "gateway": {"state_dir": str(tmp_path / "gateway")},
                                    "hpc": {"scheduler": "none"}})


def test_doctor_offline_missing_tools_and_manifest(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_network_check", lambda url: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(doctor, "_resolve_command", lambda cmd, env, engine: cmd)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    assert result["summary"]["fail"] == 0
    assert all(row["detail"] == "skipped" for row in result["checks"] if row["group"] == "data")
    assert any(row["name"] == "worker" and row["status"] == "warn" for row in result["checks"])
    path = doctor.save(result, settings)
    assert path == tmp_path / "state" / "capabilities.json"
    assert json.loads(path.read_text(encoding="utf-8")) == result
    assert "worker" in doctor.render(result)


def test_doctor_uses_runner_resolution_and_prefix_args(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.engines.claude_code.bin = "node"
    settings.engines.claude_code.prefix_args = ["cli.js"]
    calls = []

    def resolve(cmd, env, engine):
        calls.append((engine, cmd))
        return [str(tmp_path / "node.exe"), *cmd[1:]]

    (tmp_path / "node.exe").touch()
    monkeypatch.setattr(doctor, "_resolve_command", resolve)
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, "version 1.2.3"))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    doctor.collect(settings)
    assert ("claude_code", ["node", "cli.js"]) in calls


def test_doctor_reuses_plugin_preflight_and_detects_windows_refusal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    agent = tmp_path / "agents" / "core" / "worker.yaml"
    agent.write_text("id: worker\nname: Worker\nrole: test\nengine: claude_code\n"
                     "plugin_dirs: ['${TEST_PLUGIN_DIR}']\nallow_skills: true\n"
                     "required_skills: ['bioinfo:run']\n", encoding="utf-8")
    settings.policy.data_zones = [DataZone(path="/restricted", level="restricted")]
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.delenv("TEST_PLUGIN_DIR", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    checks = {(r["group"], r["name"]): r for r in result["checks"]}
    assert checks["config", "restricted data zones"]["status"] == "fail"
    assert "TEST_PLUGIN_DIR is not set" == checks["plugin", "worker"]["detail"]
    assert result["summary"]["fail"] == 1


def test_network_only_when_requested(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    called = []
    monkeypatch.setattr(doctor, "_network_check", lambda url: called.append(url) or True)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings, network=True)
    assert called == list(doctor.SOURCES.values())
    assert all(r["status"] == "ok" for r in result["checks"] if r["group"] == "data")


def test_inaccessible_shim_and_home_path_do_not_crash_or_leak(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_resolve_command", lambda *a: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    assert result["summary"]["fail"] == 0
    assert all(r["status"] == "warn" for r in result["checks"] if r["group"] == "engine")
    shown = doctor._safe_path(doctor.Path.home() / f"folder-{doctor.Path.home().name}" / "config.yaml")
    assert doctor.Path.home().name not in shown
    assert shown.startswith("~/")


def test_manifest_refuses_repository_state_dir(tmp_path):
    settings = _settings(tmp_path)
    settings.runner.state_dir = str(doctor.Path(__file__).resolve().parents[1] / "state")
    with pytest.raises(OSError, match="outside the repository"):
        doctor.save({"checks": []}, settings)
