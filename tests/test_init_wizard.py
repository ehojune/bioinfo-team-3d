"""Init must never touch the real user's home or print credentials."""

import hashlib
import os
from pathlib import Path

import pytest
import yaml

from labhq import cli, doctor, init_wizard as wizard
from labhq.adapters import base
from labhq.runner import versions
from labhq.settings import Settings


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
    monkeypatch.chdir(tmp_path)
    return home


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


def test_scheduler_present_keeps_example_default(monkeypatch):
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in ("qsub", "qstat") else None)
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == "sge"


@pytest.mark.parametrize("missing", ["qsub", "qstat"])
def test_one_missing_scheduler_tool_suggests_none(monkeypatch, missing):
    monkeypatch.setattr(wizard.shutil, "which", lambda name, **_: name if name in ("qsub", "qstat") and name != missing else None)
    wizard.run(yes=True)
    assert _data()["hpc"]["scheduler"] == "none"


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
    other = tmp_path / "explicit" / "labhq.yaml"
    cli.main(["-c", str(other), "init", "--yes"])
    assert other.exists()


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
