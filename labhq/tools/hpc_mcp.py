"""labhq-hpc MCP server (stdio). Launched per task by the runner.

Env (set by the runner): LABHQ_BROKER_URL, LABHQ_BROKER_TOKEN, LABHQ_TASK_ID, LABHQ_AGENT_ID,
LABHQ_WORKDIR, LABHQ_CONFIG.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import httpx

from ..policy import core_hours, hpc_needs_approval
from ..settings import Settings
from ._mcpcompat import make_server
from .scheduler import Scheduler, build_script, sanitize_job_name

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
SCHED = Scheduler(S.hpc)
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")
WORKDIR = Path(os.environ.get("LABHQ_WORKDIR", ".")).resolve()

server = make_server(
    "labhq-hpc",
    instructions=(
        "Submit and monitor batch jobs on the lab's HPC (SGE or PBS). Use this for anything heavy "
        "(alignment, variant calling, large downloads, anything touching restricted data). Submissions "
        "may wait for the PI's approval on their phone. After submitting, do not poll in a loop: "
        "summarize what you are waiting for and end your turn. You will be resumed when jobs finish."
    ),
)


async def _broker(path: str, payload: dict, timeout: float) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{BROKER}{path}", json=payload, headers={"X-Labhq-Token": TOKEN})
        r.raise_for_status()
        return r.json()


def _prepare_job_files(workdir: Path, script_path: Path, logs: Path, body: str,
                       job_group: str | None = None) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    if job_group:
        if os.name == "nt":
            raise RuntimeError("hpc.job_group requires a POSIX runner")
        import grp

        gid = grp.getgrnam(job_group).gr_gid
        for directory in (workdir, script_path.parent, logs):
            os.chown(directory, -1, gid)
        # The data account must traverse the task directory to reach jobs/.
        os.chmod(workdir, stat.S_IMODE(workdir.stat().st_mode) | stat.S_IXGRP)
        os.chmod(script_path.parent, 0o2750)
        os.chmod(logs, 0o2770)
    script_path.write_text(body)
    if job_group:
        os.chown(script_path, -1, gid)
    script_path.chmod(0o750)


@server.tool()
async def hpc_submit(script: str, job_name: str, cores: int = 1, mem: str = "4G",
                     walltime: str = "04:00:00", queue: str | None = None, reason: str = "") -> str:
    """Submit a bash script as a batch job.

    script: the script body (commands; scheduler directives optional). It runs from your workspace.
    cores / mem (total, e.g. "32G") / walltime ("HH:MM:SS"). reason: why this job is needed.
    Returns JSON: {"submitted": true, "job_id": ...} or {"submitted": false, "reason": ...}.
    """
    name = sanitize_job_name(job_name)
    logs = WORKDIR / "jobs" / "logs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    spath = WORKDIR / "jobs" / f"{name}_{stamp}.sh"
    try:
        _prepare_job_files(WORKDIR, spath, logs, build_script(script, str(WORKDIR)),
                           S.hpc.job_group if S.hpc.submit_prefix else None)
    except (OSError, KeyError, RuntimeError) as e:
        return json.dumps({"submitted": False, "reason": f"job file permissions: {e}"})
    ch = core_hours(cores, walltime)

    if hpc_needs_approval(cores, walltime, S.policy):
        preview = "\n".join(spath.read_text().splitlines()[:40])
        try:
            dec = await _broker("/approval", {
                "task_id": TASK, "agent_id": AGENT, "kind": "hpc_submit",
                "summary": f"HPC 제출: {name} · {cores} cores · {mem} · {walltime} (~{ch:.1f} core-h)",
                "detail": {"reason": reason, "script_path": str(spath), "script_preview": preview, "queue": queue},
                "timeout_s": S.policy.approvals.timeout_s,
            }, timeout=S.policy.approvals.timeout_s + 30)
        except Exception as e:  # fail closed
            return json.dumps({"submitted": False, "reason": f"approval broker unreachable: {e}"})
        if not dec.get("approved"):
            return json.dumps({"submitted": False, "reason": dec.get("note") or "denied by the PI"})

    try:
        job_id = await asyncio.to_thread(
            SCHED.submit, str(spath), name, cores, mem, walltime, queue or S.hpc.default_queue,
            str(logs / f"{name}_{stamp}.out"), str(logs / f"{name}_{stamp}.err"),
        )
    except Exception as e:
        return json.dumps({"submitted": False, "error": str(e)})

    try:
        await _broker("/jobs/track", {"task_id": TASK, "agent_id": AGENT, "job_id": job_id,
                                      "name": name, "script": str(spath), "core_hours": ch}, timeout=30)
    except Exception:
        pass
    return json.dumps({
        "submitted": True, "job_id": job_id, "script": str(spath), "logs": str(logs),
        "note": "Do not poll in a loop. Summarize what you are waiting for and end your turn; "
                "you will be resumed when the job finishes.",
    })


@server.tool()
async def hpc_status(job_id: str) -> str:
    """Current state of one job (queued/running/completed/failed/…) with exit status if known."""
    info = await asyncio.to_thread(SCHED.status, job_id)
    return json.dumps(info.to_dict())


@server.tool()
async def hpc_queue() -> str:
    """All of the lab account's jobs currently known to the scheduler."""
    jobs = await asyncio.to_thread(SCHED.queue)
    return json.dumps([j.to_dict() for j in jobs])


@server.tool()
async def hpc_cancel(job_id: str) -> str:
    """Cancel a job you submitted."""
    return json.dumps({"job_id": job_id, "result": await asyncio.to_thread(SCHED.cancel, job_id)})


if __name__ == "__main__":
    server.run()
