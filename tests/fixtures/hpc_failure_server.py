"""HPC stdio fixture: scheduler and broker calls never leave this process."""

import sys
from subprocess import CompletedProcess

from labhq.settings import HpcSettings, Settings
from labhq.tools import hpc_mcp as hpc
from labhq.tools.scheduler import Scheduler

scenario = sys.argv[1]
hpc.S = Settings(hpc=HpcSettings(scheduler="pbs" if scenario.startswith("pbs") else "sge",
                                user="fixture", submit_prefix=["sudo", "-n"],
                                job_group="fixture-jobs"))
hpc.S.policy.approvals.hpc_core_hours_threshold = 0 if scenario in {"broker", "denied"} else 1000


def fake_run(args):
    if "qsub" in args:
        return CompletedProcess(args, 1, "", "qsub: fixture submission rejected")
    if "qdel" in args:
        return CompletedProcess(args, 1, "", "sudo: fixture qdel permission denied")
    if scenario == "accounting" and args[0] == "qstat":
        return CompletedProcess(args, 0, "", "")
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
