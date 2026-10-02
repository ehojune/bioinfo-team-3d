"""HPC submission the runner performs itself (PR #214 review).

A task token reaches the broker from the agent's own process, so whatever it reports is a claim. The runner
runs the submit command and keeps the id the scheduler printed: only such an id is a job hpc_cancel may act on.
The approval decision is made here as well, so calling the broker directly cannot skip it.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import AsyncContextManager, Awaitable, Callable

from ..policy import core_hours, hpc_needs_approval
from ..settings import Settings
from ..tools.scheduler import Scheduler, sanitize_job_name, script_directives, slurm_cluster_directive

Approve = Callable[[dict], Awaitable[dict]]
Record = Callable[[dict], Awaitable[None]]
Gate = Callable[[], AsyncContextManager[bool]]


def _script(workdir: Path, raw: object) -> Path | None:
    """A .sh file directly in the task's jobs/ folder (where hpc_submit writes); neither may be a link."""
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or path.suffix != ".sh" or path.is_symlink() or not path.is_file():
        return None
    # Against the workdir itself: a jobs/ link to another folder resolves elsewhere and is refused.
    return path if path.parent.resolve() == Path(workdir).resolve() / "jobs" else None


async def submit_job(s: Settings, scheduler: Scheduler, workdir: Path, body: dict, approve: Approve,
                     gate: Gate | None = None, record: Record | None = None) -> dict:
    """{"submitted": True, "job_id", …}, {"submitted": False, "reason"} after a PI denial, or {"error"}.

    gate: entered right before the submit command, yielding whether the calling task run is still going; the
    runner holds the run's end until it exits. record: called inside the gate with a successful result.
    """
    spath = _script(workdir, body.get("script"))
    if spath is None:
        return {"error": f"job script must be a .sh file hpc_submit wrote in this task's jobs folder: "
                         f"{body.get('script')!r}"}
    queue = body.get("queue") or s.hpc.default_queue
    if queue is not None and not isinstance(queue, str):
        return {"error": f"queue must be a name: {queue!r}"}
    try:
        cores = int(body.get("cores", 1))
        mem, walltime = str(body.get("mem") or "4G"), str(body.get("walltime") or "04:00:00")
        ch = core_hours(cores, walltime)
        text = spath.read_text(encoding="utf-8")
    except (TypeError, ValueError, OSError) as e:
        return {"error": f"cannot submit {spath.name}: {e}"}
    if s.hpc.scheduler == "slurm" and (option := slurm_cluster_directive(text)):
        return {"error": f"#SBATCH {option}: labhq tracks jobs on the default cluster only; "
                         "remove -M/--clusters and submit again"}
    name = sanitize_job_name(str(body.get("name") or spath.stem))
    # Directives (#SBATCH --array, #$ -pe, #PBS -J, …) can ask for more than cores × walltime and the command
    # line does not override all of them, so any directive sends the job to the PI whatever the threshold (#172).
    directives = script_directives(text)
    approval_reasons = []
    if s.hpc.scheduler == "pbs" and s.hpc.ssh_host:
        approval_reasons.append("remote PBS_DPREFIX cannot be inspected locally")
    if hpc_needs_approval(cores, walltime, s.policy) or directives or approval_reasons:
        outside = f" · 스크립트 지시 {len(directives)}개는 core-h 계산 밖" if directives else ""
        dec = await approve({
            "kind": "hpc_submit",
            "summary": f"HPC 제출: {name} · {cores} cores · {mem} · {walltime} (~{ch:.1f} core-h){outside}",
            "detail": {"reason": str(body.get("reason") or ""), "script_path": str(spath),
                       "script_preview": "\n".join(text.splitlines()[:40]), "queue": queue,
                        "script_directives": directives[:50], "approval_reasons": approval_reasons},
            "timeout_s": s.policy.approvals.timeout_s,
        })
        if not dec.get("approved"):
            return {"submitted": False, "reason": dec.get("note") or "denied by the PI"}
        try:
            unchanged = _script(workdir, str(spath)) == spath and spath.read_text(encoding="utf-8") == text
        except OSError:
            unchanged = False
        if not unchanged:  # the PI approved what they were shown, not a later edit
            return {"error": f"{spath.name} changed while waiting for the PI; it was not submitted"}
    logs = spath.parent / "logs"
    async with (gate() if gate else contextlib.nullcontext(True)) as running:
        if not running:
            return {"error": "the task ended while waiting for the PI; the job was not submitted"}
        try:
            job_id = await asyncio.to_thread(scheduler.submit, str(spath), name, cores, mem, walltime, queue,
                                             str(logs / f"{spath.stem}.out"), str(logs / f"{spath.stem}.err"))
        except Exception as e:
            return {"error": str(e)}
        result = {"submitted": True, "job_id": job_id, "name": name, "script": str(spath), "core_hours": ch}
        if record:
            await record(result)
    return result
