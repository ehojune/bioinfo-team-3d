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
from contextlib import ExitStack
from pathlib import Path

import httpx

from ..adapters.held_dir import HeldDir
from ..adapters.owned import chmod_owned, plain_directory, write_owned
from ..settings import Settings
from ._mcpcompat import make_server, tool_failure
from .scheduler import (
    MAYBE_SUBMITTED, Scheduler, build_script, checked_job_id, sanitize_job_name, slurm_cluster_directive,
)

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
        "Submit and monitor batch jobs on the lab's HPC (SGE, PBS or Slurm). Use this for anything heavy "
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


def _share_workspace_parents(workdir: Path, workspace_root: Path, gid: int,
                             job_user: str) -> None:
    """Open only labhq-managed parents; require preconfigured traversal above them."""
    import pwd

    root = workspace_root.resolve()
    if root == Path(root.anchor):
        raise RuntimeError("runner.workspace_root must be a dedicated directory, not the filesystem root")
    workdir = workdir.resolve(strict=True)
    managed = [p for p in workdir.parents if p == root or root in p.parents] if root in workdir.parents else []
    managed.reverse()
    try:
        account = pwd.getpwnam(job_user)
    except KeyError as e:
        raise RuntimeError(f"hpc.user does not exist: {job_user}") from e
    groups = set(os.getgrouplist(job_user, account.pw_gid))
    for parent in (root.parents if managed else workdir.parents):
        st = parent.stat()
        permitted = (bool(st.st_mode & stat.S_IXUSR) if st.st_uid == account.pw_uid else
                     bool(st.st_mode & stat.S_IXGRP) if st.st_gid in groups else
                     bool(st.st_mode & stat.S_IXOTH))
        if not permitted:
            raise RuntimeError(f"hpc.user cannot traverse outside runner.workspace_root: {parent}; "
                               "configure parent execute permission")
    for parent in managed:
        try:
            os.chown(parent, -1, gid)
            os.chmod(parent, (stat.S_IMODE(parent.stat().st_mode) & 0o700) | 0o010)
        except OSError as e:
            raise RuntimeError(f"cannot grant hpc.job_group traverse on {parent}: {e}") from e


def _prepare_job_files(workdir: Path, script_path: Path, logs: Path, body: str,
                       job_group: str | None = None, workspace_root: Path | None = None,
                       job_user: str | None = None) -> None:
    try:
        script_relative = script_path.relative_to(workdir)
        logs_relative = logs.relative_to(workdir)
    except ValueError as exc:
        raise RuntimeError("job script and logs must stay inside the task workdir") from exc
    gid = None
    if job_group:
        if os.name == "nt":
            raise RuntimeError("hpc.job_group requires a POSIX runner")
        import grp

        gid = grp.getgrnam(job_group).gr_gid
        if workdir.is_symlink():
            raise RuntimeError(f"task workdir must not be a symlink: {workdir}")
        if workspace_root is None or not job_user:
            raise RuntimeError("runner.workspace_root and hpc.user are required to check job path traversal")
        # Validate traversal outside the managed root before creating jobs/logs.
        _share_workspace_parents(workdir, workspace_root, gid, job_user)
    jobs_dir = plain_directory(workdir, script_relative.parent)
    logs_dir = plain_directory(workdir, logs_relative)
    if jobs_dir is None or logs_dir is None:
        raise RuntimeError("shared job path is a link or not a directory")
    if os.name != "nt" and not job_group:
        with ExitStack() as stack:
            held_workdir = stack.enter_context(HeldDir.hold(workdir))
            held_jobs = stack.enter_context(held_workdir.child(script_relative.parts[0]))
            held_logs = stack.enter_context(held_jobs.child(logs_relative.parts[-1]))
            held_logs.chmod(0o700)
    if job_group:
        output_dir = workdir / "hpc_out"
        for shared in (script_path.parent, logs, output_dir):
            if shared.is_symlink():
                raise RuntimeError(f"shared job path must not be a symlink: {shared}")
        if script_path.is_symlink():
            raise RuntimeError(f"job script must not be a symlink: {script_path}")
        if script_path.exists() and script_path.stat().st_nlink > 1:
            raise RuntimeError(f"job script must not be a hard link: {script_path}")
        # Old workspaces or explicit workdir overrides may predate the runner's
        # private umask. Remove group/other access before granting group traversal.
        def fail_on_walk_error(error: OSError) -> None:
            raise error

        for root, dirs, files in os.walk(workdir, onerror=fail_on_walk_error, followlinks=False):
            dirs[:] = [name for name in dirs if Path(root) / name not in {logs, output_dir}]
            for name in [*dirs, *files]:
                path = Path(root) / name
                if path.is_symlink() and path.parent == workdir / "inputs":
                    continue  # the runner's inputs/<step> link to an upstream step (#423): not walked, not chmod-ed
                if path.is_symlink():
                    raise RuntimeError(f"task input must not be a symlink: {path}")
                st = path.stat()
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    raise RuntimeError(f"task input is a hard link; copy it before HPC submit: {path}")
                if path == script_path or (path.parent == script_path.parent and path.suffix == ".sh"
                                           and st.st_gid == gid and stat.S_IMODE(st.st_mode) == 0o750):
                    # A previously submitted script may still be queued.
                    continue
                mode = stat.S_IMODE(st.st_mode) & ~0o077
                if path == script_path.parent:
                    mode |= stat.S_IXGRP  # keep earlier jobs traversable during cleanup
                os.chmod(path, mode)
        if plain_directory(workdir, output_dir.relative_to(workdir)) is None:
            raise RuntimeError("shared output path is a link or not a directory")
    if job_group:
        # Hold every shared folder before changing it. A concurrent replacement is rejected; chmod/chown use the
        # handles, so a later rename cannot redirect them to an agent-controlled link target.
        try:
            with ExitStack() as stack:
                held_workdir = stack.enter_context(HeldDir.hold(workdir))
                held_jobs = stack.enter_context(held_workdir.child(script_relative.parts[0]))
                held_logs = stack.enter_context(held_jobs.child(logs_relative.parts[-1]))
                held_output = stack.enter_context(held_workdir.child(output_dir.name))
                for directory in (held_workdir, held_jobs, held_logs, held_output):
                    directory.chown(-1, gid)
                held_workdir.chmod(0o710)
                held_jobs.chmod(0o2750)
                held_logs.chmod(0o2770)
                held_output.chmod(0o2770)
        except OSError as exc:
            raise RuntimeError("shared job path is a link or was replaced") from exc
    write_owned(workdir, script_relative.as_posix(), body)
    if os.name != "nt":
        chmod_owned(workdir, script_relative.as_posix(), 0o750, gid if job_group else None)
    else:
        script_path.chmod(0o750)


@server.tool()
async def hpc_submit(script: str, job_name: str, cores: int = 1, mem: str = "4G",
                     walltime: str = "04:00:00", queue: str | None = None, reason: str = "") -> str:
    """Submit a bash script as a batch job.

    script: the script body (commands; scheduler directives optional). With account switching,
    relative outputs go to hpc_out/; otherwise it runs from your workspace.
    cores / mem (total, e.g. "32G") / walltime ("HH:MM:SS"). reason: why this job is needed.
    Returns JSON: {"submitted": true, "job_id": ...} or a normal PI denial.
    Operational failures are MCP tool errors; denial must not trigger resubmission.
    """
    if S.hpc.scheduler == "slurm" and (option := slurm_cluster_directive(script)):
        raise tool_failure(f"#SBATCH {option}: labhq tracks jobs on the default cluster only; "
                           "remove -M/--clusters and submit again")
    name = sanitize_job_name(job_name)
    logs = WORKDIR / "jobs" / "logs"
    output_dir = WORKDIR / "hpc_out" if S.hpc.submit_prefix else WORKDIR
    stamp = time.strftime("%Y%m%d-%H%M%S")
    spath = WORKDIR / "jobs" / f"{name}_{stamp}.sh"
    try:
        _prepare_job_files(WORKDIR, spath, logs,
                           build_script(script, str(output_dir), umask="007" if S.hpc.submit_prefix else None),
                           S.hpc.job_group if S.hpc.submit_prefix else None,
                           S.path(S.runner.workspace_root) if S.hpc.submit_prefix else None,
                           S.hpc.user if S.hpc.submit_prefix else None)
    except (OSError, KeyError, RuntimeError) as e:
        raise tool_failure(f"job file permissions: {e}") from e
    # The runner asks the PI when needed and runs the submit command itself: the id its own call returns is the
    # only record hpc_cancel trusts later, and a broker call from the agent cannot skip the approval (#214 review).
    try:
        out = await _broker("/jobs/submit", {
            "task_id": TASK, "agent_id": AGENT, "script": str(spath), "name": name, "cores": cores, "mem": mem,
            "walltime": walltime, "queue": queue, "reason": reason,
        }, timeout=S.policy.approvals.timeout_s + S.hpc.command_timeout_s + 60)
    except (httpx.ConnectError, ConnectionError) as e:  # fail closed
        raise tool_failure(f"labhq broker unreachable; the job was not submitted: {e}") from e
    except Exception as e:  # the runner may have submitted before the answer was lost
        raise tool_failure(f"labhq broker failed: {e!r}{MAYBE_SUBMITTED}") from e
    if out.get("error"):
        raise tool_failure(str(out["error"]))
    if not out.get("submitted"):
        return json.dumps({"submitted": False, "reason": out.get("reason") or "denied by the PI",
                           "note": "Do not resubmit this job unless the PI explicitly requests it."})
    job_id = out["job_id"]
    return json.dumps({
        "submitted": True, "job_id": job_id, "script": str(spath), "logs": str(logs),
        "output_dir": str(output_dir),
        "note": "Relative job outputs are in output_dir. Do not poll in a loop; "
                "summarize what you are waiting for and end your turn; "
                "you will be resumed when the job finishes.",
    })


@server.tool()
async def hpc_status(job_id: str) -> str:
    """Current state of one job (queued/running/completed/failed/…) with exit status if known."""
    try:
        info = await asyncio.to_thread(SCHED.status, job_id)
    except Exception as e:
        raise tool_failure(str(e)) from e
    return json.dumps(info.to_dict())


@server.tool()
async def hpc_queue() -> str:
    """All of the lab account's jobs currently known to the scheduler."""
    try:
        jobs = await asyncio.to_thread(SCHED.queue)
    except Exception as e:
        raise tool_failure(str(e)) from e
    return json.dumps([j.to_dict() for j in jobs])


@server.tool()
async def hpc_cancel(job_id: str) -> str:
    """Cancel a job you submitted with hpc_submit (or one of its array tasks)."""
    try:
        if S.hpc.scheduler != "mock":  # mock ids ("mock-…") never reach a scheduler command
            checked_job_id(job_id)
    except RuntimeError as e:
        raise tool_failure(str(e)) from e
    # The account may be shared (submit_prefix data account): a bare id could name someone else's job (#172).
    try:
        owned = await _broker("/jobs/owned", {"task_id": TASK, "agent_id": AGENT, "job_id": job_id}, timeout=30)
    except Exception as e:  # fail closed
        raise tool_failure(f"cannot confirm that labhq tracks job {job_id}: {e}") from e
    if not owned.get("owned"):
        raise tool_failure(f"job {job_id} was not submitted by you through hpc_submit; labhq cancels only jobs it "
                           "tracks for you. Ask the PI to cancel other jobs.")
    try:
        result = await asyncio.to_thread(SCHED.cancel, job_id)
    except Exception as e:
        raise tool_failure(str(e)) from e
    return json.dumps({"job_id": job_id, "result": result})


if __name__ == "__main__":
    server.run()
