"""Issue #120: Slurm backend against fake sbatch/squeue/sacct/scancel. No real cluster is contacted."""

import json
import subprocess
from subprocess import CompletedProcess

import pytest

from labhq.policy import evaluate_tool
from labhq.runner.daemon import Runner
from labhq.settings import HpcSettings, PolicySettings, Settings
from labhq.tools.scheduler import (
    Scheduler, build_script, parse_slurm_sacct, parse_slurm_squeue, slurm_cluster_directive, slurm_job,
)
from tests.fixtures.fake_slurm import INVALID, FakeSlurm

PREFIX = ["sudo", "-n", "-u", "data-account"]


def slurm(**kw) -> tuple[Scheduler, FakeSlurm]:
    backend = Scheduler(HpcSettings(scheduler="slurm", user="fixture", **kw))
    fake = FakeSlurm()
    backend._run = fake
    return backend, fake


def test_submit_args_fill_template_and_partition():
    backend = Scheduler(HpcSettings(scheduler="slurm"))
    args = backend.submit_args("/w/j.sh", "1bad name", 8, "32G", "1-00:00:00", "short", "/w/o%1.out", "/w/e")
    assert args == ["sbatch", "--parsable", "--job-name=j_1bad_name", "--output=/w/o%%1.out", "--error=/w/e",
                    "--nodes=1", "--ntasks=1", "--cpus-per-task=8", "--mem=32G", "--time=24:00:00",
                    "--export=NONE", "--partition=short", "/w/j.sh"]
    assert "-q" not in args  # sbatch -q is a QOS
    small = backend.submit_args("/w/j.sh", "qc", 1, "512M", "30:00", None, "/w/o", "/w/e")
    assert "--mem=512M" in small and "--time=30:00:00" in small
    assert not any(a.startswith("--partition") for a in small)


def test_sbatch_args_are_configurable_and_checked_at_load(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text('hpc:\n  scheduler: slurm\n  slurm:\n    sbatch_args: ["--account=lab", "-c{cores}"]\n',
                      encoding="utf-8")
    backend = Scheduler(Settings.load(str(config)).hpc)
    assert backend.submit_args("/w/j.sh", "a", 4, "4G", "1:00:00", None, "/w/o", "/w/e")[5:7] == ["--account=lab", "-c4"]
    for bad, message in [('["--account={account}"]', "only use"), ('["short"]', "sbatch options"),
                         ('["--clusters=other"]', "another cluster"), ('["-Mother"]', "another cluster"),
                         ('["--clusters", "other"]', "another cluster")]:
        config.write_text(f"hpc:\n  scheduler: slurm\n  slurm:\n    sbatch_args: {bad}\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            Settings.load(str(config))


@pytest.mark.parametrize("final,exit_code,state,exit_status", [
    ("COMPLETED", "0:0", "completed", 0),
    ("FAILED", "3:0", "failed", 3),
    ("OUT_OF_MEMORY", "0:125", "failed", 0),
    ("TIMEOUT", "0:15", "failed", 0),
])
def test_submit_wait_and_finish(final, exit_code, state, exit_status):
    backend, fake = slurm()
    job_id = backend.submit("/w/j.sh", "align", 4, "16G", "02:00:00", stdout="/w/o", stderr="/w/e")
    assert job_id == "4100" and fake.commands() == ["sbatch"]
    queued = backend.status(job_id)
    assert (queued.state, queued.detail, queued.terminal) == ("queued", "Priority", False)
    fake.set(job_id, "PENDING", reason="JobHeldUser")
    assert backend.status(job_id).state == "held"
    fake.set(job_id, "RUNNING")
    assert backend.status(job_id).state == "running"
    assert [(j.job_id, j.state, j.name) for j in backend.queue()] == [(job_id, "running", "align")]

    fake.set(job_id, final, exit_code=exit_code)
    early = backend.status(job_id)  # controller knows, accounting has not caught up
    assert early.state == state and early.terminal and early.exit_status is None
    fake.account(job_id)
    fake.purge(job_id)
    final_info = backend.status(job_id)
    assert (final_info.state, final_info.exit_status) == (state, exit_status)
    assert backend.queue() == []
    assert fake.commands().count("sbatch") == 1  # labhq never resubmits


def test_cancel_uses_scancel_with_prefix_and_reports_cancelled():
    backend, fake = slurm(submit_prefix=PREFIX, job_group="lab-jobs")
    job_id = backend.submit("/w/j.sh", "align")
    assert fake.calls[0][:5] == [*PREFIX, "sbatch"]
    assert backend.cancel(job_id) == ""
    assert fake.calls[-1] == [*PREFIX, "scancel", job_id]
    assert backend.status(job_id).state == "cancelled"
    fake.account(job_id)
    fake.purge(job_id)
    info = backend.status(job_id)
    assert info.state == "cancelled" and "CANCELLED by" in info.detail
    # Status commands run as the runner account: sudoers only needs sbatch and scancel.
    assert all(call[0] in ("squeue", "sacct") for call in fake.calls if "sudo" not in call)
    assert [call[4] for call in fake.calls if call[0] == "sudo"] == ["sbatch", "scancel"]


def test_cancel_failure_names_scancel_and_sudoers():
    backend, fake = slurm(submit_prefix=PREFIX, job_group="lab-jobs")
    fake.fail["scancel"] = (1, "", "sudo: a password is required")
    with pytest.raises(RuntimeError, match=r"scancel failed \(1\).*password is required.*allow scancel in sudoers"):
        backend.cancel("4100")


def test_ssh_host_wraps_every_slurm_command(monkeypatch):
    seen = []

    def run(args, **kw):
        seen.append(args)
        return CompletedProcess(args, 0, "4100\n" if "sbatch" in args[-1] else "", "")

    monkeypatch.setattr(subprocess, "run", run)
    backend = Scheduler(HpcSettings(scheduler="slurm", user="fixture", ssh_host="login1"))
    assert backend.submit("/w/j.sh", "align") == "4100"
    backend.queue()
    assert all(args[:4] == ["ssh", "-o", "BatchMode=yes", "login1"] for args in seen)
    assert seen[0][4].startswith("sbatch --parsable --job-name=align ")
    assert seen[1][4] == "squeue -h -r -u fixture -o '%i|%T|%r|%j'"


@pytest.mark.parametrize("returncode,stdout,stderr,match", [
    (1, "", "sbatch: error: Batch job submission failed: Invalid partition name specified",
     r"sbatch failed \(1\): .*Invalid partition"),
    (0, "Submitted batch job 4100\n", "", r"cannot parse job id.*may have been submitted"),
    (0, "", "", r"no job id.*may have been submitted"),
])
def test_submit_failure_is_reported_once(returncode, stdout, stderr, match):
    backend, fake = slurm()
    fake.fail["sbatch"] = (returncode, stdout, stderr)
    with pytest.raises(RuntimeError, match=match):
        backend.submit("/w/j.sh", "align")
    assert fake.commands() == ["sbatch"]


def test_submit_timeout_warns_that_the_job_may_exist():
    backend = Scheduler(HpcSettings(scheduler="slurm"))
    calls = []

    def hang(args):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 60)

    backend._run = hang
    with pytest.raises(RuntimeError, match=r"sbatch timed out after 60s.*check hpc_queue before resubmitting"):
        backend.submit("/w/j.sh", "align")
    assert len(calls) == 1


@pytest.mark.parametrize("command,stub,match", [
    ("squeue", (255, "", "ssh: connect to host login1: Connection refused"), r"squeue failed \(255\)"),
    ("squeue", (1, "", "slurm_load_jobs error: Unable to contact slurm controller"), r"squeue failed \(1\)"),
    ("squeue", (0, "squeue: warning: unexpected banner\n", ""), "squeue returned unexpected output"),
    ("sacct", (1, "", "sacct: error: Problem talking to the database: Connection refused"), r"sacct failed \(1\)"),
    ("sacct", (0, "Slurm accounting storage is disabled\n", ""), "sacct returned unexpected output"),
])
def test_failed_lookup_is_not_missing(command, stub, match):
    backend, fake = slurm()
    job_id = backend.submit("/w/j.sh", "align")
    fake.purge(job_id)  # squeue says "invalid job id", so status needs sacct
    fake.fail[command] = stub
    with pytest.raises(RuntimeError, match=match):
        backend.status(job_id)


@pytest.mark.parametrize("stub", [(255, "", "ssh: fixture connection refused"), (0, "garbage\n", "")])
def test_failed_squeue_is_not_an_empty_queue(stub):
    backend, fake = slurm()
    fake.fail["squeue"] = stub
    with pytest.raises(RuntimeError, match="squeue"):
        backend.queue()


@pytest.mark.parametrize("squeue", [(1, "", INVALID), (0, "", "")])
def test_job_unknown_to_controller_and_accounting_is_missing(squeue):
    backend, fake = slurm()
    fake.fail["squeue"] = squeue
    info = backend.status("4100")
    assert info.state == "missing" and not info.terminal
    assert fake.commands() == ["squeue", "sacct"]


async def test_runner_hibernates_until_slurm_job_finishes_then_wakes_once(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.hpc.scheduler, s.hpc.user = "slurm", "fixture"
    runner = Runner(s)
    fake = FakeSlurm()
    runner.scheduler._run = fake
    job_id = runner.scheduler.submit("/w/j.sh", "align")
    await runner._on_track({"job_id": job_id, "task_id": "t1", "agent_id": "a", "name": "align"})

    def woke():
        return [e for e in runner.store.pending() if e["type"] == "jobs.finished"]

    await runner._poll_jobs()
    fake.set(job_id, "RUNNING")
    await runner._poll_jobs()
    assert runner.jobs[job_id]["state"] == "running" and not woke()
    fake.set(job_id, "FAILED", exit_code="2:0")
    fake.purge(job_id)
    fake.fail["sacct"] = (1, "", "sacct: error: slurmdbd unreachable")
    with pytest.raises(RuntimeError, match="sacct failed"):
        await runner._poll_jobs()  # an outage is not "the job vanished"
    assert runner.jobs[job_id]["missing"] == 0 and not woke()
    del fake.fail["sacct"]
    fake.account(job_id)
    await runner._poll_jobs()
    await runner._poll_jobs()
    assert (runner.jobs[job_id]["state"], runner.jobs[job_id]["exit_status"]) == ("failed", 2)
    assert len(woke()) == 1
    assert woke()[0]["data"]["jobs"] == [{"job_id": job_id, "name": "align", "state": "failed", "exit_status": 2}]
    states = [e["data"]["state"] for e in runner.store.pending() if e["type"] == "job.state"]
    assert states == ["running", "failed"]
    assert fake.commands().count("sbatch") == 1


async def test_hpc_submit_asks_pi_before_sbatch_and_never_after_denial(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    settings = Settings(hpc=HpcSettings(scheduler="slurm", submit_prefix=PREFIX, job_group="lab-jobs",
                                        user="data-account", default_queue="short"))
    settings.policy.approvals.hpc_core_hours_threshold = 0
    backend = Scheduler(settings.hpc)
    fake = FakeSlurm()
    backend._run = fake
    order, decision = [], {"approved": False, "note": "fixture PI denied"}

    def prepare(workdir, script_path, logs, body, *args):
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(body, encoding="utf-8")

    async def broker(path, payload, timeout):
        order.append(path)
        return decision if path == "/approval" else {}

    monkeypatch.setattr(hpc, "S", settings)
    monkeypatch.setattr(hpc, "WORKDIR", tmp_path)
    monkeypatch.setattr(hpc, "SCHED", backend)
    monkeypatch.setattr(hpc, "_prepare_job_files", prepare)
    monkeypatch.setattr(hpc, "_broker", broker)
    denied = json.loads(await hpc.hpc_submit("#SBATCH --gres=gpu:1\necho ok", "align", cores=2))
    assert denied["submitted"] is False and "Do not resubmit" in denied["note"]
    assert order == ["/approval"] and fake.calls == []

    decision = {"approved": True}
    result = json.loads(await hpc.hpc_submit("#SBATCH --gres=gpu:1\necho ok", "align", cores=2))
    assert result["submitted"] and result["job_id"] == "4100"
    assert order == ["/approval", "/approval", "/jobs/track"]
    (call,) = fake.calls
    assert call[:5] == [*PREFIX, "sbatch"] and "--partition=short" in call and "--cpus-per-task=2" in call
    script = (tmp_path / "jobs").glob("align_*.sh")
    body = next(script).read_text(encoding="utf-8").splitlines()
    assert body[:2] == ["#!/bin/bash", "#SBATCH --gres=gpu:1"]  # directive kept before the first command


def test_directives_after_leading_comments_stay_above_preamble():
    lines = build_script("#!/bin/bash\n# align sample 1\n\n#SBATCH --gres=gpu:1\n#PBS -V\necho hi", "/w").splitlines()
    first_command = lines.index("set -euo pipefail")
    assert lines.index("#SBATCH --gres=gpu:1") < first_command and lines.index("#PBS -V") < first_command
    assert lines[-1] == "echo hi"


@pytest.mark.parametrize("scheduler", ["sge", "pbs", "slurm"])
@pytest.mark.parametrize("job_id", ["--user=data-account", "-u", "123 456", "", "123;rm", "$(id)"])
def test_agent_job_id_is_never_an_option(scheduler, job_id):
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="fixture"))
    calls = []
    backend._run = lambda args: calls.append(args) or CompletedProcess(args, 0, "", "")
    for operation in (backend.cancel, backend.status):
        with pytest.raises(RuntimeError, match="invalid job id"):
            operation(job_id)
    assert calls == []


@pytest.mark.parametrize("job_id", ["4100", "4100_7", "12345.server", "123[].pbs01", "123.1"])
def test_scheduler_job_ids_still_accepted(job_id):
    backend = Scheduler(HpcSettings(scheduler="slurm", user="fixture"))
    backend._run = lambda args: CompletedProcess(args, 0, "", "")
    assert backend.cancel(job_id) == ""


def test_every_queue_id_is_accepted_by_status_and_cancel():
    backend = Scheduler(HpcSettings(scheduler="slurm", user="fixture"))
    calls = []

    def run(args):
        calls.append(args)
        if args[0] == "squeue" and "-u" in args:  # pending array tasks compress unless -r is given
            rows = ("4100_1|PENDING|Resources|align\n4100_8|PENDING|Resources|align\n" if "-r" in args
                    else "4100_[1,8]|PENDING|Resources|align\n")
            return CompletedProcess(args, 0, rows + "4100_0|RUNNING|None|align\n", "")
        if args[0] == "squeue":
            job = args[args.index("-j") + 1]
            return CompletedProcess(args, 0, f"{job}|PENDING|Resources|align\n", "")
        return CompletedProcess(args, 0, "", "")

    backend._run = run
    jobs = backend.queue()
    assert [j.job_id for j in jobs] == ["4100_1", "4100_8", "4100_0"]
    for job in jobs:
        assert backend.status(job.job_id).job_id == job.job_id
        assert backend.cancel(job.job_id) == ""
    assert [c[1] for c in calls if c[0] == "scancel"] == ["4100_1", "4100_8", "4100_0"]


def test_array_and_het_records_roll_up_into_the_submitted_id():
    live = parse_slurm_squeue("4100_[3-9]|PENDING|Resources|align\n4100_2|RUNNING|None|align\n"
                              "41000|RUNNING|None|other\n")
    info = slurm_job("4100", live)
    assert info.state == "running" and info.job_id == "4100" and info.detail == "2 records (2 active)"
    done = parse_slurm_sacct("4100_1|COMPLETED|0:0|align\n4100_2|FAILED|1:0|align\n4100+0|COMPLETED|0:0|align\n")
    final = slurm_job("4100", done)
    assert (final.state, final.exit_status) == ("failed", 1)
    assert slurm_job("410", done) is None  # 4100 is not a task of 410


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("command", ["sbatch run.sh", "srun -c 4 bwa mem ref.fa r.fq", "salloc -N 1",
                                     "scancel 4100", "qsub run.sh", "qrsh", "qlogin", "qdel 12"])
def test_direct_scheduler_job_commands_ask(tool, command):
    policy = PolicySettings()
    assert evaluate_tool(tool, {"command": command}, policy).action == "ask"
    assert evaluate_tool(tool, {"command": "squeue -u me; sacct -j 4100"}, policy).action == "allow"


@pytest.mark.parametrize("scheduler", ["sge", "pbs", "slurm"])
@pytest.mark.parametrize("job_id", ["all", "ALL", "align", "j_qc"])
def test_one_job_id_never_names_many_jobs(scheduler, job_id):
    # Torque `qdel all` and SGE `qdel <job name>` act on every matching job of the (shared) account.
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="fixture"))
    calls = []
    backend._run = lambda args: calls.append(args) or CompletedProcess(args, 0, "", "")
    for operation in (backend.cancel, backend.status):
        with pytest.raises(RuntimeError, match="invalid job id"):
            operation(job_id)
    assert calls == []


def test_submission_to_another_cluster_is_never_tracked_as_a_local_id():
    # SBATCH_CLUSTERS or a directive the pre-check missed: "4100;other" is not job 4100 here.
    backend, fake = slurm()
    fake.cluster = "other"
    with pytest.raises(RuntimeError, match=r"job 4100 to cluster other.*not tracked.*scancel -M other 4100"):
        backend.submit("/w/j.sh", "align")
    assert fake.commands() == ["sbatch"]


@pytest.mark.parametrize("body,option", [
    ("#!/bin/bash\n#SBATCH --clusters=other\necho hi", "--clusters=other"),
    ("#SBATCH --mem=4G -M other\necho hi", "-M"),
    ("# step 1\n#SBATCH -Mother\necho hi", "-Mother"),
    ("#SBATCH --cluster=other\necho hi", "--cluster=other"),
    ("#SBATCH --clusters other\necho hi", "--clusters"),
])
def test_cluster_directive_is_found_before_submission(body, option):
    assert slurm_cluster_directive(body) == option


@pytest.mark.parametrize("body", ["#SBATCH --mem=4G\n#SBATCH --mail-type=END\necho -M", "echo hi",
                                  "#SBATCH --cluster-constraint=fast\necho hi"])
def test_other_directives_are_not_cluster_choices(body):
    assert slurm_cluster_directive(body) is None


def _runner(tmp_path) -> tuple[Runner, FakeSlurm]:
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.hpc.scheduler, s.hpc.user = "slurm", "fixture"
    runner = Runner(s)
    fake = FakeSlurm()
    runner.scheduler._run = fake
    return runner, fake


@pytest.mark.parametrize("controller_holds_it", [True, False])
def test_revoked_federation_sibling_is_a_terminal_failure(controller_holds_it):
    # #186: REVOKED (a finished federation sibling) read as "unknown", so the watcher never woke the agent.
    backend, fake = slurm()
    job_id = backend.submit("/w/j.sh", "align")
    fake.set(job_id, "REVOKED")
    if not controller_holds_it:
        fake.account(job_id)
        fake.purge(job_id)
    info = backend.status(job_id)
    assert (info.state, info.raw_state, info.terminal) == ("failed", "REVOKED", True)


async def test_runner_wakes_once_after_a_revoked_job(tmp_path):
    runner, fake = _runner(tmp_path)
    try:
        job_id = runner.scheduler.submit("/w/j.sh", "align")
        await runner._on_track({"job_id": job_id, "task_id": "t1", "agent_id": "a", "name": "align"})
        fake.set(job_id, "REVOKED")
        fake.account(job_id)
        fake.purge(job_id)
        await runner._poll_jobs()
        woke = [e for e in runner.store.pending() if e["type"] == "jobs.finished"]
        assert len(woke) == 1 and woke[0]["data"]["jobs"][0]["state"] == "failed"
    finally:
        runner.store.close()


def test_unrecognized_state_is_finished_only_after_the_controller_forgets_the_job():
    # Same class as #186: a state missing from SLURM_STATE must not keep a gone job "active" forever.
    backend, fake = slurm()
    job_id = backend.submit("/w/j.sh", "align")
    fake.set(job_id, "FUTURE_STATE")
    live = backend.status(job_id)
    assert (live.state, live.terminal) == ("unknown", False)  # slurmctld still holds it: keep watching
    fake.account(job_id)
    fake.purge(job_id)
    gone = backend.status(job_id)
    assert (gone.state, gone.raw_state, gone.terminal) == ("unknown_finished", "FUTURE_STATE", True)
    assert "FUTURE_STATE" in gone.detail
