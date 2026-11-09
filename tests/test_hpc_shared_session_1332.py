"""Submission pins at the scheduler and runner boundaries; no HPC connection is opened."""
import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock

import pytest

from labhq import hpc_site
from labhq.runner.hpc_jobs import submit_job
from labhq.settings import Settings
from labhq.tools.scheduler import Scheduler


@pytest.fixture
def setup_submit(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))

    def make(backend, *, pin=True, connect=False, **hpc):
        profile = hpc_site.SiteProfile(
            name="fake", scheduler=backend, connect_argv=["unused-gateway"] if connect else [],
            queue="batch" if pin else None, pinned_nodes=["node01"] if pin else [])
        hpc_site.save_profile(profile)
        settings = Settings.model_validate({"hpc": {"scheduler": backend, "site_profile": "fake",
                                                    "job_group": "fixture", "user": "fixture", **hpc}})
        scheduler = Scheduler(settings.hpc)
        # A forbidden submission must not reach transfer, the session, or the command executor.
        scheduler.transfer = Mock(return_value=None)
        scheduler._run = Mock(return_value=subprocess.CompletedProcess([], 0, "123\n", ""))
        jobs = tmp_path / "jobs"
        jobs.mkdir(exist_ok=True)
        script = jobs / "job.sh"
        script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        return settings, scheduler, script

    return make


@pytest.mark.parametrize("backend", ["sge", "slurm"])
@pytest.mark.parametrize("connect", [False, True])
@pytest.mark.parametrize("runner", [False, True])
def test_pin_reaches_actual_submit(setup_submit, tmp_path, backend, connect, runner):
    settings, scheduler, script = setup_submit(backend, connect=connect)
    if runner:
        result = asyncio.run(submit_job(settings, scheduler, tmp_path, {"script": str(script), "queue": "batch"},
                                        AsyncMock(return_value={"approved": True})))
        assert result["submitted"] and result["job_id"] == "123"
    else:
        assert scheduler.submit(str(script), "job", queue="batch") == "123"
    args = scheduler._run.call_args.args[0]
    if backend == "slurm":
        assert "--partition=batch" in args and "--nodelist=node01" in args
        assert args.count("--partition=batch") == 1
    else:
        assert args[args.index("-q") + 1] == "batch@node01"


@pytest.mark.parametrize("backend,options,queue", [
    ("slurm", {"slurm": {"sbatch_args": ["--nodelist=other"]}}, None),
    ("slurm", {"slurm": {"sbatch_args": ["--partition=other"]}}, None),
    ("sge", {"sge": {"mem_resource": "h"}}, None),
    ("slurm", {}, "other"), ("sge", {}, "other"),
    ("pbs", {}, None),
])
@pytest.mark.parametrize("runner", [False, True])
def test_conflicting_pin_never_submits(setup_submit, tmp_path, backend, options, queue, runner):
    settings, scheduler, script = setup_submit(backend, **options)
    approve = AsyncMock(return_value={"approved": True})
    if runner:
        result = asyncio.run(submit_job(settings, scheduler, tmp_path, {"script": str(script), "queue": queue}, approve))
        assert "error" in result and not result.get("submitted")
        approve.assert_not_called()
        scheduler.transfer.assert_not_called()
    else:
        with pytest.raises(hpc_site.SiteProfileError):
            scheduler.submit(str(script), "job", queue=queue)
    scheduler._run.assert_not_called()


@pytest.mark.parametrize("backend", ["sge", "slurm"])
@pytest.mark.parametrize("runner", [False, True])
def test_wrapper_options_are_preserved_outside_placement_parsing(setup_submit, tmp_path, backend, runner):
    prefix = ["runuser", "-p", "-w", "PATH", "-u", "fixture", "--"]
    settings, scheduler, script = setup_submit(backend, submit_prefix=prefix)
    if runner:
        result = asyncio.run(submit_job(settings, scheduler, tmp_path, {"script": str(script)},
                                        AsyncMock(return_value={"approved": True})))
        assert result["submitted"] and result["job_id"] == "123"
    else:
        assert scheduler.submit(str(script), "job") == "123"
    args = scheduler._run.call_args.args[0]
    assert args[:len(prefix)] == prefix
    assert args[len(prefix)] == ("sbatch" if backend == "slurm" else "qsub")
    if backend == "slurm":
        assert "--partition=batch" in args and "--nodelist=node01" in args
    else:
        assert args[args.index("-q") + 1] == "batch@node01"


@pytest.mark.parametrize("backend", ["sge", "pbs", "slurm"])
@pytest.mark.parametrize("runner", [False, True])
def test_unpinned_submit_keeps_requested_queue(setup_submit, tmp_path, backend, runner):
    settings, scheduler, script = setup_submit(backend, pin=False)
    if runner:
        result = asyncio.run(submit_job(settings, scheduler, tmp_path, {"script": str(script), "queue": "other"},
                                        AsyncMock(return_value={"approved": True})))
        assert result["submitted"]
    else:
        assert scheduler.submit(str(script), "job", queue="other") == "123"
    args = scheduler._run.call_args.args[0]
    if backend == "slurm":
        assert "--partition=other" in args and not any(a.startswith("--nodelist") for a in args)
    else:
        assert args[args.index("-q") + 1] == "other"
