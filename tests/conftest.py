import pytest

# Capture the operator home before imports/collection can expand a default path.
from labhq import test_guard

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
    # Inherited operator overrides must not defeat the isolated default. Tests may set their own.
    for name in ("LABHQ_STATE_DIR", "LABHQ_CONFIG", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        patch.delenv(name, raising=False)


def pytest_sessionstart(session):
    _isolate_home(_SESSION_ENV, session.config._tmp_path_factory.getbasetemp() / "collection-home")


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
