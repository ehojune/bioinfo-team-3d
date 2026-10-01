import json
import pytest
from labhq import doctor
from labhq.adapters import codex as codex_mod
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


@pytest.mark.parametrize("empty_roster", [False, True])
def test_invalid_force_engine_is_failure_and_doctor_finishes(tmp_path, monkeypatch, empty_roster):
    settings = _settings(tmp_path)
    settings.runner.force_engine = "mok"
    if empty_roster:
        (tmp_path / "agents" / "core" / "worker.yaml").unlink()
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["name"] == "runner.force_engine")
    assert row["status"] == "fail"
    assert any(r["group"] == "data" for r in result["checks"])
    assert doctor.save(result, settings).is_file()


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


def test_doctor_warns_about_parent_session_markers_and_pi_skills(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "not-reported")
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    checks = {row["name"]: row for row in result["checks"]}
    assert checks["claude_parent_session_env"]["status"] == "warn"
    assert "2" in checks["claude_parent_session_env"]["detail"]
    assert "not-reported" not in json.dumps(result)
    assert checks["codex_user_skills_leak"]["status"] == "warn"
    assert checks["codex_user_skills_leak"]["detail"] == "직원 세션이 PI 개인 skill·규칙을 읽을 수 있음"


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
    from types import SimpleNamespace
    from labhq.runner import daemon
    monkeypatch.setattr(daemon, "os", SimpleNamespace(name="nt"))
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


@pytest.mark.parametrize("case,expected", [
    ("readable", "fail"), ("traversable", "fail"), ("unc", "fail"),
    ("override", "warn"), ("denied", "ok"), ("absent", "ok"),
])
def test_doctor_reuses_posix_data_guard(tmp_path, monkeypatch, case, expected):
    import os
    from types import SimpleNamespace
    from labhq.runner import daemon

    settings = _settings(tmp_path)
    zone = tmp_path / "restricted"
    zone.mkdir()
    settings.policy.data_zones = [DataZone(path="//test-host/share" if case == "unc" else str(zone))]
    settings.policy.allow_runner_read_restricted = case == "override"
    guard_os = SimpleNamespace(name="posix", R_OK=os.R_OK, X_OK=os.X_OK,
                               path=SimpleNamespace(exists=lambda p: case != "absent", isdir=lambda p: True),
                               access=lambda p, mode: case in ("readable", "override") or
                               (case == "traversable" and mode == os.X_OK))

    def listdir(path):
        raise PermissionError()

    guard_os.listdir = listdir
    # Patch only the guard module's view, keeping pathlib on the host OS.
    monkeypatch.setattr(daemon, "os", guard_os)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["name"] == "restricted data zones")
    assert row["status"] == expected
    assert str(zone) not in json.dumps(result)
    assert not list((tmp_path / "state").glob("*.sqlite3"))


def test_doctor_runs_staff_adapter_preflight_with_engine_environment(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    (tmp_path / "agents" / "core" / "worker.yaml").write_text(
        "id: worker\nname: Worker\nrole: test\nengine: codex\n", encoding="utf-8")
    config = tmp_path / "staff-config"
    config.mkdir()
    (config / "AGENTS.md").write_text("global instructions", encoding="utf-8")
    settings.engines.codex.env = {"CODEX_HOME": str(config)}
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: "available")
    monkeypatch.setattr(doctor, "_resolve_command", lambda cmd, env, engine: cmd)
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, "version 1.2.3"))
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["group"] == "staff" and r["name"] == "worker")
    assert row["status"] == "fail" and "AGENTS.md" in row["detail"]
    assert "CODEX_HOME" in row["hint"] and "codex login" in row["hint"]
    assert str(config) not in json.dumps(result)
    settings.engines.codex.allow_global_agents_md = True
    result = doctor.collect(settings)
    assert next(r for r in result["checks"] if r["group"] == "staff" and r["name"] == "worker")["status"] == "ok"


def test_doctor_names_missing_codex_elevated_setup_without_starting_it(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: True)
    settings = _settings(tmp_path)
    (tmp_path / "agents" / "core" / "worker.yaml").write_text(
        "id: worker\nname: Worker\nrole: test\nengine: codex\n", encoding="utf-8")
    staff_home = tmp_path / "codex-staff"
    staff_home.mkdir()
    (staff_home / "auth.json").write_text("{}", encoding="utf-8")
    settings.engines.codex.env = {"CODEX_HOME": str(staff_home)}
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: "available")
    monkeypatch.setattr(doctor, "_resolve_command", lambda cmd, env, engine: cmd)
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, "version 1.2.3"))

    result = doctor.collect(settings)

    row = next(r for r in result["checks"] if r["group"] == "staff" and r["name"] == "worker")
    assert row["status"] == "fail"
    assert "elevated sandbox setup" in row["detail"] and "setup_marker.json" in row["detail"]
    assert "대화형" in row["hint"] and "elevated sandbox setup" in row["hint"]


def test_doctor_shows_enabled_dev_log_target_and_exit_conditions(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.dev_log.repo = "records/private"
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["name"] == "dev log")
    assert row["status"] == "ok"
    assert "records/private" in row["detail"]
    assert "v1.0" in row["detail"] and "20" in row["detail"] and "#69" in row["detail"]


@pytest.mark.parametrize("case", ["group_missing", "runner_group", "user_missing", "user_group"])
def test_doctor_reuses_submit_prefix_account_guard(tmp_path, monkeypatch, case):
    import sys
    from types import SimpleNamespace
    from labhq.runner import daemon

    settings = _settings(tmp_path)
    settings.hpc.submit_prefix = ["sudo"]
    settings.hpc.job_group, settings.hpc.user = "test-group", "test-user"

    def group(name):
        if case == "group_missing":
            raise KeyError(name)
        return SimpleNamespace(gr_gid=42)

    def user(name):
        if case == "user_missing":
            raise KeyError(name)
        return SimpleNamespace(pw_gid=42)

    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=group))
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=user))
    monkeypatch.setattr(daemon, "os", SimpleNamespace(name="posix", getgid=lambda: 7 if case == "runner_group" else 42,
                        getgroups=lambda: [], getgrouplist=lambda *a: [] if case == "user_group" else [42]))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["name"] == "HPC job group")
    assert row["status"] == "fail"
    assert "test-user" not in json.dumps(result) and "test-group" not in json.dumps(result)
    assert not list((tmp_path / "state").glob("*.sqlite3"))


@pytest.mark.parametrize("missing,status", [(None, "ok"), ("sacct", "warn"), ("scancel", "warn")])
def test_doctor_checks_slurm_commands(tmp_path, monkeypatch, missing, status):
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "slurm"
    tools = {"sbatch", "squeue", "sacct", "scancel"} - {missing}
    monkeypatch.setattr(doctor.shutil, "which", lambda name, **kw: name if name in tools else None)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["name"] == "scheduler")
    assert row["status"] == status
    assert row["detail"].startswith("slurm; local sbatch/squeue/sacct/scancel ")
    assert result["runner_capabilities"]["scheduler"] == "slurm" and result["runner_capabilities"]["hpc_tools"]
