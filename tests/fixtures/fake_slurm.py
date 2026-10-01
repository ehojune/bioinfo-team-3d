"""Fake sbatch/squeue/sacct/scancel: argv in, CompletedProcess out. Nothing reaches a real cluster.

Assign an instance to ``Scheduler._run``. Jobs move only when a test calls ``set``/``purge``/``account``,
so each scheduler call sees exactly the controller and accounting state the test arranged.
"""

from __future__ import annotations

from subprocess import CompletedProcess

COMMANDS = ("sbatch", "squeue", "sacct", "scancel")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL"}
INVALID = "slurm_load_jobs error: Invalid job id specified"


class FakeSlurm:
    def __init__(self, cluster: str | None = None):
        self.cluster = cluster  # sbatch --parsable prints "id;cluster" on multi-cluster sites
        self.calls: list[list[str]] = []
        self.jobs: dict[str, dict] = {}
        self.next_id = 4100
        self.fail: dict[str, tuple[int, str, str]] = {}  # command → (returncode, stdout, stderr)

    # ----- what the test controls -----
    def set(self, job_id: str, state: str, *, reason: str = "None", exit_code: str = "0:0") -> None:
        self.jobs[job_id].update(state=state, reason=reason, exit_code=exit_code)

    def purge(self, job_id: str) -> None:
        """slurmctld forgets the job (MinJobAge); only accounting still knows it."""
        self.jobs[job_id]["live"] = False

    def account(self, job_id: str) -> None:
        """slurmdbd catches up."""
        self.jobs[job_id]["accounted"] = True

    def commands(self) -> list[str]:
        return [next(a for a in call if a in COMMANDS) for call in self.calls]

    # ----- what the scheduler sees -----
    def __call__(self, argv: list[str]) -> CompletedProcess:
        self.calls.append(list(argv))
        at = next(i for i, a in enumerate(argv) if a in COMMANDS)  # after any submit_prefix
        cmd, args = argv[at], argv[at + 1:]
        if cmd in self.fail:
            return CompletedProcess(argv, *self.fail[cmd])
        out, err, rc = getattr(self, "_" + cmd)(args)
        return CompletedProcess(argv, rc, out, err)

    @staticmethod
    def _opt(args: list[str], flag: str) -> str:
        return args[args.index(flag) + 1]

    def _sbatch(self, args):
        assert "--parsable" in args
        name = next(a.split("=", 1)[1] for a in args if a.startswith("--job-name="))
        job_id = str(self.next_id)
        self.next_id += 1
        self.jobs[job_id] = {"name": name, "state": "PENDING", "reason": "Priority", "exit_code": "0:0",
                             "live": True, "accounted": False, "argv": args}
        return (f"{job_id};{self.cluster}" if self.cluster else job_id) + "\n", "", 0

    def _squeue(self, args):
        assert self._opt(args, "-o") == "%i|%T|%r|%j" and {"-h", "-r"} <= set(args)
        if "-j" in args:
            job = self.jobs.get(self._opt(args, "-j"))
            if not job or not job["live"]:
                return "", INVALID, 1
            ids = [self._opt(args, "-j")]
        else:  # -u: the default state filter hides finished jobs
            ids = [i for i, j in self.jobs.items() if j["live"] and j["state"] not in TERMINAL]
        rows = [f"{i}|{self.jobs[i]['state']}|{self.jobs[i]['reason']}|{self.jobs[i]['name']}" for i in ids]
        return "".join(row + "\n" for row in rows), "", 0

    def _sacct(self, args):
        assert {"-n", "-P", "-X"} <= set(args) and self._opt(args, "-o") == "JobID,State,ExitCode,JobName"
        job_id = self._opt(args, "-j")
        job = self.jobs.get(job_id)
        if not job or not job["accounted"]:
            return "", "", 0
        state = "CANCELLED by 1000" if job["state"] == "CANCELLED" else job["state"]
        return f"{job_id}|{state}|{job['exit_code']}|{job['name']}\n", "", 0

    def _scancel(self, args):
        (job_id,) = args
        job = self.jobs.get(job_id)
        if not job or not job["live"]:
            return "", f"scancel: error: Kill job error on job id {job_id}: Invalid job id specified", 1
        job.update(state="CANCELLED", exit_code="0:15")
        return "", "", 0
