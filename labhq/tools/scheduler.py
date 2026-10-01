"""SGE / PBS(Torque, Pro) / Slurm backend: submit, status, queue, cancel.

Parsers are pure functions so they can be unit-tested against real qstat/qacct/squeue/sacct output.
Cluster specifics (PE name, memory resource, PBS resource syntax, sbatch options) live in HpcSettings.
"""

from __future__ import annotations

import getpass
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass

from ..settings import HpcSettings

TERMINAL = {"completed", "failed", "cancelled", "unknown_finished"}

# Commands each backend runs on the runner (or hpc.ssh_host); doctor checks they are installed.
COMMANDS = {
    "sge": ("qsub", "qstat"),
    "pbs": ("qsub", "qstat"),
    "slurm": ("sbatch", "squeue", "sacct", "scancel"),
}

SGE_STATE = {
    "qw": "queued", "hqw": "held", "hRwq": "held", "r": "running", "t": "running",
    "Rr": "running", "Rt": "running", "s": "suspended", "S": "suspended", "ts": "suspended",
    "dr": "cancelling", "dt": "cancelling", "dqw": "cancelling", "Eqw": "error",
}
PBS_STATE = {
    "Q": "queued", "R": "running", "C": "completed", "E": "exiting", "H": "held", "W": "waiting",
    "T": "transit", "S": "suspended", "F": "completed", "B": "running", "X": "completed",
}
SLURM_STATE = {
    "PENDING": "queued", "CONFIGURING": "queued", "REQUEUED": "queued", "REQUEUE_FED": "queued",
    "RUNNING": "running", "COMPLETING": "running", "RESIZING": "running", "SIGNALING": "running",
    "STAGE_OUT": "running", "SUSPENDED": "suspended", "STOPPED": "suspended",
    "REQUEUE_HOLD": "held", "SPECIAL_EXIT": "held", "RESV_DEL_HOLD": "held",
    "COMPLETED": "completed", "CANCELLED": "cancelled",
    "FAILED": "failed", "TIMEOUT": "failed", "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed",
    "BOOT_FAIL": "failed", "DEADLINE": "failed", "PREEMPTED": "failed",
    "REVOKED": "failed",  # federation sibling removed because another cluster started it; not tracked here
}
# Name last: other users' job names may contain the delimiter.
SQUEUE_FORMAT = "%i|%T|%r|%j"
SACCT_FIELDS = "JobID,State,ExitCode,JobName"
# Agent-supplied ids reach qdel/scancel argv. Every SGE/PBS/Slurm id starts with a digit, so neither
# an option ("-u x", "--user=x") nor a selector of many jobs (Torque "all", SGE job names) gets through.
JOB_ID = re.compile(r"[0-9][A-Za-z0-9_.\[\]+-]{0,127}")
# sbatch options that send the job to another cluster; squeue/sacct/scancel would then find the wrong job.
SLURM_CLUSTER_OPTION = re.compile(r"-M|--clusters?(?:=|$)")
MAYBE_SUBMITTED = "; the job may have been submitted, check hpc_queue before resubmitting"


@dataclass
class JobInfo:
    job_id: str
    state: str  # queued|running|held|suspended|error|completed|failed|cancelled|missing|unknown_finished
    raw_state: str = ""
    name: str = ""
    exit_status: int | None = None
    detail: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["terminal"] = self.terminal
        return d


# ---------- helpers ----------

def parse_mem_gb(mem: str) -> float:
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?)(i?B)?\s*", mem, flags=re.I)
    if not m:
        raise ValueError(f"bad memory spec: {mem!r}")
    val, unit = float(m.group(1)), m.group(2).upper()
    return val * {"": 1 / 1024**3, "K": 1 / 1024**2, "M": 1 / 1024, "G": 1, "T": 1024}[unit]


def fmt_mem(gb: float) -> str:
    return f"{int(round(gb))}G" if gb >= 1 else f"{max(1, int(round(gb * 1024)))}M"


def normalize_walltime(w: str) -> str:
    from ..policy import walltime_hours

    total = int(round(walltime_hours(w) * 3600))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def sanitize_job_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip()) or "job"
    return n if n[0].isalpha() else f"j_{n}"


def checked_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise RuntimeError(f"invalid job id: {job_id!r}")
    return job_id


def slurm_cluster_directive(body: str) -> str | None:
    """First -M/--clusters option in an #SBATCH line, or None."""
    for line in body.splitlines():
        if line.startswith("#SBATCH"):
            for token in line[len("#SBATCH"):].split():
                if SLURM_CLUSTER_OPTION.match(token):
                    return token
    return None


def build_script(body: str, workdir: str, *, umask: str | None = None) -> str:
    """Wrap an agent-written script: shebang, scheduler directives, strict mode, cd, exit trace."""
    lines = body.strip("\n").splitlines()
    shebang = lines.pop(0) if lines and lines[0].startswith("#!") else "#!/bin/bash"
    # qsub and sbatch read #$ / #PBS / #SBATCH only before the first command, so the whole leading
    # comment block stays above labhq's preamble (a directive below a command is silently ignored).
    header = []
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        header.append(lines.pop(0))
    pre = [
        "set -euo pipefail",
        *([f"umask {umask}"] if umask else []),
        f"cd {shlex.quote(workdir)}",
        "trap 'echo \"[labhq] exit=$? end=$(date -Is)\"' EXIT",
        'echo "[labhq] host=$(hostname) start=$(date -Is)"',
    ]
    return "\n".join([shebang, *header, *pre, *lines]) + "\n"


def _detail(p: subprocess.CompletedProcess) -> str:
    return (p.stderr or "").strip() or (p.stdout or "").strip()


# ---------- parsers ----------

def parse_sge_qstat(text: str) -> list[JobInfo]:
    jobs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit():
            raw = parts[4]
            state = SGE_STATE.get(raw, "error" if "E" in raw else "unknown")
            jobs.append(JobInfo(job_id=parts[0], name=parts[2], state=state, raw_state=raw))
    return jobs


def parse_sge_qacct(job_id: str, text: str) -> JobInfo:
    kv: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\w+)\s+(.*)$", line)
        if m:
            kv[m.group(1)] = m.group(2).strip()
    failed = kv.get("failed", "0").split()[0]
    exit_status = int(kv.get("exit_status", "0").split()[0])
    ok = failed == "0" and exit_status == 0
    return JobInfo(
        job_id=job_id, name=kv.get("jobname", ""), state="completed" if ok else "failed",
        raw_state=f"failed={failed}", exit_status=exit_status,
        detail="" if ok else kv.get("failed", ""),
    )


def parse_pbs_qstat_table(text: str) -> list[JobInfo]:
    jobs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 10 and re.match(r"^\d+(\[\d*\])?(\.\S+)?$", parts[0]):
            raw = parts[-2]
            jobs.append(JobInfo(job_id=parts[0], name=parts[3], state=PBS_STATE.get(raw, "unknown"), raw_state=raw))
    return jobs


def parse_pbs_qstat_full(text: str) -> JobInfo | None:
    attrs: dict[str, str] = {}
    job_id, key = None, None
    for line in text.splitlines():
        m = re.match(r"^Job Id:\s*(\S+)", line)
        if m:
            job_id = m.group(1)
            continue
        m = re.match(r"^\s+([\w.]+)\s*=\s*(.*)$", line)
        if m:
            key = m.group(1)
            attrs[key] = m.group(2).strip()
        elif key and line.startswith("\t"):
            attrs[key] += line.strip()
    if not job_id:
        return None
    raw = attrs.get("job_state", "")
    state = PBS_STATE.get(raw, "unknown")
    exit_raw = attrs.get("exit_status") or attrs.get("Exit_status")
    exit_status = int(exit_raw) if exit_raw and re.fullmatch(r"-?\d+", exit_raw) else None
    if state == "completed" and exit_status not in (None, 0):
        state = "failed"
    return JobInfo(job_id=job_id, name=attrs.get("Job_Name", ""), state=state, raw_state=raw, exit_status=exit_status)


def _slurm_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.strip().split("|", 3)]
        if len(parts) == 4 and re.match(r"\d", parts[0]):
            rows.append(parts)
    return rows


def parse_slurm_squeue(text: str) -> list[JobInfo]:
    """`squeue -h -o "%i|%T|%r|%j"`: id, long state, reason, name."""
    jobs = []
    for job_id, raw, reason, name in _slurm_rows(text):
        state = SLURM_STATE.get(raw, "unknown")
        if state == "queued" and "held" in reason.lower():  # JobHeldUser, JobHeldAdmin, …
            state = "held"
        detail = reason if state in ("queued", "held") and reason not in ("", "None") else ""
        jobs.append(JobInfo(job_id=job_id, name=name, state=state, raw_state=raw, detail=detail))
    return jobs


def parse_slurm_sacct(text: str) -> list[JobInfo]:
    """`sacct -n -P -X -o JobID,State,ExitCode,JobName`; State may read "CANCELLED by 1000"."""
    jobs = []
    for job_id, raw, exit_code, name in _slurm_rows(text):
        state = SLURM_STATE.get(raw.split()[0] if raw else "", "unknown")
        m = re.fullmatch(r"(\d+):(\d+)", exit_code)
        exit_status = int(m.group(1)) if m else None
        if state == "completed" and (exit_status not in (None, 0) or (m and m.group(2) != "0")):
            state = "failed"
        detail = f"{raw} ExitCode={exit_code}" if state in ("failed", "cancelled") else ""
        jobs.append(JobInfo(job_id=job_id, name=name, state=state, raw_state=raw,
                            exit_status=exit_status, detail=detail))
    return jobs


def slurm_job(job_id: str, records: list[JobInfo]) -> JobInfo | None:
    """One job from its records; array tasks (123_4) and het components (123+0) roll up into 123."""
    mine = [r for r in records if r.job_id == job_id or r.job_id.startswith((f"{job_id}_", f"{job_id}+"))]
    if not mine:
        return None
    active = [r for r in mine if not r.terminal]
    if active:
        pick = next((r for r in active if r.state == "running"), active[0])
    else:
        pick = next((r for state in ("failed", "cancelled") for r in mine if r.state == state), mine[0])
    detail = pick.detail
    if len(mine) > 1:
        detail = "; ".join(filter(None, [detail, f"{len(mine)} records ({len(active)} active)"]))
    return JobInfo(job_id=job_id, state=pick.state, raw_state=pick.raw_state, name=pick.name,
                   exit_status=pick.exit_status, detail=detail)


# ---------- backend ----------

class Scheduler:
    def __init__(self, cfg: HpcSettings):
        self.cfg = cfg
        self.user = cfg.user or getpass.getuser()

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        if self.cfg.ssh_host:
            args = ["ssh", "-o", "BatchMode=yes", self.cfg.ssh_host, shlex.join(args)]
        return subprocess.run(args, capture_output=True, text=True, timeout=self.cfg.command_timeout_s)

    def submit_args(self, script: str, name: str, cores: int, mem: str, walltime: str,
                    queue: str | None, stdout: str, stderr: str) -> list[str]:
        name = sanitize_job_name(name)
        wt = normalize_walltime(walltime)
        if self.cfg.scheduler == "sge":
            c = self.cfg.sge
            gb = parse_mem_gb(mem) / (max(1, cores) if c.mem_per_slot else 1)
            args = ["qsub", "-terse", "-N", name, "-o", stdout, "-e", stderr]
            if cores > 1:
                args += ["-pe", c.pe, str(cores)]
            args += ["-l", f"{c.mem_resource}={fmt_mem(gb)}"]
            if c.runtime_resource:
                args += ["-l", f"{c.runtime_resource}={wt}"]
        elif self.cfg.scheduler == "pbs":
            res = self.cfg.pbs.resource_template.format(cores=cores, mem=fmt_mem(parse_mem_gb(mem)).lower() + "b", walltime=wt)
            args = ["qsub", "-N", name[:15], "-l", res, "-o", stdout, "-e", stderr]
        elif self.cfg.scheduler == "slurm":
            fields = {"cores": cores, "mem": fmt_mem(parse_mem_gb(mem)), "walltime": wt}
            # sbatch expands %j, %x … in log paths; %% keeps a literal percent sign.
            args = ["sbatch", "--parsable", f"--job-name={name}",
                    f"--output={stdout.replace('%', '%%')}", f"--error={stderr.replace('%', '%%')}",
                    *(arg.format(**fields) for arg in self.cfg.slurm.sbatch_args)]
            if queue:
                args.append(f"--partition={queue}")  # sbatch -q is a QOS, not a queue
            return [*self.cfg.submit_prefix, *args, script]
        else:
            raise RuntimeError(f"submit not supported for scheduler={self.cfg.scheduler}")
        if queue:
            args += ["-q", queue]
        return [*self.cfg.submit_prefix, *args, script]

    def submit(self, script: str, name: str, cores: int = 1, mem: str = "4G", walltime: str = "04:00:00",
               queue: str | None = None, stdout: str = "/dev/null", stderr: str = "/dev/null") -> str:
        """Run the submit command once. labhq never retries it: a second call could double-submit."""
        if self.cfg.scheduler == "mock":
            return f"mock-{abs(hash((script, name))) % 10**6}"
        cmd = "sbatch" if self.cfg.scheduler == "slurm" else "qsub"
        try:
            p = self._run(self.submit_args(script, name, cores, mem, walltime, queue, stdout, stderr))
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{cmd} timed out after {self.cfg.command_timeout_s}s{MAYBE_SUBMITTED}") from e
        if p.returncode != 0:
            raise RuntimeError(f"{cmd} failed ({p.returncode}): {_detail(p)}")
        lines = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError(f"{cmd} printed no job id{MAYBE_SUBMITTED}")
        out = lines[-1]
        if self.cfg.scheduler == "sge":
            m = re.search(r"(\d+)", out)  # -terse → "12345" or "12345.1-10:1"
        elif self.cfg.scheduler == "slurm":
            m = re.fullmatch(r"(\d+)(?:;(\S+))?", out)  # --parsable → "12345", or "12345;cluster" under -M
            if m and m.group(2):
                # The bare id names a different job (or none) on the cluster squeue/sacct/scancel query.
                raise RuntimeError(f"sbatch submitted job {m.group(1)} to cluster {m.group(2)} (-M/--clusters or "
                                   "SBATCH_CLUSTERS); labhq tracks the default cluster only, so this job is not "
                                   f"tracked. Ask the PI before resubmitting; to stop it: scancel -M {m.group(2)} "
                                   f"{m.group(1)}")
        else:
            return out  # PBS → "12345.server"
        if not m:
            raise RuntimeError(f"cannot parse job id from: {out!r}{MAYBE_SUBMITTED}")
        return m.group(1)

    def queue(self) -> list[JobInfo]:
        if self.cfg.scheduler == "mock":
            return []
        if self.cfg.scheduler == "slurm":
            # -r: one line per array task, so every listed id is one hpc_status/hpc_cancel accepts
            # (without it pending tasks read "4100_[1-4,8]").
            p = self._run(["squeue", "-h", "-r", "-u", self.user, "-o", SQUEUE_FORMAT])
            if p.returncode != 0:
                raise RuntimeError(f"squeue failed ({p.returncode}): {_detail(p)}")
            jobs = parse_slurm_squeue(p.stdout)
            if p.stdout.strip() and not jobs:
                raise RuntimeError("squeue returned unexpected output; the queue is unknown")
            return jobs
        p = self._run(["qstat", "-u", self.user])
        if p.returncode != 0:
            raise RuntimeError(f"qstat failed ({p.returncode}): {_detail(p)}")
        return parse_sge_qstat(p.stdout) if self.cfg.scheduler == "sge" else parse_pbs_qstat_table(p.stdout)

    def status(self, job_id: str) -> JobInfo:
        if self.cfg.scheduler == "mock":  # ids are "mock-…" and nothing is run
            return JobInfo(job_id=job_id, state="completed", exit_status=0)
        checked_job_id(job_id)
        if self.cfg.scheduler == "slurm":
            return self._slurm_status(job_id)
        if self.cfg.scheduler == "sge":
            for j in self.queue():
                if j.job_id == job_id:
                    return j
            p = self._run(["qacct", "-j", job_id])
            if p.returncode != 0:
                detail = _detail(p)
                if re.fullmatch(rf"error: job id {re.escape(job_id)} not found", detail, re.I):
                    return JobInfo(job_id=job_id, state="missing")  # accounting can lag; watcher retries
                raise RuntimeError(f"qacct failed ({p.returncode}): {detail}")
            if "exit_status" not in p.stdout:
                raise RuntimeError("qacct returned no exit_status; job state is unknown")
            return parse_sge_qacct(job_id, p.stdout)
        args = ["qstat", "-fx", job_id] if self.cfg.pbs.pro else ["qstat", "-f", job_id]
        p = self._run(args)
        if p.returncode != 0:
            detail = _detail(p)
            if re.fullmatch(rf"qstat: Unknown Job Id(?: Error)? {re.escape(job_id)}", detail, re.I):
                return JobInfo(job_id=job_id, state="missing")
            raise RuntimeError(f"qstat failed ({p.returncode}): {detail}")
        info = parse_pbs_qstat_full(p.stdout)
        if info is None:
            raise RuntimeError("qstat returned no job record; job state is unknown")
        return info

    def _slurm_status(self, job_id: str) -> JobInfo:
        """squeue while slurmctld holds the job, then sacct for the final state and exit code."""
        p = self._run(["squeue", "-h", "-r", "-t", "all", "-j", job_id, "-o", SQUEUE_FORMAT])
        live = None
        if p.returncode == 0:
            records = parse_slurm_squeue(p.stdout)
            if p.stdout.strip() and not records:
                raise RuntimeError("squeue returned unexpected output; job state is unknown")
            live = slurm_job(job_id, records)
            if live and not live.terminal:
                return live
        elif not re.fullmatch(r"slurm_load_jobs error: Invalid job id specified", _detail(p), re.I):
            raise RuntimeError(f"squeue failed ({p.returncode}): {_detail(p)}")
        p = self._run(["sacct", "-n", "-P", "-X", "-j", job_id, "-o", SACCT_FIELDS])
        if p.returncode != 0:
            raise RuntimeError(f"sacct failed ({p.returncode}): {_detail(p)}")
        records = parse_slurm_sacct(p.stdout)
        if p.stdout.strip() and not records:
            raise RuntimeError("sacct returned unexpected output; job state is unknown")
        # Past squeue the controller no longer holds the job as active, so it has ended: a state this table
        # does not know is a finish with unknown outcome, not a job that stays active forever (#186).
        for r in records:
            if r.state == "unknown":
                r.state, r.detail = "unknown_finished", f"unrecognized Slurm state {r.raw_state}"
        # Accounting can lag behind the controller: keep squeue's final state, else let the watcher retry.
        return slurm_job(job_id, records) or live or JobInfo(job_id=job_id, state="missing")

    def cancel(self, job_id: str) -> str:
        if self.cfg.scheduler == "mock":
            return "cancelled (mock)"
        checked_job_id(job_id)
        cmd = "scancel" if self.cfg.scheduler == "slurm" else "qdel"
        p = self._run([*self.cfg.submit_prefix, cmd, job_id])
        if p.returncode != 0:
            detail = (p.stderr or p.stdout).strip()
            hint = f"; allow {cmd} in sudoers for hpc.submit_prefix" if self.cfg.submit_prefix else ""
            raise RuntimeError(f"{cmd} failed ({p.returncode}): {detail}{hint}")
        return (p.stdout or p.stderr).strip()
