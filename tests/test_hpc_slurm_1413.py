import os
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from labhq import hpc_site
from labhq.settings import HpcSettings
from labhq.tools import scheduler as sched


@pytest.fixture
def pinned(monkeypatch):
    profile = hpc_site.SiteProfile(name="fixture", pinned_nodes=["n1", "n2"])
    monkeypatch.setattr(hpc_site, "load_profile", lambda _: profile)
    return sched.Scheduler(HpcSettings(scheduler="slurm", site_profile="fixture", user="fixture"))


def test_pin_rejects_submit_prefix_before_running(pinned):
    pinned.cfg.submit_prefix = ["sudo", "-u", "other"]
    with pytest.raises(RuntimeError, match="submit_prefix"):
        pinned.submit("run.sh", "job")


@pytest.mark.parametrize("directive", ["#SLURM --clusters=other", "#SBATCH --clust=other",
                                       "#SLURM -vMother"])
def test_script_cluster_directives_are_rejected(directive):
    assert sched.slurm_cluster_directive("#!/bin/bash\n" + directive + "\necho ok\n")


@pytest.mark.parametrize("directive", ["#SBATCH hetjob", "#SLURM packjob"])
def test_heterogeneous_component_is_recognized(directive):
    assert sched.slurm_heterogeneous_directive("#!/bin/bash\n" + directive + "\necho ok\n")


@pytest.mark.parametrize("name", ["SBATCH_CLUSTERS", "SLURM_CLUSTERS"])
def test_local_environment_is_refused_before_spawn(pinned, monkeypatch, name):
    monkeypatch.setattr(pinned, "session", lambda: None)
    monkeypatch.setenv(name, "other")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("submit was executed"))
    with pytest.raises(RuntimeError, match="refuses"):
        pinned.submit("run.sh", "job")


@pytest.mark.parametrize("transport", ["ssh", "session"])
def test_remote_submit_guard_is_inside_remote_command(pinned, monkeypatch, transport):
    seen = []
    if transport == "ssh":
        pinned.cfg.ssh_host = "example.invalid"
        monkeypatch.setattr(pinned, "session", lambda: None)
        def run(args, **kwargs):
            seen.append(args[-1])
            return subprocess.CompletedProcess(args, 0, "123\n", "")
        monkeypatch.setattr(subprocess, "run", run)
    else:
        class Session:
            cfg = SimpleNamespace(redact_words=())
            def run(self, command, timeout):
                seen.append(command)
                return SimpleNamespace(output="123\n" + sched.STDERR_MARK + "\n", exit_code=0, truncated=False)
        monkeypatch.setattr(pinned, "session", lambda: Session())
    assert pinned.submit("run.sh", "job") == "123"
    assert "SBATCH_CLUSTERS" in seen[0] and "SLURM_CLUSTERS" in seen[0]
    assert "--nodes=1" in seen[0] and "--nodelist=n1,n2" in seen[0]


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell required")
@pytest.mark.parametrize("name", [None, "SBATCH_CLUSTERS", "SLURM_CLUSTERS"])
def test_remote_guard_executes_or_refuses_before_command(pinned, monkeypatch, name):
    captured = []
    pinned.cfg.ssh_host = "example.invalid"
    monkeypatch.setattr(pinned, "session", lambda: None)
    def capture(args, **kwargs):
        captured.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "123\n", "")
    monkeypatch.setattr(subprocess, "run", capture)
    assert pinned.submit("run.sh", "job") == "123"
    command = shlex.split(captured[0])
    # Replace sbatch with a harmless marker process; run the generated guard itself.
    command[4:] = ["sh", "-c", "printf ran"]
    env = {k: v for k, v in os.environ.items() if k not in {"SBATCH_CLUSTERS", "SLURM_CLUSTERS"}}
    if name:
        env[name] = "other"
    real_run = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    out, _ = real_run.communicate(timeout=10)
    assert (real_run.returncode, out.strip()) == ((97, "") if name else (0, "ran"))
