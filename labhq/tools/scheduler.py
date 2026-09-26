"""SGE / PBS(Torque, Pro) backend: submit, status, queue, cancel.

Parsers are pure functions so they can be unit-tested against real qstat/qacct output.
Cluster specifics (PE name, memory resource, PBS resource syntax) live in HpcSettings.
"""

from __future__ import annotations

import getpass
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass

from ..settings import HpcSettings

TERMINAL = {"completed", "failed", "cancelled", "unknown_finished"}

SGE_STATE = {
    "qw": "queued", "hqw": "held", "hRwq": "held", "r": "running", "t": "running",
    "Rr": "running", "Rt": "running", "s": "suspended", "S": "suspended", "ts": "suspended",
    "dr": "cancelling", "dt": "cancelling", "dqw": "cancelling", "Eqw": "error",
}
PBS_STATE = {
    "Q": "queued", "R": "running", "C": "completed", "E": "exiting", "H": "held", "W": "waiting",
    "T": "transit", "S": "suspended", "F": "completed", "B": "running", "X": "completed",
}


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


def build_script(body: str, workdir: str) -> str:
    """Wrap an agent-written script: shebang, scheduler directives, strict mode, cd, exit trace."""
    lines = body.strip("\n").splitlines()
    shebang = lines.pop(0) if lines and lines[0].startswith("#!") else "#!/bin/bash"
    directives = []
    while lines and (lines[0].startswith("#$") or lines[0].startswith("#PBS")):
        directives.append(lines.pop(0))
    pre = [
        "set -euo pipefail",
        f"cd {shlex.quote(workdir)}",
        "trap 'echo \"[labhq] exit=$? end=$(date -Is)\"' EXIT",
        'echo "[labhq] host=$(hostname) start=$(date -Is)"',
    ]
    return "\n".join([shebang, *directives, *pre, *lines]) + "\n"


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
        else:
            raise RuntimeError(f"submit not supported for scheduler={self.cfg.scheduler}")
        if queue:
            args += ["-q", queue]
        return [*self.cfg.submit_prefix, *args, script]

    def submit(self, script: str, name: str, cores: int = 1, mem: str = "4G", walltime: str = "04:00:00",
               queue: str | None = None, stdout: str = "/dev/null", stderr: str = "/dev/null") -> str:
        if self.cfg.scheduler == "mock":
            return f"mock-{abs(hash((script, name))) % 10**6}"
        p = self._run(self.submit_args(script, name, cores, mem, walltime, queue, stdout, stderr))
        if p.returncode != 0:
            raise RuntimeError(f"qsub failed ({p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
        out = p.stdout.strip().splitlines()[-1].strip()
        if self.cfg.scheduler == "sge":
            m = re.search(r"(\d+)", out)  # -terse → "12345" or "12345.1-10:1"
            if not m:
                raise RuntimeError(f"cannot parse job id from: {out!r}")
            return m.group(1)
        return out  # PBS → "12345.server"

    def queue(self) -> list[JobInfo]:
        if self.cfg.scheduler == "mock":
            return []
        p = self._run(["qstat", "-u", self.user])
        return parse_sge_qstat(p.stdout) if self.cfg.scheduler == "sge" else parse_pbs_qstat_table(p.stdout)

    def status(self, job_id: str) -> JobInfo:
        if self.cfg.scheduler == "mock":
            return JobInfo(job_id=job_id, state="completed", exit_status=0)
        if self.cfg.scheduler == "sge":
            for j in self.queue():
                if j.job_id == job_id:
                    return j
            p = self._run(["qacct", "-j", job_id])
            if p.returncode == 0 and "exit_status" in p.stdout:
                return parse_sge_qacct(job_id, p.stdout)
            return JobInfo(job_id=job_id, state="missing")  # accounting can lag; watcher retries
        args = ["qstat", "-fx", job_id] if self.cfg.pbs.pro else ["qstat", "-f", job_id]
        p = self._run(args)
        info = parse_pbs_qstat_full(p.stdout) if p.returncode == 0 else None
        return info or JobInfo(job_id=job_id, state="missing")

    def cancel(self, job_id: str) -> str:
        if self.cfg.scheduler == "mock":
            return "cancelled (mock)"
        p = self._run(["qdel", job_id])
        return (p.stdout or p.stderr).strip()
