"""Named instances keep one PC's labhq processes separate."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from labhq import cli, doctor, init_wizard as wizard
from labhq.gateway.server import create_app
from labhq.settings import Settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    for key in ("LABHQ_CONFIG", "BIOINFO_AGENT_DIR", "CODEX_HOME", "LABHQ_STATE_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(wizard.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_probe", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected CLI")))
    monkeypatch.setattr(wizard, "_hpc_query", lambda argv: None)
    monkeypatch.chdir(tmp_path)
    return home


def _instance(home: Path, name: str) -> tuple[Path, dict]:
    path = home / ".labhq" / name / "labhq.yaml"
    return path, yaml.safe_load(path.read_text(encoding="utf-8"))


def test_init_instances_have_distinct_ports_paths_tokens_and_runner_ids(isolated_home):
    cli.main(["init", "--instance", "alpha", "--yes"])
    cli.main(["init", "--instance", "beta", "--yes"])
    alpha_path, alpha = _instance(isolated_home, "alpha")
    beta_path, beta = _instance(isolated_home, "beta")

    assert alpha_path.exists() and beta_path.exists()
    assert alpha["instance"] == alpha["runner"]["id"] == "alpha"
    assert beta["instance"] == beta["runner"]["id"] == "beta"
    assert alpha["gateway"]["port"] != beta["gateway"]["port"]
    assert alpha["runner"]["broker_port"] != beta["runner"]["broker_port"]
    for section, key in (("gateway", "state_dir"), ("runner", "state_dir"),
                         ("runner", "workspace_root"), ("runner", "talent_dir")):
        assert alpha[section][key] != beta[section][key]
        assert f"/.labhq/alpha/" in alpha[section][key].replace("\\", "/")
        assert f"/.labhq/beta/" in beta[section][key].replace("\\", "/")
    assert alpha["gateway"]["runner_token"] != beta["gateway"]["runner_token"]
    assert alpha["gateway"]["client_token"] != beta["gateway"]["client_token"]


def test_existing_instance_is_not_overwritten(isolated_home):
    cli.main(["init", "--instance", "alpha", "--yes"])
    path, _ = _instance(isolated_home, "alpha")
    original = path.read_bytes()
    cli.main(["init", "--instance", "alpha", "--yes"])
    assert path.read_bytes() == original


def test_instance_selects_config_and_conflicts_with_config(isolated_home, monkeypatch):
    cli.main(["init", "--instance", "alpha", "--yes"])
    expected, _ = _instance(isolated_home, "alpha")
    seen = []
    original = Settings.load

    def load(path=None):
        seen.append(path)
        return original(path)

    monkeypatch.setattr(Settings, "load", load)
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: [])
    cli.main(["agents", "--instance", "alpha"])
    assert Path(seen[-1]) == expected

    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", "elsewhere.yaml", "agents", "--instance", "alpha"])
    assert exc.value.code == 2


def test_default_web_names_are_unchanged_and_instance_names_are_visible(tmp_path):
    default = Settings()
    default.gateway.state_dir = str(tmp_path / "default")
    base = TestClient(create_app(default))
    assert "<title>labhq 사무실</title>" in base.get("/").text
    assert "<title>labhq · 종이숲 연구소</title>" in base.get("/3d/").text
    assert base.get("/manifest.webmanifest").json()["short_name"] == "labhq"

    named = Settings(instance="alpha")
    named.gateway.state_dir = str(tmp_path / "alpha")
    app = TestClient(create_app(named))
    assert "alpha" in app.get("/").text
    assert "alpha" in app.get("/3d/").text
    manifest = app.get("/manifest.webmanifest").json()
    assert manifest["name"] == "labhq alpha 사무실"
    assert manifest["short_name"] == "labhq alpha"


def test_instance_name_cannot_escape_its_directory():
    with pytest.raises(SystemExit) as exc:
        cli.main(["agents", "--instance", "../other"])
    assert exc.value.code == 2


def test_an_uninitialised_instance_name_is_refused_instead_of_using_defaults(isolated_home, monkeypatch):
    """#303 review: a typo must not fall back to the default ports and shared state."""
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: [])
    with pytest.raises(SystemExit) as exc:
        cli.main(["agents", "--instance", "typo"])
    assert exc.value.code == 2
    cli.main(["init", "--instance", "typo", "--yes"])  # init itself still creates it
    cli.main(["agents", "--instance", "typo"])
