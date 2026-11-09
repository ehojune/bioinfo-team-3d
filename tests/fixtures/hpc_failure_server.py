"""HPC stdio fixture: scheduler and broker calls never leave this process."""

import sys
from subprocess import CompletedProcess

from labhq.runner.hpc_jobs import submit_job
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


async def deny(payload):
    return {"approved": False, "note": "fixture PI denied"}


async def broker(path, payload, timeout):
    if scenario == "broker":
        raise ConnectionError("fixture approval broker unavailable")
    if path == "/jobs/owned":  # the runner tracks job 123 for this agent unless the scenario says otherwise
        return {"owned": scenario != "cancel_unowned"}
    if path == "/jobs/submit":  # the runner's side of hpc_submit, with the same fake scheduler
        return await submit_job(hpc.S, hpc.SCHED, hpc.WORKDIR, payload, deny)
    if path.startswith("/jobs/scheduler/"):
        op = path.rsplit("/", 1)[-1]
        try:
            if op == "cancel":
                if scenario == "cancel_unowned":
                    return {"error": "labhq cancels only jobs it tracks for you"}
                return {"result": hpc.SCHED.cancel(payload["job_id"])}
            if op == "status":
                return {"status": hpc.SCHED.status(payload["job_id"]).to_dict()}
            if op == "queue":
                return {"queue": [job.to_dict() for job in hpc.SCHED.queue()]}
        except Exception as error:
            return {"error": str(error)}
    raise AssertionError(f"unexpected broker path {path}")


hpc.SCHED = Scheduler(hpc.S.hpc)
hpc.SCHED._run = fake_run
hpc._prepare_job_files = prepare
hpc._broker = broker
hpc.server.run()
