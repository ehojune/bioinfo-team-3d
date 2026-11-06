"""SGE / PBS(Torque, Pro) / Slurm backend: submit, status, queue, cancel.

Parsers are pure functions so they can be unit-tested against real qstat/qacct/squeue/sacct output.
Cluster specifics (PE name, memory resource, PBS resource syntax, sbatch options) live in HpcSettings.
"""

from __future__ import annotations

import getpass
import os
import re
import shlex
import subprocess
import threading
from dataclasses import asdict, dataclass

from ..settings import HpcSettings, slurm_cluster_option

TERMINAL = {"completed", "failed", "cancelled", "unknown_finished"}
MISSING_POLLS_BEFORE_FINISHED = 3

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
MAYBE_SUBMITTED = "; the job may have been submitted, check hpc_queue before resubmitting"
# Script lines a scheduler reads as options. sbatch also reads #PBS and #BSUB unless --ignore-pbs is given.
DIRECTIVE_PREFIXES = ("#SBATCH", "#PBS", "#BSUB", "#$")
# The only variables scheduler commands receive (#172). A job submitted with -V or -v (SGE, PBS) copies qsub's
# environment, and the MCP process holding qsub also holds the broker token and engine credentials.
COMMAND_ENV = re.compile(
    r"PATH|HOME|USER|LOGNAME|USERNAME|SHELL|LANG|LANGUAGE|TZ|TMPDIR|TEMP|TMP|TERM|LD_LIBRARY_PATH|KRB5CCNAME"
    r"|SSH_AUTH_SOCK|SYSTEMROOT|SYSTEMDRIVE|WINDIR|COMSPEC|PATHEXT|USERPROFILE|HOMEDRIVE|HOMEPATH|APPDATA"
    r"|LOCALAPPDATA|PROGRAMDATA|(?:LC|SGE|PBS|SLURM|SBATCH|SQUEUE|SACCT|SCANCEL)_\w+"
)
ACCOUNTING_DISABLED = re.compile(r"accounting storage is disabled", re.I)


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


def job_in_family(tracked: str, job_id: str) -> bool:
    """`job_id` is the tracked job or one of its array tasks, het components or steps."""
    if job_id == tracked:
        return True
    if "[]" in tracked:  # PBS array "123[].server" → element "123[4].server"
        head, tail = tracked.split("[]", 1)
        return bool(re.fullmatch(re.escape(head) + r"\[\d+\]" + re.escape(tail), job_id))
    return job_id.startswith((f"{tracked}_", f"{tracked}+", f"{tracked}."))


def command_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    return {key: value for key, value in environ.items() if COMMAND_ENV.fullmatch(key.upper())}


def script_directives(body: str, prefixes: tuple[str, ...] | None = None) -> list[str]:
    """Scheduler directive lines anywhere in an agent script; the core-hour estimate cannot see what they ask."""
    if prefixes is None:
        extra = os.environ.get("PBS_DPREFIX")  # PBS takes its directive prefix from the submitting environment
        prefixes = (*DIRECTIVE_PREFIXES, extra) if extra else DIRECTIVE_PREFIXES
    return [line.strip() for line in body.splitlines() if line.lstrip().startswith(prefixes)]


def slurm_cluster_directive(body: str) -> str | None:
    """First option in an #SBATCH line that picks another cluster, or None."""
    for line in script_directives(body, ("#SBATCH",)):
        try:
            tokens = shlex.split(line[len("#SBATCH"):], comments=True, posix=True)
        except ValueError:
            return "<malformed directive>"  # fail closed before sbatch interprets it differently
        if option := slurm_cluster_option(tokens):
            return option
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


STDERR_MARK = "__LABHQ_STDERR__"


def _split_streams(command: str) -> str:
    """One shell line that keeps stderr apart from stdout. The shell session has a single output stream, so the command's
    stderr goes to a temporary file and comes back after a marker line; a warning printed after the job id of a submit can
    then never be read as the id. The exit status is the command's own."""
    return (f"E=$(mktemp); {command} 2>\"$E\"; S=$?; printf '\\n{STDERR_MARK}\\n'; cat \"$E\"; rm -f \"$E\"; (exit $S)")


def _detail(p: subprocess.CompletedProcess) -> str:
    """What the command said, for an error message. ``_run`` hides the site's host, port and user in it (#1295): the
    message goes to the staff agent, and a login host name is the institution's, not the agent's to know."""
    text = (p.stderr or "").strip() or (p.stdout or "").strip()
    hide = getattr(p, "redact", None)
    return hide(text) if hide else text


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

def _windows_logon_user() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        size = ctypes.c_uint32(257)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)) else ""
    except Exception:
        return ""


def _local_user() -> str:
    """Logon account name, or "" when it cannot be resolved (never a made-up name: it would filter qstat/squeue)."""
    try:
        return getpass.getuser()
    except Exception:  # getpass falls back to POSIX-only `pwd` when USERNAME/USER/LOGNAME are unset on Windows
        return _windows_logon_user()


def next_poll_interval(current: float, base: float, cap: float, factor: float, reset: bool) -> float:
    """The wait before the next job poll (#1295). Every poll over ssh is a login, so a poll that found nothing new makes
    the next one later, by ``factor`` up to ``cap``; any change (a new job, a state change, a job whose accounting is
    still catching up, a failed poll) goes back to ``base``. The cap never goes below ``base``."""
    if reset:
        return float(base)
    return float(min(max(cap, base), max(base, current * factor)))


_SESSIONS: dict[tuple[str, str], object] = {}  # one shell per site profile in this process (#1295 C)
_SESSIONS_LOCK = threading.Lock()


def close_sessions() -> None:
    """Close every session this process opened (shutdown, and tests)."""
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        session.close()


def site_session(cfg: HpcSettings):
    """The shared ``HpcSession`` of the config's site profile, or None when the profile gives no connect command.

    Every Scheduler of one profile in this process shares the session, so the runner's polling and its tools' commands go
    over one login. Opening is lazy: nothing connects until a command runs."""
    from .. import hpc_site
    from ..hpc_login_budget import budget_for
    from ..hpc_session import HpcSession

    try:
        profile = hpc_site.session_profile(cfg.site_profile)
    except hpc_site.SiteProfileError as error:
        raise RuntimeError(f"hpc.site_profile {cfg.site_profile!r} cannot be used: {error}") from error
    if profile is None:
        return None
    key = (profile.name, str(hpc_site.sites_dir()))
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = _SESSIONS[key] = HpcSession(hpc_site.session_config(profile, cfg.command_timeout_s),
                                                  budget=budget_for(cfg))
    return session


class Scheduler:
    def __init__(self, cfg: HpcSettings):
        self.cfg = cfg
        self.user = cfg.user or _local_user()

    def session(self):
        """This config's shared shell, or None (plain ssh_host, or commands run on this machine). The profile is read
        once per Scheduler; an unreadable one raises each time until it is fixed."""
        if not hasattr(self, "_session"):
            self._session = site_session(self.cfg)
        return self._session

    def attach_session(self, *, on_notice=None, has_open_jobs=None) -> bool:
        """Hook the runner into the shell: ``on_notice(kind, text)`` hears about login limits, a changed host key, a
        missing tunnel and failed reconnects; ``has_open_jobs()`` keeps the shell open while jobs run. Starts the
        keep-alive. False when there is no session."""
        session = self.session()
        if session is None:
            return False
        session.on_notice, session.has_open_jobs = on_notice, has_open_jobs
        session.start()
        return True

    def needs_login(self) -> bool:
        """Would the next scheduler command open a new login? Over a live shell it would not; over ``hpc.ssh_host`` it
        always does; on the login node itself there is no login."""
        try:
            session = self.session()
        except RuntimeError:
            return True
        if session is not None:
            return not session.connected
        return bool(self.cfg.ssh_host)

    def _require_enabled(self, operation: str) -> None:
        if self.cfg.scheduler == "none":
            raise RuntimeError(f"{operation} not supported for scheduler=none (HPC disabled)")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        self._require_enabled("command execution")
        session = self.session()
        if session is not None:  # one shell for everything: a command is a line sent to it, not a login (#1295 C)
            from ..hpc_session import CommandTimeout

            try:
                result = session.run(_split_streams(shlex.join(args)), self.cfg.command_timeout_s)
            except CommandTimeout as error:
                raise subprocess.TimeoutExpired(args, self.cfg.command_timeout_s) from error
            if result.truncated:  # the command ran: a submit may have gone through, so this is "lost", not a plain failure
                from ..hpc_session import SessionLost

                raise SessionLost(f"{args[0]} printed more than the session keeps; the answer is incomplete")
            out, _, err = result.output.rpartition(f"\n{STDERR_MARK}\n")
            if not _:  # no marker: the shell did not run our wrapper; everything is output
                out, err = result.output, ""
            done = subprocess.CompletedProcess(args, result.exit_code, out, err)
            done.redact = self._redactor(session)
            return done
        if self.cfg.ssh_host:
            # Every ssh is a login the site records: count it, and refuse it when a limit the user set is used up (#1295).
            from ..hpc_login_budget import budget_for

            budget = budget_for(self.cfg)
            if budget is not None:
                budget.admit(args[0])
            args = ["ssh", "-o", "BatchMode=yes", self.cfg.ssh_host, shlex.join(args)]
        done = subprocess.run(args, capture_output=True, text=True, timeout=self.cfg.command_timeout_s,
                              env=command_env())
        if self.cfg.ssh_host:
            done.redact = self._redactor(None)  # ssh's own error text names the host and the user
        return done

    def _redactor(self, session):
        """A function hiding this site's host, port and user names in a message: the profile's connect command, or
        ``hpc.ssh_host``."""
        from ..hpc_session import redact, redaction_words

        words = (session.cfg.redact_words if session is not None
                 else redaction_words(["ssh", *shlex.split(self.cfg.ssh_host or "")]))
        return lambda text: redact(text, words or ())

    def submit_args(self, script: str, name: str, cores: int, mem: str, walltime: str,
                    queue: str | None, stdout: str, stderr: str) -> list[str]:
        self._require_enabled("submit")
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
        except Exception as e:
            from ..hpc_session import SessionLost

            if isinstance(e, SessionLost):  # the shell dropped while qsub/sbatch ran: it may have gone through
                raise RuntimeError(f"{e}{MAYBE_SUBMITTED}") from e
            raise
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
        self._require_enabled("queue")
        if self.cfg.scheduler == "mock":
            return []
        if not self.user:
            raise RuntimeError("cannot determine the OS user for the queue query; set hpc.user")
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
        self._require_enabled("status")
        if self.cfg.scheduler == "mock":  # ids are "mock-…" and nothing is run
            return JobInfo(job_id=job_id, state="completed", exit_status=0)
        checked_job_id(job_id)
        if self.cfg.scheduler == "slurm":
            return self._slurm_status(job_id)
        if self.cfg.scheduler == "sge":
            for j in self.queue():
                if j.job_id == job_id:
                    return j
            return self._sge_accounting(job_id)
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

    def _sge_accounting(self, job_id: str) -> JobInfo:
        """A job that left the SGE queue: qacct for its final state."""
        p = self._run(["qacct", "-j", job_id])
        if p.returncode != 0:
            detail = _detail(p)
            if re.fullmatch(rf"error: job id {re.escape(job_id)} not found", detail, re.I):
                return JobInfo(job_id=job_id, state="missing")  # accounting can lag; watcher retries
            raise RuntimeError(f"qacct failed ({p.returncode}): {detail}")
        if "exit_status" not in p.stdout:
            raise RuntimeError("qacct returned no exit_status; job state is unknown")
        return parse_sge_qacct(job_id, p.stdout)

    def statuses(self, job_ids: list[str]) -> dict[str, "JobInfo | Exception"]:
        """The state of several jobs with as few scheduler commands (so ssh logins) as possible (#1295).

        Each id maps to its JobInfo, or to the exception ``status`` would have raised for it. The answer is the same
        as asking one job at a time; only the number of commands differs:

        - SGE: one ``qstat -u`` for every job still queued or running, then ``qacct`` only for a job that left it.
        - Slurm: one ``squeue -j a,b,c``, then one ``sacct -j …`` for the jobs squeue no longer holds.
        - PBS: one ``qstat -f a b c``.

        Anything the batch cannot answer cleanly (a command that fails, a job it does not list) falls back to the
        one-job path for that job, so an odd scheduler answer costs extra commands, never a wrong state."""
        ids = list(dict.fromkeys(job_ids))
        out: dict[str, JobInfo | Exception] = {}
        if self.cfg.scheduler == "mock" or len(ids) < 2:
            return self._one_by_one(ids)
        self._require_enabled("status")
        valid = []
        for job_id in ids:
            try:
                valid.append(checked_job_id(job_id))
            except Exception as exc:  # noqa: BLE001 - reported per job, like status()
                out[job_id] = exc
        try:
            if self.cfg.scheduler == "sge":
                out.update(self._sge_statuses(valid))
            elif self.cfg.scheduler == "slurm":
                out.update(self._slurm_statuses(valid))
            else:
                out.update(self._pbs_statuses(valid))
        except Exception as exc:  # noqa: BLE001 - the batch itself failed: every job reports it, as one status would
            for job_id in valid:
                out.setdefault(job_id, exc)
        return {job_id: out[job_id] for job_id in ids}

    def _one_by_one(self, ids: list[str]) -> dict[str, "JobInfo | Exception"]:
        out: dict[str, JobInfo | Exception] = {}
        for job_id in ids:
            try:
                out[job_id] = self.status(job_id)
            except Exception as exc:  # noqa: BLE001
                out[job_id] = exc
        return out

    def _sge_statuses(self, ids: list[str]) -> dict[str, "JobInfo | Exception"]:
        queued = {j.job_id: j for j in self.queue()}
        out: dict[str, JobInfo | Exception] = {}
        for job_id in ids:
            if job_id in queued:
                out[job_id] = queued[job_id]
                continue
            try:
                out[job_id] = self._sge_accounting(job_id)
            except Exception as exc:  # noqa: BLE001
                out[job_id] = exc
        return out

    def _pbs_statuses(self, ids: list[str]) -> dict[str, "JobInfo | Exception"]:
        flags = ["-fx"] if self.cfg.pbs.pro else ["-f"]
        p = self._run(["qstat", *flags, *ids])
        found: dict[str, JobInfo] = {}
        if p.returncode == 0:
            for block in re.split(r"(?m)^(?=Job Id:)", p.stdout):
                info = parse_pbs_qstat_full(block) if block.strip() else None
                if info is not None:
                    found[info.job_id.split(".")[0]] = info
        out: dict[str, JobInfo | Exception] = {}
        for job_id in ids:
            info = found.get(job_id.split(".")[0])
            if info is not None:
                out[job_id] = info
                continue
            try:
                out[job_id] = self.status(job_id)  # not listed or the batch failed: ask for this job alone
            except Exception as exc:  # noqa: BLE001
                out[job_id] = exc
        return out

    def _slurm_statuses(self, ids: list[str]) -> dict[str, "JobInfo | Exception"]:
        p = self._run(["squeue", "-h", "-r", "-t", "all", "-j", ",".join(ids), "-o", SQUEUE_FORMAT])
        if p.returncode != 0 or (p.stdout.strip() and not parse_slurm_squeue(p.stdout)):
            return self._one_by_one(ids)  # a purged id can fail the whole call: ask one job at a time
        records = parse_slurm_squeue(p.stdout)
        out: dict[str, JobInfo | Exception] = {}
        live: dict[str, JobInfo | None] = {}
        ended = []
        for job_id in ids:
            live[job_id] = slurm_job(job_id, records)
            if live[job_id] and not live[job_id].terminal:
                out[job_id] = live[job_id]
            else:
                ended.append(job_id)
        if ended:
            accounting = self._run(["sacct", "-n", "-P", "-X", "-j", ",".join(ended), "-o", SACCT_FIELDS])
            batch_failed = accounting.returncode != 0 and not (
                ACCOUNTING_DISABLED.search(accounting.stderr or "") or ACCOUNTING_DISABLED.search(accounting.stdout or ""))
            for job_id in ended:
                try:
                    # One id a site rejects can fail the whole sacct: ask for that job alone, as before the batch.
                    out[job_id] = (self._slurm_status(job_id) if batch_failed
                                   else self._slurm_final(job_id, live[job_id], accounting))
                except Exception as exc:  # noqa: BLE001
                    out[job_id] = exc
        return out

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
        return self._slurm_final(job_id, live, self._run(["sacct", "-n", "-P", "-X", "-j", job_id, "-o", SACCT_FIELDS]))

    def _slurm_final(self, job_id: str, live: "JobInfo | None", p: subprocess.CompletedProcess) -> JobInfo:
        """One job's final state from a sacct answer (for this job alone or for several jobs at once) and what squeue
        said about it."""
        if ACCOUNTING_DISABLED.search(p.stderr or "") or (not _slurm_rows(p.stdout or "")
                                                           and ACCOUNTING_DISABLED.search(p.stdout or "")):
            # No slurmdbd: squeue's final state is all there is. Once it is gone too, "missing" lets the
            # watcher retry a few polls and then wake the agent with unknown_finished (#172).
            return live or JobInfo(job_id=job_id, state="missing",
                                   detail="squeue no longer lists the job and Slurm accounting storage is "
                                          "disabled; its final state is unknown")
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
        accounted = slurm_job(job_id, records)
        if live is None and accounted and not accounted.terminal:
            return JobInfo(job_id=job_id, state="missing",
                           detail="squeue no longer lists the job but Slurm accounting still reports it active")
        # Accounting can lag behind the controller: keep squeue's final state, else let the watcher retry.
        return accounted or live or JobInfo(job_id=job_id, state="missing")

    def cancel(self, job_id: str) -> str:
        self._require_enabled("cancel")
        if self.cfg.scheduler == "mock":
            return "cancelled (mock)"
        checked_job_id(job_id)
        cmd = "scancel" if self.cfg.scheduler == "slurm" else "qdel"
        p = self._run([*self.cfg.submit_prefix, cmd, job_id])
        if p.returncode != 0:
            detail = _detail(p)
            hint = f"; allow {cmd} in sudoers for hpc.submit_prefix" if self.cfg.submit_prefix else ""
            raise RuntimeError(f"{cmd} failed ({p.returncode}): {detail}{hint}")
        text = (p.stdout or p.stderr).strip()
        hide = getattr(p, "redact", None)
        return hide(text) if hide else text
