import pytest

# Capture the operator home before imports/collection can expand a default path.
from labhq import test_guard
from scripts.ci_isolation import operator_home

_OPERATOR_HOME = str(test_guard._ORIGINAL_HOME)
_SESSION_ENV = pytest.MonkeyPatch()


def _isolate_home(patch, home):
    home.mkdir(parents=True, exist_ok=True)
    patch.setenv("LABHQ_TEST_REAL_HOME", _OPERATOR_HOME)
    for name in ("HOME", "USERPROFILE"):
        patch.setenv(name, str(home))
    for name, suffix in {
        "APPDATA": "AppData/Roaming", "LOCALAPPDATA": "AppData/Local",
        "XDG_CONFIG_HOME": ".config", "XDG_CACHE_HOME": ".cache", "XDG_DATA_HOME": ".local/share",
        "XDG_STATE_HOME": ".local/state", "LABHQ_HOME": ".labhq",
    }.items():
        patch.setenv(name, str(home / suffix))
    patch.setenv("HOMEDRIVE", home.drive)
    patch.setenv("HOMEPATH", str(home)[len(home.drive):])
    # Inherited operator overrides must not defeat the isolated default. Tests may set their own. An operator's GitHub
    # token would make doctor tests probe GitHub for real (#687), so it is dropped too.
    for name in ("LABHQ_STATE_DIR", "LABHQ_CONFIG", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "GITHUB_TOKEN", "GH_TOKEN"):
        patch.delenv(name, raising=False)


def pytest_sessionstart(session):
    operator_home(_OPERATOR_HOME, getattr(session.config, "workerinput", None))
    _isolate_home(_SESSION_ENV, session.config._tmp_path_factory.getbasetemp() / "collection-home")


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["labhq_operator_home"] = _OPERATOR_HOME


def pytest_unconfigure(config):
    _SESSION_ENV.undo()


@pytest.fixture(autouse=True)
def _isolated_user_home(tmp_path_factory, monkeypatch):
    # A sibling keeps tests that enumerate tmp_path's contents unchanged, and paths short on Windows.
    _isolate_home(monkeypatch, tmp_path_factory.mktemp("home"))

import labhq.private_paths as private_paths


@pytest.fixture(autouse=True)
def _no_host_private_paths(tmp_path_factory, monkeypatch):
    """The default policy.private_paths list names real folders in the account's home (~/.ssh, ~/.claude) and
    labhq's own config and state. Point it at an empty home so a test's result does not depend on which of those
    exist on the machine; tests/test_private_paths.py sets the real functions back where it needs them."""
    monkeypatch.setattr(private_paths, "host_home", lambda: str(tmp_path_factory.getbasetemp() / "empty-home"))
    monkeypatch.setattr(private_paths, "_labhq_entries", lambda settings, home: [])


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """The report PDF needs an installed Edge or Chrome, and the repository edition prints one (#584). No test starts a
    real browser: a test that prints patches `find_browser` or `subprocess.run` itself, as tests/test_report_pdf.py does."""
    monkeypatch.setattr("labhq.report_pdf.find_browser", lambda configured=None: None)


import labhq.bioinfo_link as bioinfo_link


@pytest.fixture(autouse=True)
def _no_host_bioinfo_agent(monkeypatch):
    """`labhq init` looks for a bioinfo-agent checkout in common places (D:/bioinfo-agent, ~/bioinfo-agent, /mnt/d/...),
    which exist on some PCs. No test depends on this machine; tests/test_init_defaults.py names its own places."""
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [])


@pytest.fixture(autouse=True)
def _no_host_wsl(monkeypatch):
    """Doctor must never start an operator's WSL distribution in a test (#612)."""
    from labhq import wsl_setup

    monkeypatch.setattr(wsl_setup, "distributions", lambda: [])
