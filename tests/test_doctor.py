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
    data_rows = [row for row in result["checks"] if row["group"] == "data"]
    assert len(data_rows) == 1 and data_rows[0]["status"] == "skip"
    assert any(row["name"] == "worker" and row["status"] == "warn" for row in result["checks"])
    path = doctor.save(result, settings)
    assert path == tmp_path / "state" / "capabilities.json"
    assert json.loads(path.read_text(encoding="utf-8")) == result
    assert "worker" in doctor.render(result)


@pytest.mark.parametrize("same_account,expected", [
    (True, "warn"),
    (False, "skip"),  # an owner mismatch alone is not proof of isolation (#304 review)
    (None, "skip"),
])
def test_doctor_compares_runner_with_config_owner(tmp_path, monkeypatch, same_account, expected):
    settings = _settings(tmp_path)
    config = tmp_path / "labhq.yaml"
    config.write_text("gateway: {}\n", encoding="utf-8")
    settings.config_path = str(config)
    monkeypatch.setattr(doctor, "_config_owner_is_current_user", lambda path: same_account)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)

    result = doctor.collect(settings)

    row = next(r for r in result["checks"] if r["name"] == "runner account isolation")
    assert row["status"] == expected
    assert "docs/runner-account.md" in row["hint"]
    assert result["summary"]["fail"] == 0


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
    assert checks["codex_user_skills_leak"]["status"] == "ok"
    assert checks["codex_user_skills_leak"]["detail"] == "직원 세션이 PI 개인 skill·규칙을 읽을 수 있음"


def test_doctor_checks_only_roster_engines_and_prints_readiness(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    executable = tmp_path / "claude.exe"
    executable.touch()
    resolved = []

    def resolve(command, env, engine):
        resolved.append(engine)
        return [str(executable)]

    monkeypatch.setattr(doctor, "_resolve_command", resolve)
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, "version 1.2.3"))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)

    result = doctor.collect(settings)

    assert resolved == ["claude_code"]
    assert {row["name"] for row in result["checks"] if row["group"] == "engine"} == {"claude_code"}
    network_rows = [row for row in result["checks"] if row["group"] == "data"]
    assert len(network_rows) == 1 and network_rows[0]["status"] == "skip"
    assert doctor.render(result).endswith("실행 준비: 예")


def test_doctor_dry_run_summarizes_login_skips_once(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    executable = tmp_path / "claude.exe"
    executable.touch()
    monkeypatch.setattr(doctor, "_resolve_command", lambda command, env, engine: [str(executable)])
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)

    result = doctor.collect(settings, dry_run=True)

    login_rows = [row for row in result["checks"] if row["group"] == "login"]
    assert len(login_rows) == 1
    assert login_rows[0]["status"] == "skip" and "dry-run" in login_rows[0]["detail"]


def test_doctor_readiness_summary_counts_failures():
    manifest = {"checks": [{"group": "config", "name": "file", "status": "fail",
                             "detail": "missing", "hint": "set it"}],
                "summary": {"ok": 0, "warn": 0, "fail": 1, "skip": 0}}
    assert doctor.render(manifest).endswith("실행 준비: 아니오(fail 1건)")


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


@pytest.mark.parametrize("current,expected", [
    ("labhq-runner", "ok"), (r"PC\labhq-runner", "ok"), (r"OTHERPC\labhq-runner", "warn"), ("pi", "warn"), (None, "skip"),
])
def test_doctor_checks_the_named_runner_account(tmp_path, monkeypatch, current, expected):
    settings = _settings(tmp_path)
    settings.runner.os_account = "labhq-runner"
    monkeypatch.setenv("COMPUTERNAME", "PC")
    monkeypatch.setattr(doctor, "_current_os_account", lambda: current)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    row = next(r for r in doctor.collect(settings)["checks"] if r["name"] == "runner account isolation")
    assert row["status"] == expected


def test_doctor_warns_when_the_runner_config_holds_the_client_token(tmp_path, monkeypatch):
    """#304 review: staff run as the runner account; a client token there lets them approve as the PI."""
    settings = _settings(tmp_path)
    settings.runner.os_account = "labhq-runner"
    monkeypatch.setattr(doctor, "_current_os_account", lambda: "labhq-runner")
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    settings.gateway.client_token = "a-real-token"
    names = [r["name"] for r in doctor.collect(settings)["checks"]]
    assert "runner config holds client token" in names
    settings.gateway.client_token = "change-me-client"
    names = [r["name"] for r in doctor.collect(settings)["checks"]]
    assert "runner config holds client token" not in names
    # #304 review: the default is a working token whenever the gateway still uses it, on any account.
    assert "default client token" in names
    monkeypatch.setattr(doctor, "_current_os_account", lambda: "pi")
    assert "default client token" in [r["name"] for r in doctor.collect(settings)["checks"]]
    monkeypatch.setattr(doctor, "_current_os_account", lambda: "labhq-runner")
    settings.gateway.client_token = ""
    names = [r["name"] for r in doctor.collect(settings)["checks"]]
    assert "runner config holds client token" not in names and "default client token" not in names


@pytest.mark.parametrize("rscript,python", [
    (r"C:\Program Files\R\bin\Rscript.exe", r"C:\Users\private-user\venv\Scripts\python.exe"),
    ("/opt/R/bin/Rscript", "/home/private-user/venv/bin/python"),
])
def test_local_software_summary_is_platform_neutral_and_does_not_expose_paths(monkeypatch, rscript, python):
    commands = {"Rscript": rscript, "docker": "/tools/docker", "java": "/tools/java"}
    monkeypatch.setattr(doctor.shutil, "which", lambda name: commands.get(name))

    def probe(argv, env, timeout=5):
        assert timeout <= 5
        if argv == [rscript, "--version"]:
            return 0, "Rscript (R) version 4.4.1"
        if argv == [python, "--version"]:
            return 0, "Python 3.12.7"
        return (1, "") if "gseapy" in argv[-1] else (0, "")

    monkeypatch.setattr(doctor, "_probe", probe)
    summary = doctor.local_software_summary(python)

    assert summary == {
        "r": {"available": True, "version": "4.4.1"},
        "python": {"version": "3.12.7", "packages": {
            "pandas": True, "numpy": True, "scipy": True, "matplotlib": True,
            "statsmodels": True, "scikit-learn": True, "gseapy": False, "pydeseq2": True,
        }},
        "tools": {"docker": True, "nextflow": False, "java": True, "wsl": False},
    }
    assert "private-user" not in json.dumps(summary)


def test_an_interpreter_that_does_not_answer_its_version_gets_no_package_probes(monkeypatch):
    """2026-10-08: the Store `python3` alias hung every call; eight import probes after a failed `--version` would
    cost their full timeouts before the runner connects (PR #486 review)."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    calls = []

    def probe(argv, env, timeout=5):
        calls.append(argv)
        return None, ""

    monkeypatch.setattr(doctor, "_probe", probe)
    summary = doctor.local_software_summary("/alias/python3")

    assert calls == [["/alias/python3", "--version"]]
    assert summary["python"] == {"version": "unreported",
                                 "packages": {name: False for name in doctor.LOCAL_PYTHON_PACKAGES}}


def test_staff_python_is_the_path_interpreter_not_labhqs_own(monkeypatch):
    """2026-10-04 T3 rerun: the runner summarized labhq's venv (no pandas) and the CSO asked to install pandas,
    while staff shells ran the PATH Python 3.12 that has it."""
    found = {"python": "/usr/local/bin/python"}
    seen = []

    def which(name, path=None):
        seen.append((name, path))
        return found.get(name)

    monkeypatch.setattr(doctor.shutil, "which", which)
    assert doctor.staff_python({"PATH": "/usr/local/bin"}) == "/usr/local/bin/python"
    assert seen[0] == ("python3", "/usr/local/bin")  # python3 is tried first, on the staff PATH given
    found.clear()
    assert doctor.staff_python({"PATH": "/nowhere"}) == doctor.sys.executable


def test_runner_probes_the_staff_python(monkeypatch):
    from labhq.runner import daemon

    from pathlib import Path

    src = Path(daemon.__file__).read_text(encoding="utf-8")
    # Since PR #381 the runner probes every PATH interpreter (staff_python_summary), not one staff_python().
    assert "asyncio.to_thread(staff_python_summary)" in src and "local_software_summary, sys.executable" not in src



def test_staff_python_summary_picks_the_interpreter_with_the_analysis_packages(monkeypatch):
    """The PI's PC: `python3` is a 3.14 with no analysis packages, `python` is the 3.12 with pandas (2026-10-04)."""
    paths = {"python3": "/apps/python3", "python": "/py312/python"}
    monkeypatch.setattr(doctor.shutil, "which", lambda name, path=None: paths.get(name))

    def summary(executable):
        has = executable == "/py312/python"
        return {"r": {"available": False, "version": None},
                "python": {"version": "3.12.10" if has else "3.14.7",
                           "packages": {"pandas": has, "numpy": has, "gseapy": False}},
                "tools": {}}

    monkeypatch.setattr(doctor, "local_software_summary", summary)
    picked = doctor.staff_python_summary({"PATH": "/x"})
    assert picked["python"]["command"] == "python" and picked["python"]["version"] == "3.12.10"
    paths.pop("python")
    assert doctor.staff_python_summary({"PATH": "/x"})["python"]["command"] == "python3"  # the only one left
    paths.clear()
    assert doctor.staff_python_summary({"PATH": "/x"})["python"]["command"] is None  # labhq's own, no command


def test_cso_capabilities_name_the_python_command():
    from labhq.orchestrator.cso import _format_local_software

    line = _format_local_software({"r": {"available": False}, "python": {"version": "3.12.10", "command": "python",
                                                                          "packages": {"pandas": True}}, "tools": {}})
    assert "Python=3.12.10 run as `python`" in line and "R=missing" in line
    odd = _format_local_software({"python": {"version": "3.12.10", "command": "rm -rf /", "packages": {}}})
    assert "rm -rf" not in odd  # only the three known command names are shown


@pytest.mark.parametrize(("research", "status", "phrase"), [
    ({}, "ok", "일반 lane"),
    ({"enabled": True}, "warn", "CP1 승인 뒤 단계를 실행하지 않고"),
    ({"enabled": True, "evidence_checkpoint": True, "active_packs": ["bulk_tumor_normal@3"]}, "ok",
     "bulk_tumor_normal@3"),
])
def test_doctor_says_whether_the_research_lane_runs_end_to_end(research, status, phrase):
    """Readiness R1 (2026-10-08): the PI config had no research block, and `enabled` alone stops after CP1."""
    from labhq.settings import Settings

    settings = Settings.model_validate({"research": research})
    row = doctor._research_row(settings)
    assert (row["name"], row["status"]) == ("research lane", status) and phrase in row["detail"]


def test_doctor_fails_the_research_row_on_a_pack_the_gateway_would_refuse():
    """#492 review: a retired or misspelled `id@version` passed as ok while the gateway refuses to start."""
    from labhq.settings import Settings

    settings = Settings.model_validate({"research": {"enabled": True, "evidence_checkpoint": True,
                                                     "active_packs": ["bulk_tumor_normal@99"]}})
    row = doctor._research_row(settings)
    assert row["status"] == "fail" and "bulk_tumor_normal@99" in row["detail"]


def test_a_misspelled_research_key_is_an_error_not_a_silent_default():
    from pydantic import ValidationError

    from labhq.settings import Settings

    with pytest.raises(ValidationError, match="evidence_checkpiont"):
        Settings.model_validate({"research": {"enabled": True, "evidence_checkpiont": True}})
