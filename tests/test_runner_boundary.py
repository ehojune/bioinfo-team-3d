import asyncio
import logging
import os

import pytest

from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings


def test_windows_runner_refuses_restricted_zone_before_start(monkeypatch):
    s = Settings()
    s.policy.data_zones = [DataZone(path="/data/cohort")]
    runner = Runner(s)
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        with pytest.raises(RuntimeError, match="Windows.*Linux"):
            asyncio.run(runner.run_forever())


def test_windows_runner_without_zones_is_allowed(monkeypatch):
    runner = Runner(Settings())
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        runner._check_data_boundary()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits and account access required")
def test_posix_runner_account_must_not_read_zone(tmp_path, caplog):
    zone = tmp_path / "restricted"
    zone.mkdir(mode=0o700)
    s = Settings()
    s.policy.data_zones = [DataZone(path=str(zone))]
    runner = Runner(s)
    with pytest.raises(RuntimeError, match="runner account can read"):
        runner._check_data_boundary()
    s.policy.allow_runner_read_restricted = True
    with caplog.at_level(logging.CRITICAL):
        runner._check_data_boundary()
    assert "UNSAFE OVERRIDE" in caplog.text
    s.policy.allow_runner_read_restricted = False
    zone.chmod(0)
    try:
        if not os.access(zone, os.R_OK):
            runner._check_data_boundary()  # denied by filesystem permissions
    finally:
        zone.chmod(0o700)
    zone.rmdir()
    runner._check_data_boundary()  # absent on this host


def test_unc_zone_refused_even_without_posix_path(monkeypatch):
    s = Settings()
    s.policy.data_zones = [DataZone(path=r"\\server\share\cohort")]
    runner = Runner(s)
    with monkeypatch.context() as m:
        m.setattr(os, "name", "posix")
        with pytest.raises(RuntimeError, match="UNC"):
            runner._check_data_boundary()
