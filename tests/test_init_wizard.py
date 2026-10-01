"""Init must never touch the real user's home or print credentials."""

import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from labhq import cli, doctor, hpc_consult, init_wizard as wizard
from labhq.adapters import base
from labhq.runner import versions
from labhq.settings import Settings
from labhq.tools.scheduler import Scheduler
from tests.test_hpc_consult import SGE, Backend, Cluster


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    for key in ("LABHQ_CONFIG", "BIOINFO_AGENT_DIR", "CODEX_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LABHQ_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(wizard.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_probe", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected CLI")))
    monkeypatch.setattr(wizard, "_hpc_query", lambda argv: None)  # the HPC consult never queries a real cluster
    monkeypatch.setattr(wizard, "_hpc_backend", _no_cluster)
    monkeypatch.chdir(tmp_path)
    return home


def _no_cluster(hpc):
    backend = Scheduler(hpc)
    backend._run = lambda args: pytest.fail(f"unexpected scheduler command {args}")
    return backend


def _data(path=Path("config/labhq.yaml")):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_yes_accepts_defaults_and_summarizes_doctor(monkeypatch, capsys):
    monkeypatch.setattr(wizard, "_is_windows", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("prompt")))
    cli.main(["init", "--yes"])
    data = _data()
    assert data["hpc"]["scheduler"] == "none"
    assert data["policy"]["data_zones"] == []
    assert data["engines"]["codex"]["bin"] == "auto"
    output = capsys.readouterr().out
    assert "doctor: ok" in output and "fail 0" in output
    assert all(data["gateway"][key] not in output for key in ("runner_token", "client_token"))


def test_dry_run_has_no_writes_or_tokens(isolated_home, tmp_path, monkeypatch, capsys):
    (isolated_home / ".codex").mkdir()
    (isolated_home / ".codex" / "AGENTS.md").write_text("personal", encoding="utf-8")
    monkeypatch.setattr(wizard.secrets, "token_urlsafe", lambda *_: (_ for _ in ()).throw(AssertionError("token")))
    monkeypatch.setattr(doctor, "_writable", lambda *_: (_ for _ in ()).throw(AssertionError("write")))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    cli.main(["init", "--yes", "--dry-run"])
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert not (isolated_home / ".labhq").exists()
    output = capsys.readouterr().out
    assert "dry-run" in output and "codex login" in output
    assert str(isolated_home) not in output


def test_existing_config_is_preserved_even_interactively(monkeypatch):
    wizard.run(yes=True)
    target = Path("config/labhq.yaml")
    with target.open("a", encoding="utf-8") as out:
        out.write("\n# keep this comment\n")
    original = _digest(target.read_text(encoding="utf-8"))
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("prompt")))
    monkeypatch.setattr(wizard.secrets, "token_urlsafe", lambda *_: (_ for _ in ()).throw(AssertionError("token")))
    wizard.run()
    assert _digest(target.read_text(encoding="utf-8")) == original


def test_force_rotates_two_independent_random_tokens(capsys):
    wizard.run(yes=True)
    first = [_digest(_data()["gateway"][key]) for key in ("runner_token", "client_token")]
    wizard.run(yes=True, force=True)
    gateway = _data()["gateway"]
    second = [_digest(gateway[key]) for key in ("runner_token", "client_token")]
    assert len(set(first + second)) == 4
    assert all(len(gateway[key]) >= 40 for key in ("runner_token", "client_token"))
    output = capsys.readouterr().out
    assert all(gateway[key] not in output for key in ("runner_token", "client_token"))


def test_force_dry_run_preserves_existing_bytes(tmp_path):
    wizard.run(yes=True)
    target = Path("config/labhq.yaml")
    original = _digest(target.read_text(encoding="utf-8"))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    wizard.run(yes=True, force=True, dry_run=True)
    assert _digest(target.read_text(encoding="utf-8")) == original
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("windows", [False, True])
def test_staff_home_and_manual_login(isolated_home, monkeypatch, capsys, windows):
    monkeypatch.setattr(wizard, "_is_windows", lambda: windows)
    personal = isolated_home / ".codex" / "AGENTS.md"
    personal.parent.mkdir()
    personal.write_text("personal instructions", encoding="utf-8")
    wizard.run(yes=True)
    assert (isolated_home / ".labhq" / "codex-staff").is_dir()
    assert not list((isolated_home / ".labhq" / "codex-staff").iterdir())
    assert personal.read_text(encoding="utf-8") == "personal instructions"
    env = _data()["engines"]["codex"]["env"]
    assert "CODEX_HOME" in env and "codex-staff" in env["CODEX_HOME"]
    output = capsys.readouterr().out
    assert "codex login" in output
    assert ("$env:CODEX_HOME" if windows else 'CODEX_HOME="$HOME') in output
    assert str(isolated_home) not in output


def test_no_personal_agents_no_staff_home(isolated_home):
    wizard.run(yes=True)
    assert not (isolated_home / ".labhq" / "codex-staff").exists()
    assert "CODEX_HOME" not in _data()["engines"]["codex"].get("env", {})


def test_interactive_plugin_and_scheduler_are_recorded(tmp_path, monkeypatch, capsys):
    plugin = tmp_path / "private-checkout"
    plugin.mkdir()
    answers = iter(["pbs", str(plugin)])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    wizard.run()
    data = _data()
    assert data["hpc"]["scheduler"] == "pbs"
    assert data["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(plugin)
    assert str(plugin) not in capsys.readouterr().out


def test_yes_uses_plugin_environment_default(tmp_path, monkeypatch):
    plugin = tmp_path / "checkout"
    monkeypatch.setenv("BIOINFO_AGENT_DIR", str(plugin))
    wizard.run(yes=True)
    assert _data()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(plugin)


@pytest.mark.parametrize("marker,expected", [("qconf", "sge"), ("pbsnodes", "pbs")])
def test_scheduler_family_is_detected_not_assumed(monkeypatch, marker, expected):
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    tools = {"qsub", "qstat", marker}
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in tools else None)
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == expected


def test_unknown_scheduler_family_fails_with_yes(monkeypatch):
    # PBS also has qsub/qstat: keeping the SGE example default would break the first PBS job.
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in ("qsub", "qstat") else None)
    with pytest.raises(wizard.InitError):
        wizard.run(yes=True)
    assert not Path("config/labhq.yaml").exists()


def test_unknown_scheduler_family_asks_interactively(monkeypatch):
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in ("qsub", "qstat") else None)
    monkeypatch.setattr("builtins.input", lambda prompt: "pbs" if "scheduler" in prompt else "")
    wizard.run(yes=False)
    assert _data()["hpc"]["scheduler"] == "pbs"


@pytest.mark.parametrize("missing", ["qsub", "qstat"])
def test_one_missing_scheduler_tool_suggests_none(monkeypatch, missing):
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in ("qsub", "qstat") and name != missing else None)
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == "none"


@pytest.mark.parametrize("tools,env,expected", [
    ({"sbatch", "sinfo"}, {}, "slurm"),
    ({"sbatch", "sinfo", "qsub", "qstat", "pbsnodes"}, {}, "slurm"),  # Slurm's Torque wrappers
    ({"sbatch"}, {}, "none"),  # no sinfo: not enough to call it Slurm
    ({"qsub", "qstat", "pbsnodes"}, {}, "pbs"),
])
def test_slurm_is_detected_from_sbatch_and_sinfo(monkeypatch, tools, env, expected):
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in tools else None)
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == expected


@pytest.mark.parametrize("tools,env", [
    ({"sbatch", "sinfo", "qsub", "qstat", "qconf"}, {}),
    ({"sbatch", "sinfo", "qsub", "qstat"}, {"PBS_EXEC": "/opt/pbs"}),
])
def test_two_scheduler_families_are_not_guessed(monkeypatch, tools, env):
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in tools else None)
    with pytest.raises(wizard.InitError, match="Slurm"):
        wizard.run(yes=True)
    assert not Path("config/labhq.yaml").exists()
    monkeypatch.setattr("builtins.input", lambda prompt: "slurm" if "scheduler" in prompt else "")
    wizard.run(yes=False)
    assert _data()["hpc"]["scheduler"] == "slurm"


def test_invalid_answer_leaves_no_files(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "invalid")
    with pytest.raises(SystemExit) as exc:
        cli.main(["init"])
    assert exc.value.code == 1
    assert not Path("config").exists()


def test_invalid_existing_yaml_reports_no_local_path(tmp_path, capsys):
    target = tmp_path / "private-config.yaml"
    target.write_text("gateway: [", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["-c", str(target), "init", "--yes"])
    assert exc.value.code == 1
    assert str(target) not in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "gateway: ["


def test_config_flag_and_environment(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "labhq.yaml"
    monkeypatch.setenv("LABHQ_CONFIG", str(target))
    cli.main(["init", "--yes"])
    assert target.exists()
    agents_dir = Path(yaml.safe_load(target.read_text(encoding="utf-8"))["runner"]["agents_dir"])
    assert agents_dir.is_absolute()
    assert list((agents_dir / "core").glob("*.yaml"))
    other = tmp_path / "explicit" / "labhq.yaml"
    cli.main(["-c", str(other), "init", "--yes"])
    assert other.exists()
    assert Path(yaml.safe_load(other.read_text(encoding="utf-8"))["runner"]["agents_dir"]) == agents_dir


def test_packaged_template_matches_repository_copy():
    repository = Path(__file__).resolve().parents[1] / "config" / "labhq.example.yaml"
    assert wizard._template_text() == repository.read_text(encoding="utf-8")


def test_packaged_resource_works_outside_repository(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    package_archive = tmp_path / "labhq-package.zip"
    with zipfile.ZipFile(package_archive, "w") as archive:
        for path in (repository / "labhq").rglob("*.py"):
            archive.write(path, path.relative_to(repository))
        resource = repository / "labhq" / "config" / "labhq.example.yaml"
        archive.write(resource, resource.relative_to(repository))
    assert '"config/*.yaml"' in (repository / "pyproject.toml").read_text(encoding="utf-8")
    probe = (
        "import pathlib, sys; sys.path.insert(0, sys.argv[1]); "
        "from labhq.init_wizard import _template_text; "
        "from labhq import cli; "
        "assert 'runner:' in _template_text(); "
        "target = pathlib.Path(sys.argv[2]); "
        "code = 0; "
        "\ntry: cli.main(['-c', str(target), 'init', '--yes'])"
        "\nexcept SystemExit as exc: code = exc.code"
        "\nassert code == 1; assert not target.exists()"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", probe, str(package_archive), str(tmp_path / "outside" / "labhq.yaml")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_missing_roster_fails_without_writing_config(tmp_path, monkeypatch, capsys):
    target = tmp_path / "wheel-install" / "labhq.yaml"
    monkeypatch.setattr(wizard, "_find_agents_dir", lambda *_: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["-c", str(target), "init", "--yes"])
    assert exc.value.code == 1
    assert not target.exists()
    assert "roster" in capsys.readouterr().err


def test_existing_config_with_no_active_agents_fails_init(tmp_path, capsys):
    target = tmp_path / "existing.yaml"
    empty = tmp_path / "empty-agents"
    empty.mkdir()
    target.write_text(yaml.safe_dump({"runner": {"agents_dir": str(empty)}}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["-c", str(target), "init", "--yes"])
    assert exc.value.code == 1
    assert "no active agents found" in capsys.readouterr().out


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_is_windows", lambda: True)
    root = tmp_path / "app" / "OpenAI" / "Codex" / "bin"
    for folder, directory_mtime, exe_mtime in (("z-old", 100, 400), ("a-new", 200, 50)):
        directory = root / folder
        directory.mkdir(parents=True)
        exe = directory / "codex.exe"
        exe.touch()
        os.utime(exe, (exe_mtime, exe_mtime))
        os.utime(directory, (directory_mtime, directory_mtime))
    incomplete = root / "incomplete"
    incomplete.mkdir()
    os.utime(incomplete, (300, 300))
    return root, {"LOCALAPPDATA": str(root.parents[2])}


@pytest.mark.parametrize("value", ["", "auto", None])
def test_codex_auto_uses_latest_directory_mtime(tmp_path, monkeypatch, value):
    root, env = _app(tmp_path, monkeypatch)
    settings = Settings.model_validate({"engines": {"codex": {"bin": value}}})
    prompt = "first\nsecond"
    assert base._resolve_command([settings.engines.codex.bin, "exec", prompt], env, "codex") == [
        str(root / "a-new" / "codex.exe"), "exec", prompt]
    # Detection is repeated after updates rather than caching a stale hash.
    os.utime(root / "z-old", (500, 500))
    assert base._resolve_command(["auto"], env, "codex")[0] == str(root / "z-old" / "codex.exe")


def test_explicit_codex_binary_bypasses_app(tmp_path, monkeypatch):
    _, env = _app(tmp_path, monkeypatch)
    explicit = str(tmp_path / "chosen.exe")
    assert base._resolve_command([explicit], env, "codex") == [explicit]


@pytest.mark.parametrize("windows", [True, False])
def test_codex_auto_falls_back_to_path_and_npm(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(base, "_is_windows", lambda: windows)
    shim = tmp_path / "codex.cmd"
    shim.write_text('node "%~dp0\\codex.js" %*\n', encoding="utf-8")
    (tmp_path / "codex.js").touch()
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setattr(base.shutil, "which", lambda name, **_: str(shim) if name == "codex" else str(node) if name == "node.exe" else None)
    assert base._resolve_command(["auto", "exec"], {"LOCALAPPDATA": str(tmp_path / "absent")}, "codex") == [
        str(node), str(tmp_path / "codex.js"), "exec"]


def test_doctor_and_version_report_use_same_app_path(tmp_path, monkeypatch):
    root, env = _app(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    settings = Settings()
    settings.engines.codex.bin = "auto"
    calls = []
    def probe(argv, env):
        calls.append(argv)
        return 0, "codex 1.2.3"
    monkeypatch.setattr(doctor, "_probe", probe)
    monkeypatch.setattr(versions, "_probe", probe)
    result = doctor.collect(settings)
    row = next(r for r in result["checks"] if r["group"] == "engine" and r["name"] == "codex")
    assert row["status"] == "ok"
    assert "%LOCALAPPDATA%/OpenAI/Codex/bin/a-new/codex.exe" in row["detail"]
    assert str(root) not in doctor.render(result)
    assert versions.engine_cli_versions(settings, {"codex"}) == {"codex": "1.2.3"}
    assert all(argv[0] == str(root / "a-new" / "codex.exe") for argv in calls)
    assert all(argv[1:] in (["--version"], ["login", "status"]) for argv in calls)


def test_doctor_dry_run_does_not_probe_available_executable(tmp_path, monkeypatch):
    _, env = _app(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    settings = Settings()
    settings.engines.codex.bin = "auto"
    doctor.collect(settings, dry_run=True)


def test_login_command_uses_discovered_app_without_user_path(tmp_path, isolated_home, monkeypatch, capsys):
    root, env = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    (isolated_home / ".codex").mkdir()
    (isolated_home / ".codex" / "AGENTS.md").touch()
    wizard.run(yes=True, dry_run=True)
    output = capsys.readouterr().out
    assert '& "$env:LOCALAPPDATA/OpenAI/Codex/bin/a-new/codex.exe" login' in output
    assert str(root) not in output and str(isolated_home) not in output


def test_init_propagates_doctor_failures(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect", lambda *a, **kw: {"checks": [], "summary": {"ok": 0, "warn": 0, "fail": 1}})
    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "--yes"])
    assert exc.value.code == 1
    assert "fail 1" in capsys.readouterr().out


def _sge_host(monkeypatch, backend):
    for key in ("SGE_ROOT", "PBS_HOME", "PBS_EXEC"):
        monkeypatch.delenv(key, raising=False)
    tools = {"qsub", "qstat", "qconf"}
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in tools else None)
    cluster = Cluster(SGE)
    monkeypatch.setattr(wizard, "_hpc_query", cluster)
    monkeypatch.setattr(wizard, "_hpc_backend", lambda hpc: backend)
    return cluster


@pytest.mark.parametrize("answer", ["n", ""])
def test_init_consult_writes_the_draft_and_a_declined_trial_submits_nothing(monkeypatch, tmp_path, capsys,
                                                                            answer):
    # #119: read-only queries become the hpc: section; refusing the trial job submits nothing.
    backend = Backend()
    cluster = _sge_host(monkeypatch, backend)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or (answer if "시험 잡" in prompt else ""))
    wizard.run()
    hpc = _data()["hpc"]
    assert hpc["scheduler"] == "sge" and hpc["sge"] == {"pe": "smp", "mem_resource": "h_vmem",
                                                        "mem_per_slot": True, "runtime_resource": "h_rt"}
    assert all(call[0] == "qconf" for call in cluster.calls) and backend.calls == []
    assert sum("시험 잡" in prompt for prompt in asked) == 1
    output = capsys.readouterr().out
    assert "HPC 설정 초안" in output and "아무것도 제출하지 않았습니다" in output and str(tmp_path) not in output


def test_init_trial_runs_once_after_the_config_is_saved(monkeypatch, tmp_path, capsys):
    backend = Backend(polls=1)
    _sge_host(monkeypatch, backend)
    monkeypatch.setattr("builtins.input", lambda prompt: "y" if "시험 잡" in prompt else "")
    wizard.run()
    assert len(backend.submitted()) == 1 and Path("config/labhq.yaml").exists()
    output = capsys.readouterr().out
    assert "추적됐습니다. (job 777)" in output and str(tmp_path) not in output


def test_init_yes_never_offers_the_trial_job(monkeypatch, capsys):
    backend = Backend()
    _sge_host(monkeypatch, backend)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("prompt"))
    wizard.run(yes=True)
    assert backend.calls == [] and _data()["hpc"]["sge"]["pe"] == "smp"
    assert "--yes에서는 묻지 않고" in capsys.readouterr().out


def test_init_dry_run_skips_the_consult(monkeypatch, capsys):
    backend = Backend()
    cluster = _sge_host(monkeypatch, backend)
    wizard.run(yes=True, dry_run=True)
    assert cluster.calls == [] and backend.calls == []
    assert "HPC 상담 조회와 시험 잡을 건너뜁니다" in capsys.readouterr().out


def test_init_without_a_cluster_explains_local_only(capsys):
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == "none" and hpc_consult.NO_CLUSTER in capsys.readouterr().out
