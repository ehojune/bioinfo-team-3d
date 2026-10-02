import pytest

import labhq.private_paths as private_paths


@pytest.fixture(autouse=True)
def _no_host_private_paths(tmp_path_factory, monkeypatch):
    """The default policy.private_paths list names real folders in the account's home (~/.ssh, ~/.claude) and
    labhq's own config and state. Point it at an empty home so a test's result does not depend on which of those
    exist on the machine; tests/test_private_paths.py sets the real functions back where it needs them."""
    monkeypatch.setattr(private_paths, "host_home", lambda: str(tmp_path_factory.getbasetemp() / "empty-home"))
    monkeypatch.setattr(private_paths, "_labhq_entries", lambda settings, home: [])
