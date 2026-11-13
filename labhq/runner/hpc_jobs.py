"""HPC submission the runner performs itself (PR #214 review).

A task token reaches the broker from the agent's own process, so whatever it reports is a claim. The runner
runs the submit command and keeps the id the scheduler printed: only such an id is a job hpc_cancel may act on.
The approval decision is made here as well, so calling the broker directly cannot skip it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from pathlib import Path
from typing import AsyncContextManager, Awaitable, Callable

from ..hpc_transfer import TransferError, job_spec, remote_script, upload_job
from ..policy import core_hours, hpc_needs_approval
from ..settings import Settings
from ..tools.scheduler import (Scheduler, sanitize_job_name, script_directives, slurm_cluster_directive,
                               slurm_heterogeneous_directive)

Approve = Callable[[dict], Awaitable[dict]]
Record = Callable[[dict], Awaitable[None]]
Gate = Callable[[], AsyncContextManager[bool]]


class SharedStatusQueries:
    """Share only in-flight batches in the same request's login scope; never cache an old job state.

    One cancelled waiter must not cancel the batch another employee or the poller still needs.
    Calls arriving while a batch is running join it only when it covers their ids.
    """

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self._pending = {}

    async def get(self, request_id, job_ids):
        from ..hpc_login_budget import request_scope
        from ..hpc_session import command_deadline, queue_wait

        ids = set(job_ids)
        batch = self._pending.get(request_id)
        if batch is None or batch[1].done() or not ids.issubset(batch[0]):
            cfg = getattr(self.scheduler, "cfg", None)
            session = self.scheduler.session() if cfg is not None else None
            from ..tools.scheduler import scheduler_call_budget_s

            budget_s = scheduler_call_budget_s(session.cfg if session else None, cfg.command_timeout_s, 2) if cfg else None

            def work():
                # Batching can fall back to per-id accounting. It must still finish within the staff status budget;
                # after this deadline no fallback command or reconnect is sent, even if a waiter has gone away.
                with request_scope(request_id), queue_wait(), command_deadline(budget_s):
                    return self.scheduler.statuses(sorted(ids))

            task = asyncio.create_task(asyncio.to_thread(work))
            batch = (ids, task)
            self._pending[request_id] = batch

            def finished(done):
                if self._pending.get(request_id) is batch:
                    self._pending.pop(request_id, None)
                if not done.cancelled():
                    done.exception()  # also consume a failure if every waiter has left

            task.add_done_callback(finished)
        result = await asyncio.shield(batch[1])
        return {jid: result[jid] for jid in job_ids}


def _script(workdir: Path, raw: object) -> Path | None:
    """A .sh file directly in the task's jobs/ folder (where hpc_submit writes); neither may be a link."""
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or path.suffix != ".sh" or path.is_symlink() or not path.is_file():
        return None
    # Against the workdir itself: a jobs/ link to another folder resolves elsewhere and is refused.
    return path if path.parent.resolve() == Path(workdir).resolve() / "jobs" else None


def _submits_remotely(s: Settings, scheduler: Scheduler) -> bool:
    """Does qsub run on another machine (over hpc.ssh_host, or in a site profile's shell)? The local environment says
    nothing about a remote PBS_DPREFIX there. A profile that cannot be read counts as remote: ask the PI."""
    if s.hpc.ssh_host:
        return True
    session = getattr(scheduler, "session", None)
    if session is None:
        return False
    try:
        return session() is not None
    except RuntimeError:
        return True


async def submit_job(s: Settings, scheduler: Scheduler, workdir: Path, body: dict, approve: Approve,
                     gate: Gate | None = None, record: Record | None = None, *, remote_data: dict | None = None) -> dict:
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
        script_bytes = spath.read_bytes()
        text = script_bytes.decode("utf-8")
    except (TypeError, ValueError, OSError, UnicodeError) as e:
        return {"error": f"cannot submit {spath.name}: {e}"}
    if s.hpc.scheduler == "slurm" and (option := slurm_cluster_directive(text)):
        return {"error": f"#SBATCH {option}: labhq tracks jobs on the default cluster only; "
                         "remove -M/--clusters and submit again"}
    name = sanitize_job_name(str(body.get("name") or spath.stem))
    # Validate the site's submission pin before approval or any remote upload. Scheduler.submit checks again at send.
    if s.hpc.scheduler != "mock" and hasattr(scheduler, "submit_args"):
        try:
            argv = scheduler.submit_args(str(spath), name, cores, mem, walltime, queue, "/dev/null", "/dev/null")
            if (s.hpc.scheduler == "slurm" and any(a.startswith("--nodelist=") for a in argv)
                    and slurm_heterogeneous_directive(text)):
                return {"error": "Pinned Slurm nodes cannot use heterogeneous job directives"}
        except Exception as e:
            return {"error": str(e)}
    # Without a shared file system (the profile names a remote_workdir) the script crosses the session shell and runs in
    # the cluster's work folder; the PI is shown the script as it will run there (#1308).
    spec = upload_text = None
    inputs = body.get("inputs")
    if inputs is not None and not (isinstance(inputs, list) and all(isinstance(i, str) for i in inputs)):
        return {"error": "inputs is a list of relative paths; the job was not submitted"}
    try:
        transfer = scheduler.transfer() if hasattr(scheduler, "transfer") else None
        if transfer is not None:
            snapshot = (remote_data or {}).get("request_snapshot")
            if snapshot:
                remote_snapshot = (remote_data.get("inputs") or {}).get("request-inputs")
                if (not isinstance(remote_snapshot, dict) or remote_snapshot.get("strict_snapshot") is not True
                        or remote_snapshot.get("files") != snapshot.get("files")
                        or remote_snapshot.get("directories") != snapshot.get("directories")):
                    return {"error": "remote request-input snapshot is missing or differs; the job was not submitted"}
            spec = job_spec(str(body.get("task_id") or ""), spath.stem,
                            remote_data.get("request_id") if remote_data else None)
            if remote_data:
                spec["remote_inputs"] = remote_data.get("inputs", {})
            upload_text = remote_script(text, transfer.path(spec["work"]))
            text = upload_text
    except (TransferError, RuntimeError) as e:
        return {"error": f"cannot prepare {spath.name} for the cluster: {e}; the job was not submitted"}
    # Directives (#SBATCH --array, #$ -pe, #PBS -J, …) can ask for more than cores × walltime and the command
    # line does not override all of them, so any directive sends the job to the PI whatever the threshold (#172).
    directives = script_directives(text)
    approval_reasons = []
    if s.hpc.scheduler == "pbs" and _submits_remotely(s, scheduler):
        approval_reasons.append("remote PBS_DPREFIX cannot be inspected locally")
    if hpc_needs_approval(cores, walltime, s.policy) or directives or approval_reasons:
        outside = f" · 스크립트 지시 {len(directives)}개는 core-h 계산 밖" if directives else ""
        dec = await approve({
            "kind": "hpc_submit",
            "summary": f"HPC 제출: {name} · {cores} cores · {mem} · {walltime} (~{ch:.1f} core-h){outside}",
            "detail": {"reason": str(body.get("reason") or ""), "script_path": str(spath),
                       "cores": cores, "mem": mem, "walltime": walltime,
                       "script_preview": "\n".join(text.splitlines()[:40]), "queue": queue,
                       **({"script_preview_truncated": True, "script_preview_chars": len(text)}
                          if len(text.splitlines()) > 40 else {}),
                        "script_directives": directives[:50], "approval_reasons": approval_reasons},
            "timeout_s": s.policy.approvals.timeout_s,
        })
        if not dec.get("approved"):
            return {"submitted": False, "reason": dec.get("note") or "denied by the PI"}
        try:
            unchanged = _script(workdir, str(spath)) == spath and spath.read_bytes() == script_bytes
        except OSError:
            unchanged = False
        if not unchanged:  # the PI approved what they were shown, not a later edit
            return {"error": f"{spath.name} changed while waiting for the PI; it was not submitted"}
    logs = spath.parent / "logs"

    async def request_input_error() -> str | None:
        if not remote_data or not remote_data.get("request_snapshot"):
            return None
        from ..research.input_proof import capture
        from .request_inputs import verify as verify_request_inputs
        try:
            approval = remote_data["request_input_approval"]
            if not approval.get("approval_id") or approval.get("runner_id") != s.runner.id:
                raise ValueError("request input approval is missing")
            observed = await asyncio.to_thread(capture, approval["descriptor"], s)
            if not observed or observed.get("sha256") != approval.get("sha256"):
                raise ValueError("request input differs from the approved preflight")
            await asyncio.to_thread(verify_request_inputs, workdir, remote_data["request_snapshot"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            return f"request input snapshot failed verification: {error}; the job was not submitted"
        return None

    async with (gate() if gate else contextlib.nullcontext(True)) as running:
        if not running:
            return {"error": "the task ended while waiting for the PI; the job was not submitted"}
        if error := await request_input_error():
            return {"error": error}
        script_arg, out_arg, err_arg = str(spath), str(logs / f"{spath.stem}.out"), str(logs / f"{spath.stem}.err")
        if spec:
            try:  # nothing is submitted until the far side holds exactly these bytes
                await asyncio.to_thread(upload_job, transfer, spec, upload_text, workdir, inputs)
            except Exception as e:  # noqa: BLE001 - a failed upload is reported with its reason; the job was not submitted
                return {"error": f"the job could not be put on the cluster: {e}; the job was not submitted"}
            script_arg, out_arg, err_arg = (transfer.path(spec["script"]), transfer.path(spec["logs"][0]),
                                            transfer.path(spec["logs"][1]))
        if error := await request_input_error():
            return {"error": error}
        if spec and remote_data and remote_data.get("request_snapshot"):
            from ..hpc_transfer import verify_remote_data
            try:
                for manifest in remote_data.get("inputs", {}).values():
                    await asyncio.to_thread(verify_remote_data, transfer, manifest, remote_data["request_id"])
            except Exception as error:  # fail closed on an unreadable or changed remote snapshot
                return {"error": f"remote input verification failed: {error}; the job was not submitted"}
        try:
            job_id = await asyncio.to_thread(scheduler.submit, script_arg, name, cores, mem, walltime, queue,
                                             out_arg, err_arg)
        except Exception as e:
            return {"error": str(e)}
        result = {"submitted": True, "job_id": job_id, "name": name, "script": str(spath),
                  "script_sha256": hashlib.sha256(script_bytes).hexdigest(), "core_hours": ch,
                  **({"transfer": spec} if spec else {})}
        if record:
            await record(result)
    return result
