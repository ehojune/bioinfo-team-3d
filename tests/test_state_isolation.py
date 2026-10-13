"""Never use the operator's state, including explicit paths and child processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from labhq.gateway.server import create_app
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.mark.parametrize("via_env", [False, True], ids=["explicit", "LABHQ_CONFIG"])
@pytest.mark.parametrize("exists", [False, True], ids=["missing", "existing"])
def test_config_load_guard_precedes_file_io(tmp_path, monkeypatch, via_env, exists):
    """PR #554 / 4256352456: neither stat nor content reads may reach the operator config."""
    home = tmp_path / "operator"
    config = home / ".labhq" / "config.yaml"
    if exists:
        config.parent.mkdir(parents=True)
        config.write_text("gateway:\n  client_token: synthetic-only\n", encoding="utf-8")
    monkeypatch.setenv("LABHQ_TEST_REAL_HOME", str(home))
    monkeypatch.setenv("LABHQ_CONFIG", str(config))
    touched = []
    for method in ("is_file", "read_text"):
        original = getattr(Path, method)

        def spy(path, *args, _original=original, _method=method, **kwargs):
            if path == config:
                touched.append(_method)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, method, spy)
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        Settings.load(None if via_env else str(config))
    assert touched == []


@pytest.mark.parametrize("entry", [
    "instance", "instance_cli", "instance_ports", "init", "staff_config",
    "cache", "skill", "skill_commit", "codex_config", "codex_ok", "codex_marker", "doctor_owner", "child_config",
])
def test_direct_home_entry_points_guard_before_io(tmp_path, monkeypatch, entry):
    from labhq import cli, doctor, init_wizard
    from labhq.adapters.base import child_config_dirs
    from labhq.recruit import paper2agent
    from labhq.runner import codex_sandbox
    from labhq.settings import write_staff_config

    home = tmp_path / "operator"
    root = home / ".labhq"
    config = root / "config.yaml"
    for key in ("HOME", "USERPROFILE", "LABHQ_TEST_REAL_HOME"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setattr(paper2agent, "skill_path", lambda engine: root / "skill")
    calls = {
        "instance": lambda: cli._instance_config("trial"),
        "instance_cli": lambda: cli.main(["--instance", "trial", "doctor"]),
        "instance_ports": init_wizard._used_instance_ports,
        "init": lambda: init_wizard.run(str(config), yes=True, dry_run=True),
        "staff_config": lambda: write_staff_config(Settings(config_path=str(config)), tmp_path / "staff"),
        "cache": lambda: paper2agent.install_skill("unused", root / "cache"),
        "skill": paper2agent.skill_installed,
        "skill_commit": paper2agent.installed_commit,
        "codex_config": lambda: codex_sandbox.config_windows_sandbox(root / "codex-staff/config.toml"),
        "codex_ok": lambda: codex_sandbox.read_ok(root / "codex-staff"),
        "codex_marker": lambda: codex_sandbox.has_setup_marker(root / "codex-staff"),
        "doctor_owner": lambda: doctor._config_owner_is_current_user(config),
        "child_config": lambda: child_config_dirs({"CODEX_HOME": str(root / "codex-staff")},
                                                    tmp_path, "CODEX_HOME", ".codex"),
    }

    def unexpected_io(*args, **kwargs):
        pytest.fail("file I/O reached before the operator path guard")

    # resolve() is used by the guard; trap the actual probes/reads, not resolution.
    for method in ("is_file", "exists", "read_text", "glob", "mkdir"):
        original = getattr(Path, method)

        def spy(path, *args, _original=original, **kwargs):
            if path.is_relative_to(root):
                unexpected_io()
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, method, spy)
    monkeypatch.setattr(doctor, "_windows_config_owner_is_current_user", unexpected_io)
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        calls[entry]()


def test_default_hub_refuses_unisolated_home(tmp_path, monkeypatch):
    home = tmp_path / "operator"
    for key in ("HOME", "USERPROFILE", "LABHQ_TEST_REAL_HOME"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.delenv("LABHQ_STATE_DIR", raising=False)
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        hub = create_app(Settings()).state.hub
        hub.store.close()  # safe pre-fix reproduction: the operator is synthetic
    assert not (home / ".labhq").exists()


@pytest.mark.parametrize("engine,key", [("claude_code", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME")])
@pytest.mark.parametrize("entry", ["skill_installed", "installed_commit", "install_skill"])
def test_paper2agent_configured_staff_path_is_guarded(tmp_path, monkeypatch, engine, key, entry):
    from labhq.recruit import paper2agent

    home = tmp_path / "operator"
    staff = home / ".labhq" / "staff"
    monkeypatch.setenv("LABHQ_TEST_REAL_HOME", str(home))
    settings = Settings.model_validate({"engines": {engine: {"bin": engine, "env": {key: str(staff)}}}})

    def unexpected_io(*args, **kwargs):
        pytest.fail("Paper2Agent reached file I/O or git before guarding the staff path")

    monkeypatch.setattr(paper2agent.subprocess, "run", unexpected_io)
    for method in ("is_file", "exists", "read_text", "mkdir"):
        monkeypatch.setattr(Path, method, unexpected_io)
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        if entry == "install_skill":
            paper2agent.install_skill("unused", tmp_path / "cache", destinations=[staff / "skills/paper2agent"])
        else:
            getattr(paper2agent, entry)(engine, settings)


def test_explicit_store_path_is_guarded_before_mkdir(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_TEST_REAL_HOME", str(tmp_path / "operator"))
    path = tmp_path / "operator" / ".labhq" / "state" / "gateway.sqlite3"
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        store = StateStore(path)
        store.close()
    assert not path.parent.exists()


def test_default_paths_and_child_process_are_isolated(tmp_path_factory):
    s = Settings()
    assert Path.home().is_relative_to(tmp_path_factory.getbasetemp())
    for raw in (s.gateway.state_dir, s.runner.state_dir, s.runner.workspace_root, s.runner.talent_dir):
        assert s.path(raw).is_relative_to(Path.home())
    child = subprocess.run([sys.executable, "-c", "from pathlib import Path; "
                            "from labhq.settings import Settings; "
                            "from labhq.gateway.server import create_app; "
                            "create_app(Settings()).state.hub.store.close(); print(Path.home())"],
                           capture_output=True, text=True, check=True)
    assert Path(child.stdout.strip()) == Path.home()


def test_child_process_guard_survives_home_override(tmp_path):
    home = tmp_path / "operator"
    env = {**os.environ, "LABHQ_TEST_REAL_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("LABHQ_STATE_DIR", None)
    env.pop("PYTEST_CURRENT_TEST", None)  # collection-time children have no current item yet
    result = subprocess.run([sys.executable, "-c",
                             "from labhq.gateway.server import create_app; "
                             "from labhq.settings import Settings; create_app(Settings())"],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "pytest" in result.stderr and "operator" in result.stderr
    assert not (home / ".labhq").exists()


def test_normal_process_keeps_default_behavior(tmp_path):
    home = tmp_path / "normal-user"
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    for name in ("PYTEST_CURRENT_TEST", "LABHQ_TEST_REAL_HOME", "LABHQ_STATE_DIR"):
        env.pop(name, None)
    subprocess.run([sys.executable, "-c", "from labhq.settings import Settings; "
                    "from labhq.gateway.server import create_app; "
                    "create_app(Settings()).state.hub.store.close()"], env=env, check=True)
    assert (home / ".labhq/state/gateway.sqlite3").is_file()


def test_guard_resolves_directory_aliases(tmp_path, monkeypatch):
    home = tmp_path / "operator"
    target = home / ".labhq"
    target.mkdir(parents=True)
    link = tmp_path / "alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("LABHQ_TEST_REAL_HOME", str(home))
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        StateStore(link / "state" / "gateway.sqlite3")
    assert not (target / "state").exists()
