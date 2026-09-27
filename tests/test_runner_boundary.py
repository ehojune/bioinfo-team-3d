import asyncio
import logging
import os

import pytest

from labhq.runner.daemon import Runner
from labhq.settings import DataZone, HpcSettings, Settings


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute-only directory permissions required")
def test_execute_only_zone_is_rejected_even_when_listing_fails(tmp_path, monkeypatch):
    zone = tmp_path / "restricted"
    zone.mkdir()
    known = zone / "known.vcf"
    known.write_text("record")
    zone.chmod(0o111)
    try:
        assert known.read_text() == "record"  # traversal can reveal a known file
        real_access = os.access
        real_listdir = os.listdir

        def access(path, mode, *args, **kwargs):
            if os.fspath(path) == str(zone) and mode == os.R_OK:
                return False
            return real_access(path, mode, *args, **kwargs)

        def listdir(path):
            if os.fspath(path) == str(zone):
                raise PermissionError("execute-only zone")
            return real_listdir(path)

        s = Settings()
        s.policy.data_zones = [DataZone(path=str(zone))]
        with monkeypatch.context() as m:
            m.setattr(os, "access", access)
            m.setattr(os, "listdir", listdir)
            with pytest.raises(RuntimeError, match="runner account can read"):
                Runner(s)._check_data_boundary()
    finally:
        zone.chmod(0o700)


def test_unc_zone_refused_even_without_posix_path(monkeypatch):
    s = Settings()
    s.policy.data_zones = [DataZone(path=r"\\server\share\cohort")]
    runner = Runner(s)
    with monkeypatch.context() as m:
        m.setattr(os, "name", "posix")
        with pytest.raises(RuntimeError, match="UNC"):
            runner._check_data_boundary()


@pytest.mark.skipif(os.name == "nt", reason="POSIX groups and runner membership required")
def test_runner_checks_job_group_membership(monkeypatch):
    import grp
    import pwd
    from types import SimpleNamespace

    group = grp.getgrgid(os.getgid()).gr_name
    user = pwd.getpwuid(os.getuid()).pw_name
    s = Settings(hpc=HpcSettings(submit_prefix=["sudo"], job_group=group, user=user))
    runner = Runner(s)
    runner._check_job_group()
    with monkeypatch.context() as m:
        m.setattr(os, "getgroups", lambda: [])
        runner._check_job_group()  # primary group alone is sufficient
    s.hpc.job_group = "missing-group"
    with pytest.raises(RuntimeError, match="does not exist"):
        runner._check_job_group()
    s.hpc.job_group = "other-group"
    other_gid = max([os.getgid(), *os.getgroups()]) + 1
    with monkeypatch.context() as m:
        m.setattr(grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=other_gid))
        with pytest.raises(RuntimeError, match="runner account is not a member"):
            runner._check_job_group()
    s.hpc.job_group = group
    s.hpc.user = "missing-account"
    with pytest.raises(RuntimeError, match="hpc.user does not exist"):
        runner._check_job_group()
    s.hpc.user = user
    with monkeypatch.context() as m:
        m.setattr(os, "getgrouplist", lambda name, primary_gid: [])
        with pytest.raises(RuntimeError, match="hpc.user is not a member"):
            runner._check_job_group()


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask controls task input modes")
def test_account_switch_sets_private_runner_umask_before_workspace_use(monkeypatch):
    s = Settings(hpc=HpcSettings(submit_prefix=["sudo"], job_group="lab-jobs", user="data-account"))
    runner = Runner(s)
    modes = []

    class StopAfterChecks(Exception):
        pass

    monkeypatch.setattr(runner, "_check_job_group", lambda: None)
    monkeypatch.setattr(runner, "_check_data_boundary", lambda: None)
    monkeypatch.setattr(os, "umask", lambda mode: modes.append(mode))
    monkeypatch.setattr(runner.registry, "load", lambda: (_ for _ in ()).throw(StopAfterChecks))
    with pytest.raises(StopAfterChecks):
        asyncio.run(runner.run_forever())
    assert modes == [0o077]
