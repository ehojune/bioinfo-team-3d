"""Exercise runner and init submission against a fake Slurm controller only."""
import asyncio
import concurrent.futures
import json
import subprocess
import threading
import time
from dataclasses import replace

import pytest

from labhq import hpc_consult, hpc_site
from labhq.hpc_cpu_ledger import SlurmRow, Submission
from labhq.runner.hpc_jobs import submit_job
from labhq.settings import Settings
from labhq.store import StateStore
from labhq.tools.scheduler import Scheduler, build_script


class FakeSlurm:
    def __init__(self):
        self.live = {}
        self.accounting = {}
        self.submits = []
        self.calls = []
        self.lose_response = False
        self.late_accept = False
        self.submit_failure = None
        self.queue_failure = False
        self.enter_sbatch = None
        self.continue_sbatch = None

    def run(self, args, *, cpu_guard=False):
        self.calls.append(args[0])
        if args[0] == "sbatch":
            assert cpu_guard
            self.submits.append(args)
            if self.enter_sbatch is not None:
                self.enter_sbatch.set()
                assert self.continue_sbatch.wait(5)
            if self.submit_failure is not None:
                return subprocess.CompletedProcess(args, *self.submit_failure)
            cpus = next(int(arg.split("=")[1]) for arg in args if arg.startswith("--cpus-per-task="))
            name = next(arg.split("=")[1] for arg in args if arg.startswith("--job-name="))
            job_id = str(1000 + len(self.submits))
            row = SlurmRow(job_id, cpus, int(time.time()), "PENDING", name)
            if not self.late_accept:
                self.live[job_id] = row
            if self.lose_response:
                raise subprocess.TimeoutExpired(args, 1)
            return subprocess.CompletedProcess(args, 0, f"{job_id}\n", "")
        if args[0] == "squeue" and cpu_guard:
            assert {"--me", "--local", "--all", "-r"} <= set(args)
            if self.queue_failure:
                return subprocess.CompletedProcess(args, 1, "", "fixture queue query failed")
            return self.rows(args, self.live.values())
        if args[0] == "sacct" and cpu_guard:
            if "--name" in args:
                names = set(args[args.index("--name") + 1].split(","))
                return self.rows(args, (row for row in self.accounting.values() if row.name in names))
            ids = set(args[args.index("-j") + 1].split(","))
            return self.rows(args, (row for row in self.accounting.values() if row.parent in ids))
        if args[0] == "squeue":  # the init poll after admission
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "sacct":
            return subprocess.CompletedProcess(args, 0, f"{args[args.index('-j') + 1]}|COMPLETED|0:0|trial\n", "")
        pytest.fail(f"unexpected scheduler command: {args[0]}")

    @staticmethod
    def rows(args, rows):
        return subprocess.CompletedProcess(args, 0, "".join(
            f"{row.job_id}|{row.cpus}|{row.submitted_at}|{row.state}|{row.name}\n" for row in rows), "")

    def finish(self, job_id, state="COMPLETED"):
        self.accounting[job_id] = replace(self.live.pop(job_id), state=state)


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    profile = hpc_site.SiteProfile(name="fixture", scheduler="slurm", queue="shared", max_concurrent_cpus=60)
    monkeypatch.setattr(hpc_site, "load_profile", lambda *args: profile)
    fake = FakeSlurm()
    monkeypatch.setattr(Scheduler, "_run", lambda self, *args, **kwargs: fake.run(*args, **kwargs))

    def settings(runner="this-session"):
        return Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state"), "id": runner},
                                        "hpc": {"scheduler": "slurm", "site_profile": profile.name}})

    return fake, settings


def submit(settings, workdir, name, cores, *, task=None, timestamp="one", directive="", submission_id=None):
    jobs = workdir / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    script = jobs / f"{name}-{timestamp}.sh"
    script.write_bytes(build_script(f"{directive}sleep 1", str(workdir)).encode())

    async def approve(_):
        return {"approved": True}

    return asyncio.run(submit_job(settings, Scheduler(settings.hpc), workdir,
                                 {"script": str(script), "name": name, "cores": cores, "walltime": "00:05:00",
                                  "task_id": task or name, "agent_id": "fixture",
                                  "submission_id": submission_id}, approve))


def trial(settings, tmp_path):
    return hpc_consult.trial_job(settings.hpc, tmp_path / "trial", lambda _: True, max_polls=1)


def test_pending_and_running_sum_to_60_excluding_other_sessions(cluster, tmp_path):
    fake, settings = cluster
    fake.live["9"] = SlurmRow("9", 120, int(time.time()), "RUNNING", "unrelated")
    first = submit(settings(), tmp_path, "first", 42)
    fake.live[first["job_id"]] = replace(fake.live[first["job_id"]], state="RUNNING")
    assert submit(settings(), tmp_path, "second", 18)["submitted"]
    assert "60 reserved + 1 requested" in submit(settings(), tmp_path, "third", 1)["error"]
    assert submit(settings("other-session"), tmp_path, "other", 1)["submitted"]
    assert len(fake.submits) == 3
    assert all("--nodes=1" in args and "--partition=shared" in args for args in fake.submits)
    assert all(not any(arg.startswith("--nodelist") for arg in args) for args in fake.submits)


def test_init_shares_limit_and_retains_same_trial_identity_after_restart(cluster, tmp_path):
    fake, settings = cluster
    first = submit(settings(), tmp_path, "full", 60)
    assert "60 reserved + 1 requested" in trial(settings(), tmp_path)["message"]
    fake.finish(first["job_id"])
    allowed = trial(settings(), tmp_path)
    assert allowed["outcome"] == "tracked"
    restarted = trial(settings(), tmp_path)
    assert restarted["job_id"] == allowed["job_id"]
    assert "1 reserved + 60 requested" in submit(settings(), tmp_path, "another", 60)["error"]
    assert len(fake.submits) == 2


def test_parallel_calls_reserve_before_sbatch_returns(cluster, tmp_path):
    fake, settings = cluster
    fake.enter_sbatch = threading.Event()
    fake.continue_sbatch = threading.Event()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        pending = pool.submit(submit, settings(), tmp_path, "pending", 60)
        assert fake.enter_sbatch.wait(4)
        try:
            assert "60 reserved + 1 requested" in trial(settings(), tmp_path)["message"]
            replay = submit(settings(), tmp_path, "pending", 60, timestamp="retry")
            assert "acceptance is unconfirmed" in replay["error"]
        finally:
            fake.continue_sbatch.set()
        assert pending.result()["submitted"]
    assert len(fake.submits) == 1


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("submission_id", [None, "run-1"])
def test_acceptance_response_lost_can_be_recovered_after_restart(cluster, tmp_path, finished, submission_id):
    fake, settings = cluster
    fake.lose_response = True
    response = submit(settings(), tmp_path, "recover", 60, submission_id=submission_id)
    assert "acceptance is unconfirmed" in response["error"]
    if finished:
        fake.finish("1001")
    recovered = submit(settings(), tmp_path, "recover", 60, timestamp="new", submission_id=submission_id)
    assert recovered["job_id"] == "1001"
    assert recovered["replayed"] is True
    assert recovered["script"].endswith("recover-one.sh")
    assert len(fake.submits) == 1


def test_distinct_invocations_submit_identical_jobs_and_reused_id_refuses_changes(cluster, tmp_path):
    fake, settings = cluster
    first = submit(settings(), tmp_path, "replicate", 30, submission_id="run-1")
    second = submit(settings(), tmp_path, "replicate", 30, timestamp="two", submission_id="run-2")
    assert first["job_id"] != second["job_id"]
    assert "60 reserved + 30 requested" in submit(
        settings(), tmp_path, "replicate", 30, timestamp="three", submission_id="run-3")["error"]
    fake.finish(first["job_id"])
    assert "different job payload" in submit(
        settings(), tmp_path, "replicate", 29, timestamp="changed", submission_id="run-1")["error"]
    replay = submit(settings(), tmp_path, "replicate", 30, timestamp="replay", submission_id="run-1")
    assert replay["job_id"] == first["job_id"]
    assert replay["replayed"] is True
    assert replay["script"] == first["script"]
    assert len(fake.submits) == 2


@pytest.mark.parametrize("state", ["REVOKED", "LAUNCH_FAILED", "RECONFIG_FAIL"])
def test_terminal_accounting_releases_capacity(cluster, tmp_path, state):
    fake, settings = cluster
    first = submit(settings(), tmp_path, "first", 60)
    fake.finish(first["job_id"], state)
    assert submit(settings(), tmp_path, "new", 60)["submitted"]
    assert len(fake.submits) == 2


@pytest.mark.parametrize("state", ["LAUNCH_FAILED", "RECONFIG_FAIL"])
def test_live_failure_flag_still_counts_toward_capacity(cluster, tmp_path, state):
    fake, settings = cluster
    first = submit(settings(), tmp_path, "first", 60)
    job_id = first["job_id"]
    fake.live[job_id] = replace(fake.live[job_id], state=state)
    assert "60 reserved + 1 requested" in submit(settings(), tmp_path, "next", 1)["error"]
    assert len(fake.submits) == 1


@pytest.mark.parametrize("reason", ["Invalid partition name specified", "Invalid qos specification"])
def test_confirmed_sbatch_rejection_releases_capacity(cluster, tmp_path, reason):
    fake, settings = cluster
    fake.submit_failure = (1, "", f"sbatch: error: Batch job submission failed: {reason}\n")
    first = submit(settings(), tmp_path, "first", 60, submission_id="first")
    assert reason in first["error"]
    assert "reservation is held" not in first["error"]
    fake.submit_failure = None
    assert submit(settings(), tmp_path, "first", 60, timestamp="retry", submission_id="first")["submitted"]
    assert len(fake.submits) == 2


@pytest.mark.parametrize("failure", [
    (1, "", "sbatch: error: Batch job submission failed: Socket timed out on send/recv operation\n"),
    (255, "", "ssh: connection closed\n"),
    (1, "1001\n", "sbatch: error: Batch job submission failed: Invalid qos specification\n"),
])
def test_uncertain_nonzero_exit_holds_capacity(cluster, tmp_path, failure):
    fake, settings = cluster
    fake.submit_failure = failure
    first = submit(settings(), tmp_path, "first", 60)
    assert "reservation is held" in first["error"]
    assert "was not resent" in submit(settings(), tmp_path, "first", 60, timestamp="retry")["error"]
    assert "60 reserved + 1 requested" in submit(settings(), tmp_path, "next", 1)["error"]
    assert len(fake.submits) == 1


async def test_mcp_passes_invocation_identity_and_returns_original_receipt(cluster, tmp_path, monkeypatch):
    from labhq.tools import hpc_mcp

    fake, settings = cluster
    config = settings()
    monkeypatch.setattr(hpc_mcp, "S", config)
    monkeypatch.setattr(hpc_mcp, "SCHED", Scheduler(config.hpc))
    monkeypatch.setattr(hpc_mcp, "SESSION_ERROR", None)
    monkeypatch.setattr(hpc_mcp, "WORKDIR", tmp_path)
    monkeypatch.setattr(hpc_mcp, "TASK", "task")
    monkeypatch.setattr(hpc_mcp, "AGENT", "fixture")
    monkeypatch.delenv("LABHQ_EXECUTION_MODE", raising=False)

    async def approve(_):
        return {"approved": True}

    async def broker(path, payload, timeout):
        assert path == "/jobs/submit"
        return await submit_job(config, Scheduler(config.hpc), tmp_path, payload, approve)

    monkeypatch.setattr(hpc_mcp, "_broker", broker)
    first = json.loads(await hpc_mcp.hpc_submit("sleep 1", "sample", submission_id="first"))
    second = json.loads(await hpc_mcp.hpc_submit("sleep 1", "sample", submission_id="second"))
    replay = json.loads(await hpc_mcp.hpc_submit("sleep 1", "sample", submission_id="first"))
    assert first["job_id"] != second["job_id"]
    assert first["script"] != second["script"]
    assert replay["job_id"] == first["job_id"]
    assert replay["script"] == first["script"]
    assert replay["replayed"] is True
    assert len(fake.submits) == 2


def test_absent_job_and_failed_query_hold_the_reservation(cluster, tmp_path):
    fake, settings = cluster
    fake.lose_response = fake.late_accept = True
    assert "reservation is held" in submit(settings(), tmp_path, "uncertain", 60)["error"]
    assert "was not resent" in submit(settings(), tmp_path, "uncertain", 60, timestamp="retry")["error"]
    assert "60 reserved + 1 requested" in trial(settings(), tmp_path)["message"]
    fake.queue_failure = True
    assert "query failed" in submit(settings(), tmp_path, "blocked", 1)["error"]
    assert len(fake.submits) == 1


def test_same_runner_keeps_different_site_ledgers_separate(cluster, tmp_path, monkeypatch):
    fake, settings = cluster
    assert submit(settings(), tmp_path, "site-one", 60)["submitted"]
    other = settings()
    other.hpc.site_profile = "other"
    monkeypatch.setattr(hpc_site, "load_profile", lambda name, *_: hpc_site.SiteProfile(
        name=name, scheduler="slurm", max_concurrent_cpus=60))
    assert submit(other, tmp_path, "site-two", 60)["submitted"]
    assert len(fake.submits) == 2


def test_legacy_runner_rows_count_expanded_array_and_ignore_other_site_and_untrusted(cluster, tmp_path):
    fake, settings = cluster
    now = int(time.time())
    state = StateStore(settings().hpc._runner_state_path)
    base = {"scheduler": "slurm", "name": "legacy", "submitted_at": now, "terminal": False}
    try:
        state.put("job", "7", {**base, "job_id": "7", "submitted_by_runner": True})
        state.put("job", "8", {**base, "job_id": "8", "submitted_by_runner": True, "site_profile": "other"})
        state.put("job", "9", {**base, "job_id": "9"})
    finally:
        state.close()
    for jid, cpus, status in [("7_1", 30, "PENDING"), ("7_2", 29, "RUNNING"), ("8", 120, "PENDING"),
                              ("9", 120, "RUNNING")]:
        fake.live[jid] = SlurmRow(jid, cpus, now, status, "second-component" if jid == "7_2" else "legacy")
    assert "59 reserved + 2 requested" in submit(settings(), tmp_path, "too-large", 2)["error"]
    assert trial(settings(), tmp_path)["outcome"] == "tracked"
    fake.live.pop("7_1")
    fake.live["7_2"] = replace(fake.live["7_2"], submitted_at=now + 1200)
    assert "previously submitted" in submit(settings(), tmp_path, "new", 1)["error"]
    assert len(fake.submits) == 1


@pytest.mark.parametrize("option", ["--exclusive", "--array=1-2", "--cpus-per-gpu=10", "--nodelist=n1", "--ntasks=2"])
def test_custom_sbatch_resource_shapes_are_refused(cluster, tmp_path, option):
    fake, settings = cluster
    config = settings()
    config.hpc.slurm.sbatch_args.append(option)
    assert "single-node/single-task" in submit(config, tmp_path, "unsafe", 1)["error"]
    assert not fake.calls


@pytest.mark.parametrize("directive", ["#SBATCH --array=1-2\n", "#PBS -l ncpus=99\n", "#SBATCH hetjob\n"])
def test_script_resource_options_are_refused(cluster, tmp_path, directive):
    fake, settings = cluster
    assert "without scheduler directives" in submit(settings(), tmp_path, "directives", 1, directive=directive)["error"]
    assert not fake.calls


def test_environment_refusal_never_starts_sbatch_and_allows_same_key_when_fixed(tmp_path, monkeypatch):
    profile = hpc_site.SiteProfile(name="fixture", scheduler="slurm", max_concurrent_cpus=60)
    monkeypatch.setattr(hpc_site, "load_profile", lambda *args: profile)
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                        "hpc": {"scheduler": "slurm", "site_profile": profile.name}})
    scheduler = Scheduler(settings.hpc)
    invoked = []

    def run(args, *, env, **kwargs):
        invoked.append(args)
        assert env["SLURM_TIME_FORMAT"] == "%s"
        return subprocess.CompletedProcess(args, 0, "1001\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("SBATCH_ARRAY_INX", "1-10")
    submission = Submission("same-key", "#!/bin/bash\nsleep 1\n", {})
    with pytest.raises(RuntimeError, match="refuses scheduler option variables"):
        scheduler.submit(str(tmp_path / "run.sh"), "fixture", 60, cpu_submission=submission)
    assert not invoked
    monkeypatch.delenv("SBATCH_ARRAY_INX")
    assert scheduler.submit(str(tmp_path / "run.sh"), "fixture", 60, cpu_submission=submission) == "1001"
    assert len(invoked) == 1
