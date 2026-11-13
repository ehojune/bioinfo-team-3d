"""labhq-hpc MCP server (stdio). Launched per task by the runner.

Env (set by the runner): LABHQ_BROKER_URL, LABHQ_BROKER_TOKEN, LABHQ_TASK_ID, LABHQ_AGENT_ID,
LABHQ_WORKDIR, LABHQ_CONFIG.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from contextlib import ExitStack
from pathlib import Path

import httpx

from ..adapters.held_dir import HeldDir
from ..adapters.owned import chmod_owned, plain_directory, read_owned, write_owned, write_owned_bytes
from ..hpc_transfer import UPLOAD_BUDGET_S, TransferError, read_confined
from ..settings import Settings
from ._mcpcompat import make_server, tool_failure
from .scheduler import (
    MAYBE_SUBMITTED, Scheduler, build_script, checked_job_id, sanitize_job_name, scheduler_call_budget_s,
    slurm_cluster_directive,
)

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
SCHED = Scheduler(S.hpc)
# All scheduler operations belong to the runner, including plain ssh_host and local scheduler commands. An employee
# process never opens a scheduler connection. Inspecting the profile below is lazy and opens no login.
VIA_RUNNER = True
try:
    SCHED.session()
    SESSION_ERROR = None
except RuntimeError as _error:
    SESSION_ERROR = str(_error)
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


def _hpc_wait_s(commands: int = 1) -> float:
    """Queueing, every reconnect attempt/backoff and execution for each command in this call."""
    session = None if SESSION_ERROR else SCHED.session()
    # the runner stands in line for the shell at most QUEUE_WAIT_S, then reconnects and runs: after that it gives up
    # without sending; every command in a multi-command status gets this full allowance
    return scheduler_call_budget_s(session.cfg if session else None, float(S.hpc.command_timeout_s), commands)


def _submit_wait_s() -> float:
    """The runner's time for a submit: the shell command, and where files cross the shell (no shared file system) the
    whole upload (its own time allowance) before it."""
    wait = _hpc_wait_s()
    try:
        transfers = not SESSION_ERROR and SCHED.transfer() is not None
    except RuntimeError:
        transfers = False
    return wait + (wait + UPLOAD_BUDGET_S if transfers else 0)


async def _runner_op(op: str, **fields) -> dict:
    """Run a scheduler command through the runner's HPC shell. The runner decides; an error comes back as text."""
    if SESSION_ERROR:
        raise tool_failure(SESSION_ERROR)
    try:
        out = await _broker(f"/jobs/scheduler/{op}", {"task_id": TASK, "agent_id": AGENT, **fields},
                            timeout=_hpc_wait_s(2 if op == "status" else 1) + 30)
    except Exception as e:  # fail closed: without the runner there is no shell to ask
        raise tool_failure(f"labhq broker failed; the HPC command outcome is unknown: {e!r}") from e
    if out.get("error"):
        raise tool_failure(str(out["error"]))
    return out


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


def _share_data_inputs(workdir: Path, snapshots: dict[str, str], gid: int) -> None:
    """Grant only this run's inventoried snapshot files, checking bytes before opening group access."""
    from ..util import INPUT_STEP_ID

    if workdir.parent.name != "steps":
        raise RuntimeError("shared data requires a step workspace")
    root = workdir.parent.parent
    budget = [S.runner.input_hash_max_total_bytes]
    for step, raw in snapshots.items():
        snapshot = Path(raw)
        task_id = snapshot.name
        if (not INPUT_STEP_ID.fullmatch(step) or not INPUT_STEP_ID.fullmatch(task_id)
                or step in {".", ".."} or task_id in {".", ".."}
                or snapshot != root / "data" / step / task_id):
            raise RuntimeError("invalid shared data identity")
        records = json.loads(read_owned(root, f".labhq/hpc-inputs/{step}/{task_id}.json") or "null")
        if not isinstance(records, list) or len(records) > 20_000:
            raise RuntimeError("shared data inventory is missing or exceeds limit")
        for row in records:
            name = row.get("path")
            parts = Path(name).parts if isinstance(name, str) else ()
            if (not parts or Path(name).is_absolute() or any(p in {".", ".."} for p in parts)
                    or row.get("producer_step") != step or row.get("task_id") != task_id):
                raise RuntimeError("invalid shared data inventory")
            with ExitStack() as stack:
                folders = [stack.enter_context(HeldDir.hold(root))]
                for part in ("data", step, task_id, *parts[:-1]):
                    folders.append(stack.enter_context(folders[-1].child(part)))
                with os.fdopen(folders[-1].open_read_file(parts[-1]), "rb") as opened:
                    info = os.fstat(opened.fileno())
                    if info.st_nlink != 1 or info.st_size > S.runner.input_hash_max_file_bytes:
                        raise RuntimeError("shared data is linked or exceeds size limit")
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := opened.read(1024 * 1024):
                        size += len(chunk)
                        budget[0] -= len(chunk)
                        if size > S.runner.input_hash_max_file_bytes or budget[0] < 0:
                            raise RuntimeError("shared data exceeds size limit")
                        digest.update(chunk)
                    if digest.hexdigest() != row.get("sha256"):
                        raise RuntimeError("shared data hash differs")
                    os.fchown(opened.fileno(), -1, gid)
                    os.fchmod(opened.fileno(), 0o440)
                for folder in folders[1:]:  # request root is already traversal-only; never open its metadata
                    folder.chown(-1, gid)
                    folder.chmod(0o2750)


def _prepare_job_files(workdir: Path, script_path: Path, logs: Path, body: str,
                       job_group: str | None = None, workspace_root: Path | None = None,
                       job_user: str | None = None, *, output_name: str = "hpc_out",
                       data_inputs: dict[str, str] | None = None) -> None:
    if output_name not in {"hpc_out", "outputs"}:
        raise RuntimeError("unknown shared output directory")
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
        if output_name == "outputs" and data_inputs:
            _share_data_inputs(workdir, data_inputs, gid)
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
        output_dir = workdir / output_name
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
    # Bytes, not text: a Windows runner's text mode writes CRLF, and sbatch refuses a script with DOS line breaks
    # (#1331). build_script already joins with \n.
    write_owned_bytes(workdir, script_relative.as_posix(), body.encode("utf-8"))
    if os.name != "nt":
        chmod_owned(workdir, script_relative.as_posix(), 0o750, gid if job_group else None)
    else:
        script_path.chmod(0o750)


SHELL_SUFFIXES = {".sh", ".bash"}


def crlf_shell_inputs(workdir: Path, inputs: list[str]) -> list[str]:
    """The shell scripts among ``inputs`` that have CRLF line endings (#1331). A script written on Windows with
    PowerShell or a CRLF editor runs on the cluster's bash as `cmd\r`, which fails with a confusing error long after the
    submit. Each file is read with ``read_confined`` (held, no-follow folder handles inside the workspace, the upload
    size limit), so a link, an absolute path or a special file is skipped, never followed or opened; the upload step
    refuses those itself."""
    found = []
    for relative in inputs:
        try:
            head = read_confined(workdir, relative, 512 * 1024)
        except (TransferError, OSError, ValueError):
            continue
        shell = Path(relative).suffix.lower() in SHELL_SUFFIXES or (
            head.startswith(b"#!") and b"sh" in head.split(b"\n", 1)[0])
        if shell and b"\r\n" in head:
            found.append(relative)
    return found


@server.tool()
async def hpc_submit(script: str, job_name: str, cores: int = 1, mem: str = "4G",
                     walltime: str = "04:00:00", queue: str | None = None, reason: str = "",
                     inputs: list[str] | None = None) -> str:
    """Submit a bash script as a batch job.

    script: the script body (commands; scheduler directives optional). With account switching,
    relative outputs go to hpc_out/; otherwise it runs from your workspace.
    cores / mem (total, e.g. "32G") / walltime ("HH:MM:SS"). Set walltime explicitly to about twice
    estimated execution time, excluding queue wait (20 minutes -> "00:40:00"). reason: why this job is
    needed, estimated runtime and its basis (input size, throughput, tool/core count or a comparable measured
    run), plus any different margin. The 04:00:00 default is compatibility fallback, not a runtime estimate.
    If runtime is unknown, say so and obtain missing input/resource information or a small measurement within
    existing execution approval; this guidance authorizes no new execution. Do not invent an estimate or use
    a large default or zero/unlimited as one. If the proposed limit exceeds a site or PI limit, propose splitting
    the work or ask for a decision; never silently clip it. Existing approval and resubmission protections apply.
    inputs: small files your script reads, as paths relative to your workspace (at most 20, 512 KiB each). A cluster
    that does not share files with this machine gets them in the job's work folder under the same relative path; a
    cluster that does share them ignores this list. Larger files are not sent: say so in your reason.
    Returns JSON: {"submitted": true, "job_id": ...} or a normal PI denial.
    Operational failures are MCP tool errors; denial must not trigger resubmission.
    """
    mode = os.environ.get("LABHQ_EXECUTION_MODE", "")
    if mode == "local":
        raise tool_failure("this request selected local execution; HPC submission is disabled")
    if S.hpc.scheduler == "slurm" and (option := slurm_cluster_directive(script)):
        raise tool_failure(f"#SBATCH {option}: labhq tracks jobs on the default cluster only; "
                           "remove -M/--clusters and submit again")
    if inputs is not None and (not isinstance(inputs, list) or len(inputs) > 20
                               or not all(isinstance(i, str) and i and not i.startswith("/") and ".." not in i.split("/")
                                          for i in inputs)):
        raise tool_failure("inputs is a list of at most 20 relative paths inside your workspace")
    if any(part.casefold().rstrip(' .') == '.tmp' for item in inputs or []
           for part in item.replace('\\', '/').split('/')):
        raise tool_failure(".tmp/ is private task scratch and cannot be an HPC job input. "
                           "Move scripts to outputs/scripts/ and small supporting files to outputs/; "
                           "update the script and inputs paths before submitting. Upload limits are unchanged.")
    if crlf := crlf_shell_inputs(WORKDIR, inputs or []):
        raise tool_failure("CRLF line endings in " + ", ".join(crlf) + ": bash on the cluster reads the \\r as part "
                           "of each command and fails. Rewrite them with LF line endings and submit again")
    upload_inputs = inputs  # `inputs` is reused below for the shared data snapshots
    name = sanitize_job_name(job_name)
    logs = WORKDIR / "jobs" / "logs"
    output_dir = WORKDIR / "outputs" if mode == "hpc" else WORKDIR / "hpc_out" if S.hpc.submit_prefix else WORKDIR
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if mode == "hpc":
        stamp += "_" + sanitize_job_name(TASK or "task")
    spath = WORKDIR / "jobs" / f"{name}_{stamp}.sh"
    try:
        inputs = {}
        if mode == "hpc" and S.hpc.submit_prefix:
            manifest = json.loads(read_owned(WORKDIR, "manifest.json") or "{}")
            inputs = ((manifest.get("runs") or {}).get(TASK) or {}).get("hpc_data_inputs") or {}
            if not isinstance(inputs, dict):
                raise RuntimeError("shared data snapshot mapping is invalid")
        _prepare_job_files(WORKDIR, spath, logs,
                           build_script(script, str(output_dir), umask="007" if S.hpc.submit_prefix else None),
                           S.hpc.job_group if S.hpc.submit_prefix else None,
                           S.path(S.runner.workspace_root) if S.hpc.submit_prefix else None,
                           S.hpc.user if S.hpc.submit_prefix else None,
                           **({"output_name": "outputs", "data_inputs": inputs} if mode == "hpc" else {}))
    except (OSError, KeyError, RuntimeError) as e:
        raise tool_failure(f"job file permissions: {e}") from e
    # The runner asks the PI when needed and runs the submit command itself: the id its own call returns is the
    # only record hpc_cancel trusts later, and a broker call from the agent cannot skip the approval (#214 review).
    try:
        out = await _broker("/jobs/submit", {
            "task_id": TASK, "agent_id": AGENT, "script": str(spath), "name": name, "cores": cores, "mem": mem,
            "walltime": walltime, "queue": queue, "reason": reason, "inputs": upload_inputs,
        }, timeout=S.policy.approvals.timeout_s + _submit_wait_s() + 30)
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
        **({"remote_output_dir": SCHED.transfer().path(out["transfer"]["work"]),
            "remote_data_dir": SCHED.transfer().path(out["transfer"]["data"]),
            "retention": "Remote outputs remain in the request data folder; only small files are fetched locally."}
           if out.get("transfer", {}).get("request_id") else {}),
        "note": "Relative job outputs are in output_dir. Do not poll in a loop; "
                "summarize what you are waiting for and end your turn; "
                "you will be resumed when the job finishes.",
    })


@server.tool()
async def hpc_status(job_id: str) -> str:
    """Current state of one job (queued/running/completed/failed/…) with exit status if known."""
    return json.dumps((await _runner_op("status", job_id=job_id))["status"])


@server.tool()
async def hpc_queue() -> str:
    """List owned tracked jobs through the runner. An empty queue cannot resolve a submission with an unknown outcome."""
    return json.dumps((await _runner_op("queue"))["queue"])


@server.tool()
async def hpc_cancel(job_id: str) -> str:
    """Cancel a job you submitted with hpc_submit (or one of its array tasks)."""
    try:
        if S.hpc.scheduler != "mock":  # mock ids ("mock-…") never reach a scheduler command
            checked_job_id(job_id)
    except RuntimeError as e:
        raise tool_failure(str(e)) from e
    return json.dumps({"job_id": job_id, "result": (await _runner_op("cancel", job_id=job_id))["result"]})


if __name__ == "__main__":
    server.run()
