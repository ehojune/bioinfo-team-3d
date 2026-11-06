"""#1295: open HPC jobs are polled with one batched scheduler call, and the poll interval backs off over ssh.

Mock scheduler output only: a scripted `_run`, no real cluster, no ssh.
"""

import asyncio
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from labhq.settings import HpcSettings, Settings
from labhq.tools.scheduler import JobInfo, Scheduler, next_poll_interval


def done(argv, stdout="", stderr="", rc=0):
    return CompletedProcess(argv, rc, stdout, stderr)


def fake(scheduler, replies):
    """A Scheduler whose `_run` answers from {first-argv-word: callable(argv) -> CompletedProcess}; counts every call."""
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="lab"))
    calls = []

    def run(args):
        calls.append(list(args))
        return replies[args[0]](args)

    backend._run = run
    return backend, calls


def as_state(results):
    return {k: (v.state, v.exit_status) if isinstance(v, JobInfo) else repr(v) for k, v in results.items()}


# ---- SGE ----------------------------------------------------------------------------------------------------------

QSTAT = ("job-ID prior name user state submit/start at queue slots ja-task-ID\n"
         "-----------------------------------------------------------------\n"
         "101 0.5 a lab r 11/06/2026 10:00:00 all.q@n1 1\n"
         "102 0.5 b lab qw 11/06/2026 10:00:00 1\n")
QACCT = "jobnumber 103\nexit_status 0\nfailed 0\n"


def sge(per_job=False):
    def qacct(argv):
        return done(argv, QACCT) if argv[2] == "103" else done(argv, stderr=f"error: job id {argv[2]} not found", rc=1)
    return fake("sge", {"qstat": lambda argv: done(argv, QSTAT), "qacct": qacct})


def test_sge_asks_the_queue_once_for_every_job_and_qacct_only_for_one_that_left_it():
    backend, calls = sge()
    results = backend.statuses(["101", "102", "103", "104"])
    assert [c[0] for c in calls] == ["qstat", "qacct", "qacct"]
    assert as_state(results) == {"101": ("running", None), "102": ("queued", None), "103": ("completed", 0),
                                 "104": ("missing", None)}


def test_a_batch_gives_the_same_answers_as_asking_one_job_at_a_time():
    batch, batch_calls = sge()
    single, single_calls = sge()
    ids = ["101", "102", "103", "104"]
    alone = {}
    for job_id in ids:
        alone[job_id] = single.status(job_id)
    assert as_state(batch.statuses(ids)) == as_state(alone)
    assert len(batch_calls) < len(single_calls)  # 3 against 1 + 1 + 2 + 2


def test_three_queued_jobs_cost_one_command_not_three():
    backend, calls = sge()
    backend.statuses(["101", "102"])
    assert [c[0] for c in calls] == ["qstat"]


def test_a_failing_batch_reports_the_error_for_every_job():
    backend, _ = fake("sge", {"qstat": lambda argv: done(argv, stderr="denied", rc=1)})
    results = backend.statuses(["101", "102"])
    assert all(isinstance(v, RuntimeError) and "qstat failed" in str(v) for v in results.values())


def test_an_invalid_id_is_reported_alone_and_the_rest_are_still_answered():
    backend, _ = sge()
    results = backend.statuses(["101", "102", "1; rm -rf"])
    assert isinstance(results["1; rm -rf"], RuntimeError) and "invalid job id" in str(results["1; rm -rf"])
    assert results["101"].state == "running"


def test_one_job_and_the_mock_scheduler_keep_the_single_job_path():
    backend, calls = sge()
    assert backend.statuses(["101"])["101"].state == "running"
    assert [c[0] for c in calls] == ["qstat"]
    mock = Scheduler(HpcSettings(scheduler="mock"))
    assert mock.statuses(["mock-1", "mock-2"]) == {"mock-1": JobInfo(job_id="mock-1", state="completed", exit_status=0),
                                                   "mock-2": JobInfo(job_id="mock-2", state="completed", exit_status=0)}


# ---- Slurm --------------------------------------------------------------------------------------------------------

def slurm(squeue, sacct):
    return fake("slurm", {"squeue": squeue, "sacct": sacct})


def test_slurm_asks_squeue_once_and_sacct_once_for_all_the_jobs_squeue_no_longer_holds():
    def squeue(argv):
        assert argv[argv.index("-j") + 1] == "11,12,13,14"
        return done(argv, "11|RUNNING|None|a\n12|PENDING|Priority|b\n13|COMPLETED|None|c\n")

    def sacct(argv):
        assert argv[argv.index("-j") + 1] == "13,14"
        return done(argv, "13|COMPLETED|0:0|c\n14|FAILED|1:0|d\n")

    backend, calls = slurm(squeue, sacct)
    results = backend.statuses(["11", "12", "13", "14"])
    assert [c[0] for c in calls] == ["squeue", "sacct"]
    assert as_state(results) == {"11": ("running", None), "12": ("queued", None), "13": ("completed", 0),
                                 "14": ("failed", 1)}


def test_slurm_batch_equals_one_job_at_a_time():
    squeue_out = "11|RUNNING|None|a\n13|COMPLETED|None|c\n"
    sacct_out = "13|COMPLETED|0:0|c\n14|FAILED|1:0|d\n"

    def squeue(argv):
        ids = argv[argv.index("-j") + 1].split(",")
        return done(argv, "".join(line + "\n" for line in squeue_out.splitlines() if line.split("|")[0] in ids))

    def sacct(argv):
        ids = argv[argv.index("-j") + 1].split(",")
        return done(argv, "".join(line + "\n" for line in sacct_out.splitlines() if line.split("|")[0] in ids))

    batch, batch_calls = slurm(squeue, sacct)
    single, single_calls = slurm(squeue, sacct)
    ids = ["11", "13", "14"]
    assert as_state(batch.statuses(ids)) == as_state({i: single.status(i) for i in ids})
    assert len(batch_calls) == 2 and len(single_calls) == 5


def test_a_slurm_batch_that_squeue_refuses_falls_back_to_one_job_at_a_time():
    def squeue(argv):
        ids = argv[argv.index("-j") + 1]
        if "," in ids:
            return done(argv, stderr="slurm_load_jobs error: Invalid job id specified", rc=1)
        return done(argv, f"{ids}|RUNNING|None|x\n")

    backend, calls = slurm(squeue, lambda argv: done(argv))
    results = backend.statuses(["21", "22"])
    assert as_state(results) == {"21": ("running", None), "22": ("running", None)}
    assert [c[0] for c in calls] == ["squeue", "squeue", "squeue"]  # the batch, then each job alone


def test_slurm_accounting_disabled_keeps_squeues_final_state_or_missing():
    backend, _ = slurm(lambda argv: done(argv, "31|COMPLETED|None|a\n"),
                       lambda argv: done(argv, stderr="slurm_persist_conn_open: accounting storage is disabled", rc=1))
    results = backend.statuses(["31", "32"])
    assert results["31"].state == "completed" and results["32"].state == "missing"


# ---- PBS ----------------------------------------------------------------------------------------------------------

PBS = ("Job Id: 41.server\n    Job_Name = a\n    job_state = R\n\n"
       "Job Id: 42.server\n    Job_Name = b\n    job_state = Q\n")


def test_pbs_asks_for_all_the_jobs_in_one_qstat():
    backend, calls = fake("pbs", {"qstat": lambda argv: done(argv, PBS)})
    results = backend.statuses(["41.server", "42.server"])
    assert [c[:2] for c in calls] == [["qstat", "-f"]] and calls[0][2:] == ["41.server", "42.server"]
    assert as_state(results) == {"41.server": ("running", None), "42.server": ("queued", None)}


def test_pbs_asks_alone_only_for_a_job_the_batch_did_not_list():
    def qstat(argv):
        if len(argv) > 3:
            return done(argv, PBS, rc=153)
        if argv[2] == "43.server":
            return done(argv, stderr="qstat: Unknown Job Id 43.server", rc=153)
        return done(argv, PBS.split("\n\n")[0] if argv[2] == "41.server" else PBS.split("\n\n")[1])

    backend, calls = fake("pbs", {"qstat": qstat})
    results = backend.statuses(["41.server", "43.server"])
    assert results["41.server"].state == "running" and results["43.server"].state == "missing"
    assert len(calls) == 3


# ---- the poll interval --------------------------------------------------------------------------------------------

def test_the_interval_grows_by_the_factor_up_to_the_cap_and_restarts_on_a_change():
    wait, seen = 60.0, []
    for _ in range(6):
        wait = next_poll_interval(wait, 60, 300, 2.0, reset=False)
        seen.append(wait)
    assert seen == [120.0, 240.0, 300.0, 300.0, 300.0, 300.0]
    assert next_poll_interval(300.0, 60, 300, 2.0, reset=True) == 60.0


def test_a_cap_below_the_base_turns_the_backoff_off():
    assert next_poll_interval(60.0, 60, 30, 2.0, reset=False) == 60.0
    assert next_poll_interval(60.0, 60, 0, 2.0, reset=False) == 60.0


def test_the_defaults_cost_about_fifty_polls_over_four_hours_instead_of_240():
    s = Settings().runner
    wait, elapsed, polls = float(s.job_poll_s), 0.0, 0
    while elapsed < 4 * 3600:
        elapsed += wait
        polls += 1
        wait = next_poll_interval(wait, s.job_poll_s, s.job_poll_max_s, s.job_poll_backoff, reset=False)
    assert 45 <= polls <= 55 and 4 * 3600 / s.job_poll_s == 240


# ---- the runner's poll --------------------------------------------------------------------------------------------

def runner_with(jobs, scheduler):
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s = Settings()
    runner.s.hpc.scheduler = "sge"
    runner.scheduler = scheduler
    runner.jobs = jobs
    runner.task_req, runner.notified, runner.tasks = {}, set(), {}
    runner.store = SimpleNamespace(put=lambda *a, **k: None)
    runner.events = []

    async def emit_all(events, after=()):
        runner.events.extend(events)

    async def wake():
        return None

    runner.emit_all, runner._wake_finished_jobs = emit_all, wake
    return runner


def job(state="queued"):
    return {"task_id": "t1", "agent_id": "a", "name": "n", "state": state, "terminal": False, "missing": 0}


def test_the_runner_asks_for_all_its_open_jobs_in_one_call_and_skips_the_ended_ones():
    asked = []

    class Backend:
        def statuses(self, ids):
            asked.append(list(ids))
            return {i: JobInfo(job_id=i, state="running") for i in ids}

    jobs = {"1": job(), "2": job(), "3": {**job("completed"), "terminal": True}}
    runner = runner_with(jobs, Backend())
    assert asyncio.run(runner._poll_jobs()) is True  # new jobs and state changes
    assert asked == [["1", "2"]]
    assert asyncio.run(runner._poll_jobs()) is False  # nothing new: the backoff may grow
    assert asked == [["1", "2"], ["1", "2"]]
    jobs["4"] = job()
    assert asyncio.run(runner._poll_jobs()) is True  # a job nobody had polled restarts the interval


def test_a_missing_job_keeps_the_interval_short_and_an_error_surfaces():
    class Backend:
        def statuses(self, ids):
            return {i: JobInfo(job_id=i, state="missing") for i in ids}

    runner = runner_with({"1": job("running")}, Backend())
    asyncio.run(runner._poll_jobs())
    assert asyncio.run(runner._poll_jobs()) is True  # accounting still catching up

    class Failing:
        def statuses(self, ids):
            return {i: RuntimeError("qstat failed") for i in ids}

    runner = runner_with({"1": job("running")}, Failing())
    with pytest.raises(RuntimeError, match="qstat failed"):
        asyncio.run(runner._poll_jobs())


def watch_intervals(monkeypatch, *, ssh_host, polls):
    """The sleeps `_job_watch_loop` asks for while `_poll_jobs` answers `polls` (changed or not) one after the other."""
    runner = runner_with({}, SimpleNamespace())
    runner.s.hpc.ssh_host = ssh_host
    runner.s.runner.job_poll_s, runner.s.runner.job_poll_max_s = 60, 300
    runner._stopping = False
    sleeps, answers = [], iter(polls)

    async def sleep(seconds):
        sleeps.append(seconds)

    async def poll():
        try:
            return next(answers)
        except StopIteration:
            runner._stopping = True
            return False

    monkeypatch.setattr(asyncio, "sleep", sleep)
    runner._poll_jobs = poll
    asyncio.run(runner._job_watch_loop())
    return sleeps


def test_over_ssh_the_watch_loop_backs_off_and_a_change_restarts_it(monkeypatch):
    sleeps = watch_intervals(monkeypatch, ssh_host="login", polls=[False, False, False, False, True, False])
    assert sleeps[:7] == [60, 120, 240, 300, 300, 60, 120]


def test_without_ssh_the_watch_loop_keeps_the_base_interval(monkeypatch):
    sleeps = watch_intervals(monkeypatch, ssh_host=None, polls=[False, False, False, False])
    assert set(sleeps) == {60}


def test_a_poll_that_raises_is_retried_at_the_base_interval(monkeypatch):
    runner = runner_with({}, SimpleNamespace())
    runner.s.hpc.ssh_host, runner._stopping = "login", False
    runner.s.runner.job_poll_s, runner.s.runner.job_poll_max_s = 60, 300
    sleeps, tries = [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    async def poll():
        tries.append(1)
        if len(tries) < 3:
            return False
        if len(tries) == 3:
            raise RuntimeError("qstat failed")
        runner._stopping = True
        return False

    monkeypatch.setattr(asyncio, "sleep", sleep)
    runner._poll_jobs = poll
    asyncio.run(runner._job_watch_loop())
    assert sleeps == [60, 120, 240, 60]  # the failed third poll sent the next wait back to the base


def test_a_failed_sacct_for_several_ended_jobs_falls_back_to_each_job_alone():
    def squeue(argv):
        ids = argv[argv.index("-j") + 1].split(",")
        return done(argv, "".join(f"{i}|COMPLETED|None|x\n" for i in ids) if len(ids) > 1 else "")

    def sacct(argv):
        ids = argv[argv.index("-j") + 1].split(",")
        if len(ids) > 1:
            return done(argv, stderr="sacct: error: Invalid job id 52", rc=1)  # one id the site rejects fails the call
        return done(argv, f"{ids[0]}|COMPLETED|0:0|x\n") if ids[0] == "51" else done(argv, stderr="boom", rc=1)

    backend, calls = slurm(squeue, sacct)
    results = backend.statuses(["51", "52"])
    assert results["51"].state == "completed"  # not lost with the failing one
    assert isinstance(results["52"], RuntimeError)


def test_an_error_for_one_job_does_not_stop_the_others_from_being_saved():
    class Backend:
        def statuses(self, ids):
            return {"1": RuntimeError("qstat failed"), "2": JobInfo(job_id="2", state="completed", exit_status=0)}

    runner = runner_with({"1": job("running"), "2": job("running")}, Backend())
    with pytest.raises(RuntimeError, match="qstat failed"):
        asyncio.run(runner._poll_jobs())
    assert runner.jobs["2"]["terminal"] is True and runner.jobs["2"]["state"] == "completed"
    assert runner.jobs["1"]["terminal"] is False


def test_with_no_open_job_the_poll_asks_nothing_keeps_the_base_interval_and_still_wakes_finished_tasks():
    woken = []

    class Backend:
        def statuses(self, ids):
            raise AssertionError("nothing is open: nothing to ask")

    runner = runner_with({"1": {**job("completed"), "terminal": True}}, Backend())

    async def wake():
        woken.append(1)

    runner._wake_finished_jobs = wake
    assert asyncio.run(runner._poll_jobs()) is True
    assert woken == [1]
