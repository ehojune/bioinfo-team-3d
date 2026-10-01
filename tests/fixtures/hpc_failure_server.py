"""HPC stdio fixture: scheduler and broker calls never leave this process."""

import sys
from subprocess import CompletedProcess

from labhq.settings import HpcSettings, Settings
from labhq.tools import hpc_mcp as hpc
from labhq.tools.scheduler import Scheduler

scenario = sys.argv[1]
family = next((name for name in ("pbs", "slurm") if scenario.startswith(name)), "sge")
hpc.S = Settings(hpc=HpcSettings(scheduler=family, user="fixture", submit_prefix=["sudo", "-n"],
                                job_group="fixture-jobs"))
hpc.S.policy.approvals.hpc_core_hours_threshold = 0 if scenario in {"broker", "denied"} else 1000


def fake_run(args):
    for submit in ("qsub", "sbatch"):
        if submit in args:
            return CompletedProcess(args, 1, "", f"{submit}: fixture submission rejected")
    for cancel in ("qdel", "scancel"):
        if cancel in args:
            return CompletedProcess(args, 1, "", f"sudo: fixture {cancel} permission denied")
    if scenario.endswith("accounting") and args[0] in ("qstat", "squeue"):
        return CompletedProcess(args, 0, "", "")  # the controller no longer lists the job
    return CompletedProcess(args, 255, "", "ssh: fixture connection refused")


def prepare(workdir, script_path, logs, body, *args):
    if scenario == "permissions":
        raise OSError("fixture script permission denied")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(body, encoding="utf-8")


async def broker(path, payload, timeout):
    if scenario == "broker":
        raise ConnectionError("fixture approval broker unavailable")
    return {"approved": False, "note": "fixture PI denied"}


hpc.SCHED = Scheduler(hpc.S.hpc)
hpc.SCHED._run = fake_run
hpc._prepare_job_files = prepare
hpc._broker = broker
hpc.server.run()
