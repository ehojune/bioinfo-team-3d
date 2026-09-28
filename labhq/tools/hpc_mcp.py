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
    if job_group:
        if os.name == "nt":
            raise RuntimeError("hpc.job_group requires a POSIX runner")
        import grp

        gid = grp.getgrnam(job_group).gr_gid
        if workdir.is_symlink():
            raise RuntimeError(f"task workdir must not be a symlink: {workdir}")
        if workspace_root is None or not job_user:
            raise RuntimeError("runner.workspace_root and hpc.user are required to check job path traversal")
        _share_workspace_parents(workdir, workspace_root, gid, job_user)
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
        output_dir.mkdir(mode=0o700, exist_ok=True)
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if job_group:
        for directory in (workdir, script_path.parent, logs, output_dir):
            os.chown(directory, -1, gid)
        # Allow traversal without making task inputs group-listable or writable.
        os.chmod(workdir, 0o710)
        os.chmod(script_path.parent, 0o2750)
        os.chmod(logs, 0o2770)
        os.chmod(output_dir, 0o2770)
    script_path.write_text(body, encoding="utf-8")
    if job_group:
        os.chown(script_path, -1, gid)
    script_path.chmod(0o750)


@server.tool()
async def hpc_submit(script: str, job_name: str, cores: int = 1, mem: str = "4G",
                     walltime: str = "04:00:00", queue: str | None = None, reason: str = "") -> str:
    """Submit a bash script as a batch job.

    script: the script body (commands; scheduler directives optional). With account switching,
    relative outputs go to hpc_out/; otherwise it runs from your workspace.
    cores / mem (total, e.g. "32G") / walltime ("HH:MM:SS"). reason: why this job is needed.
    Returns JSON: {"submitted": true, "job_id": ...} or {"submitted": false, "reason": ...}.
    """
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
        return json.dumps({"submitted": False, "reason": f"job file permissions: {e}"})
    ch = core_hours(cores, walltime)

    if hpc_needs_approval(cores, walltime, S.policy):
        preview = "\n".join(spath.read_text(encoding="utf-8").splitlines()[:40])
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
        "output_dir": str(output_dir),
        "note": "Relative job outputs are in output_dir. Do not poll in a loop; "
                "summarize what you are waiting for and end your turn; "
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
