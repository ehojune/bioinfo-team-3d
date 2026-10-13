"""Never use the operator's state, including explicit paths and child processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from labhq.gateway.server import create_app
from labhq.settings import Settings
from labhq.store import StateStore


def test_default_hub_refuses_unisolated_home(tmp_path, monkeypatch):
    home = tmp_path / "operator"
    for key in ("HOME", "USERPROFILE", "LABHQ_TEST_REAL_HOME"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.delenv("LABHQ_STATE_DIR", raising=False)
    with pytest.raises(RuntimeError, match="pytest.*operator"):
        hub = create_app(Settings()).state.hub
        hub.store.close()  # safe pre-fix reproduction: the operator is synthetic
    assert not (home / ".labhq").exists()


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
